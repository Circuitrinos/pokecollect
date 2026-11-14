"""
PokéCollect card matcher — identifies cards by artwork.

POST /match   photo (+ mode, rows, cols, page, skip) -> per detected card: corners,
              top candidates (TCGdex ids), and whether the choice is clean
POST /verify  saved scan photo + its card id (+ original, corners) -> how well
              it fits the card's catalog images, and better corners
GET  /health  loading state, index size, mapping and extension progress
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from collections.abc import Callable

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

import json

from . import detect, embedding, foil, frame, learn, matcher, numocr, recrop, stamp, timing
from .catalog import CatalogCard, load_catalog, map_pokemontcg, num_key
from .convert import convert
from .extend import Extender
from .store import Store, load_segment

from . import logsetup  # noqa: E402

# Konsole + Logdateien in /app/logs, wenn eingehängt (matcher.log, matcher-errors.log);
# kein Zugriffsprotokoll — die Kamera-Vorschau fragt /detect mehrmals je Sekunde
logsetup.setup("matcher", access=False)
log = logging.getLogger("matcher")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SEG_DIR = DATA_DIR / "segments"
INDEX_PKL = Path(os.getenv("INDEX_PKL", str(DATA_DIR / "card_index1.pkl")))
DATABASE_URL = os.getenv("DATABASE_URL", "")
EXTEND = os.getenv("EXTEND", "1") == "1"
EXTEND_REGIONS = {r.strip() for r in os.getenv("EXTEND_REGIONS", "west").split(",") if r.strip()}
REFRESH_HOURS = float(os.getenv("REFRESH_HOURS", "24"))
# Extension pass + embedding backfill wait this long after startup (and then
# run at low CPU priority), so the first scans after a reboot don't compete
# with them.
BACKGROUND_DELAY_MIN = float(os.getenv("BACKGROUND_DELAY_MIN", "10"))
WARMUP = os.getenv("WARMUP", "1") == "1"
MAX_BYTES = 12 * 1024 * 1024

_T0 = time.monotonic()
app = FastAPI(title="PokéCollect matcher")


@app.middleware("http")
async def fehler_protokollieren(request: Request, call_next):
    """Unbehandelte Fehler mit Traceback und Pfad, Antworten ab 500 und sehr langsame
    Anfragen nach matcher-errors.log (503 „nicht bereit" während des Aufbaus nicht)."""
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        exc._pokecollect_logged = True
        log.exception("Fehler bei %s %s", request.method, request.url.path)
        raise
    ms = round((time.perf_counter() - t0) * 1000)
    if response.status_code >= 500 and response.status_code != 503:
        log.error("%s %s → %s nach %d ms", request.method, request.url.path, response.status_code, ms)
    elif ms >= _SLOW_MS:
        log.warning("Langsam: %s %s → %s nach %d ms", request.method, request.url.path, response.status_code, ms)
    return response


_SLOW_MS = int(os.environ.get("LOG_SLOW_MS", "30000"))
store = Store()
state: dict = {"status": "loading", "phase": "starting", "error": None, "mapping": None,
               "ready_after_s": None, "warmup_ms": None, "matches": 0}
_match_lock = threading.Lock()   # one photo at a time; each match already uses every core
_detect_lock = threading.Lock()  # live preview: one frame at a time, extra frames are dropped
DETECT_MAX_SIDE = 1280
catalog: dict[str, CatalogCard] = {}
extender = Extender(SEG_DIR, store, EXTEND_REGIONS)
shortlist = embedding.Shortlist()
learner = learn.Learner(store, shortlist, DATABASE_URL)


def _load_catalog_with_retry() -> dict[str, CatalogCard]:
    # After a reboot the database often comes up after us: retry often enough
    # that we don't sit idle long after it's back.
    delay = 3
    while True:
        try:
            return load_catalog(DATABASE_URL)
        except Exception as exc:
            log.warning("Catalog not reachable yet (%s); retrying in %ds", exc, delay)
            time.sleep(delay)
            delay = min(delay * 2, 30)


def _since(t: float) -> str:
    return f"{time.monotonic() - t:.1f}s"


def _startup() -> None:
    global catalog
    try:
        SEG_DIR.mkdir(parents=True, exist_ok=True)
        base = SEG_DIR / "base"
        if not base.with_suffix(".npz").exists() and INDEX_PKL.exists():
            state.update(status="converting", phase="converting")
            convert(INDEX_PKL, base)
        state.update(status="loading", phase="catalog")
        t = time.monotonic()
        catalog = _load_catalog_with_retry()
        log.info("Startup: catalog %d cards (%s)", len(catalog), _since(t))
        state["phase"] = "index"
        t = time.monotonic()
        if base.with_suffix(".npz").exists():
            seg = load_segment(base)
            seg.card_ids, report = map_pokemontcg(seg.meta, catalog)
            state["mapping"] = report.as_dict()
            store.add(seg)
        n_ext = extender.load_existing(
            catalog, progress=lambda i, n: state.update(phase=f"index {i}/{n}"))
        log.info("Startup: index %s (%s)", store.stats(), _since(t))
        state["phase"] = "embeddings"
        t = time.monotonic()
        shortlist.load()
        shortlist.update_coverage(store.known_card_ids())
        log.info("Startup: shortlist %s (%s)", "DINOv2 + pHash" if shortlist.active else "pHash", _since(t))
        learner.load_cached(catalog)
        if WARMUP:
            state["phase"] = "warm-up"
            state["warmup_ms"] = _warm_up()
        state.update(status="ready", phase=None, ready_after_s=round(time.monotonic() - _T0, 1))
        log.info("Ready after %.1fs: %s (+%d from earlier extension runs), shortlist: %s",
                 state["ready_after_s"], store.stats(), n_ext,
                 "DINOv2 + pHash" if shortlist.active else "pHash")
    except Exception as exc:
        log.exception("Startup failed")
        state.update(status="error", phase=None, error=str(exc))
        return
    if learn.LEARN:
        threading.Thread(target=_learn_loop, name="learn", daemon=True).start()
    if not (EXTEND or shortlist.engine is not None):
        return
    _lower_priority()
    if BACKGROUND_DELAY_MIN > 0:
        log.info("Background passes (extension, embedding backfill) start in %g min", BACKGROUND_DELAY_MIN)
        time.sleep(BACKGROUND_DELAY_MIN * 60)
    while True:
        if EXTEND:
            extender.run_once(catalog)
        shortlist.backfill(catalog, store.known_card_ids())   # embed cards that have none yet
        time.sleep(REFRESH_HOURS * 3600)
        catalog = _load_catalog_with_retry()


def _learn_loop() -> None:
    """Index users' confirmed scan photos (first pass a minute after start)."""
    _lower_priority()
    time.sleep(60)
    try:
        learner.sync(catalog)
    except Exception:
        log.exception("Learning from scans failed")
    learner.loop(lambda: catalog)


def _lower_priority() -> None:
    """Nice this thread (and the worker threads it starts) so scans win the CPU."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except (AttributeError, OSError) as exc:
        log.debug("Could not lower background priority: %s", exc)


def _synthetic_photo() -> np.ndarray:
    """A textured, card-shaped rectangle on a plain desk."""
    rng = np.random.default_rng(7)
    card = cv2.resize(rng.integers(0, 256, (48, 34, 3), dtype=np.uint8), (734, 1024),
                      interpolation=cv2.INTER_NEAREST)
    cv2.rectangle(card, (0, 0), (733, 1023), (40, 200, 230), 28)
    photo = np.full((1200, 1000, 3), 70, np.uint8)
    photo[88:88 + 1024, 133:133 + 734] = card
    return photo


def _warm_up() -> int:
    """
    Run every stage once before reporting ready: first calls pay for lazy
    set-up (ONNX Runtime buffers for the 2-image batch, OpenCV/BLAS thread
    pools, Tesseract binary + language data from a cold disk after a reboot).
    """
    t = time.perf_counter()
    timing.start()
    try:
        photo = _synthetic_photo()
        dets = _detect(photo, max_cards=5, nested=True)
        quad = dets[0].quad if dets else detect.whole_image_as_card(photo)
        _match(detect.warp(photo, quad), store.snapshot(), 5)
        with timing.phase("ocr"):
            numocr.warm_up()
    except Exception:
        log.exception("Warm-up failed (ignored)")
    ms = round((time.perf_counter() - t) * 1000)
    log.info("Startup: warm-up %d ms %s", ms, timing.take())
    return ms


@app.on_event("startup")
def _on_startup() -> None:
    threading.Thread(target=_startup, name="startup", daemon=True).start()


@app.get("/health")
def health() -> dict:
    return {**state, "uptime_s": round(time.monotonic() - _T0), "store": store.stats(),
            "extension": extender.status, "embedding": shortlist.status, "learned": learner.status,
            "ocr": numocr.available(), "catalog": len(catalog)}


@app.post("/learn")
def learn_now() -> dict:
    """The api saved a scan photo: index it soon (debounced)."""
    learner.poke()
    return {"ok": True, "enabled": learn.LEARN}


# ── matching ────────────────────────────────────────────────────────────────

def _card_info(card_id: str) -> dict:
    c = catalog.get(card_id)
    if c is None:
        return {"card_id": card_id}
    return {"card_id": card_id, "name": c.name_en or c.name, "set_name": c.set_name_en or c.set_name,
            "set_id": c.set_id, "local_id": c.local_id, "image_url": c.image_url}


def _decide(card: np.ndarray, cands: list[matcher.Candidate], quad: np.ndarray) -> tuple[matcher.Decision, np.ndarray]:
    upside = bool(cands and cands[0].upside_down)
    wanted = {num_key(catalog[c.card_id].local_id) for c in cands if c.card_id in catalog}
    def read_numbers():
        with timing.phase("ocr"):
            return numocr.read_numbers(card, upside_down=upside, wanted=wanted)

    decision = matcher.decide(cands, catalog, read_numbers)
    q = detect.portrait_order(quad)
    if decision.candidates and decision.candidates[0].upside_down:
        q = np.roll(q, 2, axis=0)  # report corners in the card's real orientation
    return decision, q


def _match(card: np.ndarray, snap, top_k: int) -> list[matcher.Candidate]:
    with timing.phase("embed"):
        sl = shortlist.shortlist(card)
    with timing.phase("match"):
        return matcher.match_card(card, snap, top_k=top_k, shortlist=sl)


def _detect(img: np.ndarray, **kw) -> list:
    with timing.phase("detect"):
        return detect.detect_cards(img, **kw)


def _identify(img: np.ndarray, quad: np.ndarray, top_k: int) -> tuple[matcher.Decision, np.ndarray]:
    card = detect.warp(img, quad)
    return _decide(card, _match(card, store.snapshot(), top_k), quad)


def _best_of(img: np.ndarray, quads: list[np.ndarray], top_k: int,
             snap_outline: bool = False) -> tuple[matcher.Decision, np.ndarray]:
    """
    Match every outline hypothesis (cheap), keep the one with the strongest art
    match, and only then run the decision (which may need slow OCR) on it.
    snap_outline: also when the art match is already clear, put the corners on
    the matched card's true outline (a pocket-sized guess becomes the card).
    """
    snap = store.snapshot()
    best = None
    for q in quads:
        card = detect.warp(img, q)
        cands = _match(card, snap, top_k)
        key = (cands[0].geo_ok, cands[0].art_inliers, cands[0].inliers) if cands else (False, -1, -1)
        if best is None or key > best[0]:
            best = (key, card, cands, q)
        if matcher.art_clear(cands):
            break
    _, card, cands, q = best
    card, cands, q = _refine(img, card, cands, q, snap, top_k)
    if snap_outline and cands and cands[0].geo_ok and not cands[0].upside_down:
        inner = matcher.card_outline_in_query(cands[0], (card.shape[1], card.shape[0]))
        if inner is not None:
            q2 = cv2.perspectiveTransform(inner.reshape(-1, 1, 2),
                                          np.linalg.inv(detect.warp_matrix(q))).reshape(4, 2)
            if detect.plausible_quad(q2, img.shape) and _overlap(q2, np.float32(q)) > 0.6:
                card, q = detect.warp(img, q2), q2
    return _decide(card, cands, q)


def _refine(img, card, cands, q, snap, top_k):
    """
    Map the best match's full card outline back into the photo (inverse
    homography) and match again. Fixes partial or sloppy outlines and gives a
    tighter crop. Up to two rounds; keeps whichever round matched best.
    """
    for _ in range(2):
        if not cands or cands[0].inliers < matcher.MIN_GOOD or matcher.art_clear(cands):
            break
        inner = matcher.card_outline_in_query(cands[0], (card.shape[1], card.shape[0]))
        if inner is None:
            break
        q2 = cv2.perspectiveTransform(inner.reshape(-1, 1, 2),
                                      np.linalg.inv(detect.warp_matrix(q))).reshape(4, 2)
        if not detect.plausible_quad(q2, img.shape):
            break
        card2 = detect.warp(img, q2)
        cands2 = _match(card2, snap, top_k)
        if not cands2 or (cands2[0].geo_ok, cands2[0].art_inliers, cands2[0].inliers) <= \
                (cands[0].geo_ok, cands[0].art_inliers, cands[0].inliers):
            break
        card, cands, q = card2, cands2, q2
    return card, cands, q


def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    """Intersection over the smaller quad (1.0 when one contains the other)."""
    inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
    return float(inter) / max(1e-6, min(cv2.contourArea(a.astype(np.float32)),
                                         cv2.contourArea(b.astype(np.float32))))


def _stamp_on(img: np.ndarray, quad: np.ndarray) -> dict | None:
    """
    Play! stamp on this card (the learned template, stamp.py), checked on the
    upright card as the app crops it. None without a template or a match.
    """
    if stamp.load_template() is None:
        return None
    h, w = img.shape[:2]
    try:
        with timing.phase("stamp"):
            r = stamp.find(recrop.warp_upright(img, np.float32(quad) / [w, h], stamp.H))
    except cv2.error:
        return None
    if r is None:
        return None
    return {**r, "ok": r["score"] >= stamp.MIN_SCORE, "sure": r["score"] >= stamp.SURE_SCORE}


def _result(d: matcher.Decision, quad: np.ndarray, img: np.ndarray, position: int | None) -> dict:
    h, w = img.shape[:2]
    return {
        "position": position,
        "quad": [[round(float(x) / w, 4), round(float(y) / h, 4)] for x, y in quad],
        "auto": d.auto,
        "reason": d.reason,
        "confidence": d.confidence,
        "number_read": d.number_read,
        "candidates": [{**_card_info(c.card_id), "score": round(c.score, 1), "inliers": c.inliers,
                        "art_inliers": c.art_inliers,
                        "good_matches": c.good, "phash_distance": c.phash_dist} for c in d.candidates],
        # Prize Pack: Play!-Stempel auf der erkannten Karte (nur wenn sie erkannt ist)
        "stamp": _stamp_on(img, quad) if d.candidates else None,
    }


@app.post("/detect")
async def detect_preview(file: UploadFile = File(...)) -> dict:
    """
    Live preview for the scan camera: card outlines in a (small) frame, no
    matching. Same detector as /match, so the boxes show what a capture of
    this frame would work with. Busy → 429 (the client just sends the next frame).
    """
    if state["status"] != "ready":
        raise HTTPException(503, f"matcher not ready: {state['status']}")
    data = await file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "empty or oversized image")
    return await run_in_threadpool(_detect_request, data)


def _preview_quads(dets: list, max_err: float = 1.6) -> list[np.ndarray]:
    """
    The detector proposes many outlines (card, art frame, half a card, two cards
    merged, a whole binder page); /match sorts them out by matching. Without
    matching, keep the card-shaped ones (63:88, square corners) and resolve the
    look-alikes: half a card and two cards side by side are card-shaped too
    (88/2 : 63 ≈ 63 : 88), and so is a binder page.
    """
    c = [d for d in dets if d.area_frac >= 0.004 and detect._rectangularity(d.quad) <= max_err]

    def inside(a, b):  # a lies within b and is clearly smaller
        return a is not b and _overlap(a.quad, b.quad) > 0.85 and a.area_frac < 0.75 * b.area_frac

    holds = {id(b): [a for a in c if inside(a, b)] for b in c}
    c = [b for b in c if len(holds[id(b)]) < 3]                      # binder page / block of cards
    drop: set[int] = set()
    for b in c:
        parts = [a for a in holds[id(b)] if 0.3 <= a.area_frac / b.area_frac <= 0.7]
        if len(parts) != 2:
            continue
        # card + its two halves, or two cards + the outline around both? Ask the
        # other cards in the frame which size a card has here.
        others = [o.area_frac for o in c if _overlap(o.quad, b.quad) < 0.1 and not holds[id(o)]]
        half = sum(a.area_frac for a in parts) / 2
        if others and abs(np.log(half / np.median(others))) < abs(np.log(b.area_frac / np.median(others))):
            drop.add(id(b))                                           # two cards
        else:
            drop.update(id(a) for a in parts)                         # one card
    kept: list[tuple[np.ndarray, float]] = []
    for d in sorted((d for d in c if id(d) not in drop), key=lambda d: -d.area_frac):
        if all(_overlap(d.quad, k[0]) < 0.3 for k in kept):
            kept.append((d.quad, d.area_frac))
    # cards in one photo have similar sizes: a box far smaller than the largest is a detail
    biggest = max((a for _, a in kept), default=0.0)
    return [q for q, a in kept if a >= 0.45 * biggest]


def _detect_request(data: bytes) -> dict:
    if not _detect_lock.acquire(blocking=False):
        raise HTTPException(429, "busy")
    try:
        t0 = time.perf_counter()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "unreadable image")
        h, w = img.shape[:2]
        k = min(1.0, DETECT_MAX_SIDE / max(h, w))
        if k < 1.0:
            img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
            h, w = img.shape[:2]
        quads = _preview_quads(detect.detect_cards(img, max_cards=24, nested=True))
        cards = [{"quad": [[round(float(x) / w, 4), round(float(y) / h, 4)] for x, y in q]} for q in quads]
        return {"cards": cards, "ms": round((time.perf_counter() - t0) * 1000)}
    finally:
        _detect_lock.release()


@app.post("/outline")
async def outline(file: UploadFile = File(...), quad: str = Form(""), refs: str = Form("")) -> dict:
    """
    Snap rough corners to the card's real edges (the app's crop editors: a
    picture pasted from an image search, a card the scan missed, adjusted
    corners). With refs (the recognised card and/or other prints of the same
    name) the card is found by its artwork and settled like "Fix crop";
    otherwise, or when no print shares the artwork, by the typical card frame
    and the card-shaped outlines the detector finds near the rough corners.
    → {"quad": [[x, y]…] (fractions, card's TL first) or null,
       "method": "reference" | "frame" | "edges", "frame_ok", "reference", …}.
    Works before the catalog is loaded too, just without the artwork stage.
    """
    data = await file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "empty or oversized image")
    ref_ids = [r.strip() for r in refs.split(",") if r.strip()][:24] if state["status"] == "ready" else []
    return await run_in_threadpool(_outline_request, data, quad, ref_ids)


OUTLINE_MIN_IOU = 0.45


def _plain_margin(img: np.ndarray, quad: np.ndarray) -> bool:
    """Is everything outside the outline plain background (near white or black, not colourful)?"""
    mask = np.full(img.shape[:2], 255, np.uint8)
    cv2.fillConvexPoly(mask, quad.astype(np.int32), 0)
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
    if mask.sum() == 0:
        return True
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    sat = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)[:, :, 1]
    light = float(lab[:, :, 0][mask > 0].mean())
    return (light > 225 or light < 30) and float(sat[mask > 0].mean()) < 40


def _outline_by_reference(img: np.ndarray, rough: np.ndarray, ref_ids: list[str]) -> dict | None:
    """Card found by its artwork in a same-name print, then settled with /verify's refine."""
    h, w = img.shape[:2]
    roi = None
    x, y, bw, bh = cv2.boundingRect(rough.astype(np.int32))
    if bw * bh < 0.9 * w * h:      # real hint: only look around it (screenshots show several cards)
        mx, my = int(0.25 * bw), int(0.25 * bh)
        roi = (max(0, x - mx), max(0, y - my), min(w, x + bw + mx), min(h, y + bh + my))
    snap = store.snapshot()
    with _match_lock:
        loc = matcher.locate_card(img, snap, ref_ids, roi)
    if loc is None or not detect.plausible_quad(loc["quad"], img.shape):
        return None
    q = np.clip(loc["quad"] / [w, h], 0.0, 1.0)
    cid = loc["card_id"]

    def again(crop):
        with _match_lock:
            return matcher.verify_card(crop, snap, cid)
    crop = recrop.warp_upright(img, q, 880)
    first = again(crop)
    if first.get("outline") and first.get("geo_ok"):
        start = recrop.outline_to_original(img, q, (crop.shape[1], crop.shape[0]), first["outline"])
        q = np.float32(recrop.refine(img, crop, first, start.tolist(), again)["suggested_quad"])
    return {"quad": np.round(q, 4).tolist(), "method": "reference", "reference": cid,
            "inliers": int(loc["inliers"]), "iou": None}


def _upright(quad: np.ndarray) -> np.ndarray:
    """Corners the way the app crops them: an outline lying sideways starts one corner on (cards are portrait)."""
    q = np.float32(quad)
    if np.linalg.norm(q[1] - q[0]) > np.linalg.norm(q[3] - q[0]):
        q = np.roll(q, -1, axis=0)
    return q


def _oriented_like(quad: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """
    `quad`'s corners re-ordered (turned and/or mirrored) to lie closest to `ref`'s:
    the editor's rough corners carry the orientation the user sees (card's TL
    first, also when the photo shows it sideways or upside down), and an edge
    outline only knows where its corners are.
    """
    q, r = np.float32(quad), np.float32(ref)
    options = [np.roll(o, -k, axis=0) for o in (q, q[::-1]) for k in range(4)]
    return min(options, key=lambda o: float(np.linalg.norm(o - r, axis=1).sum()))


FRAME_SEEDS = 4
FRAME_EDGE_MIN = 0.25       # an edge outline that scores this much as a card frame is the card
FRAME_EXACT_RECT = 0.03     # rectangularity of an outline that is exactly card-shaped
FRAME_TIE = 0.04            # outlines scoring within this of the best count as the same
FRAME_AGREE = 0.025         # of the card width: an edge outline this close to the fitted frame
FRAME_EDGE_SLACK = 0.06     # the fitted frame must look this much more like a card to beat the edges


def _outline_by_frame(img: np.ndarray, rough: np.ndarray, hint: bool, cands: list[np.ndarray]) -> dict | None:
    """
    "A Pokémon card" rather than "this card", with the mean card frame (frame.py):

    - every card-shaped edge outline (near the hint) is scored for how much it
      looks like a card frame; edges give the exact corners, the frame says
      which outline is the card and not its sleeve, pocket or art box. An
      exactly card-shaped outline counts too (full-art cards hide the frame)
    - when none looks like a card, the frame itself is fitted from the hint
      (or, without one, from the biggest outlines and the whole picture) and
      its sides snapped onto their edges
    """
    if not frame.available():
        return None
    h, w = img.shape[:2]
    rough = np.float32(rough)
    area = lambda c: float(cv2.contourArea(np.float32(c)))   # noqa: E731
    # with a hint, the outlines are turned the way the user holds the card: the frame
    # is scored that way up (a sideways or upside-down card scores as one)
    orient = (lambda c: _oriented_like(detect.portrait_order(c), rough)) if hint else detect.portrait_order  # noqa: E731
    shaped = [orient(c) for c in cands if detect._rectangularity(c) <= 2.5 and area(c) >= 0.02 * w * h]
    if hint:
        shaped = [c for c in shaped if detect._iou(rough, c) >= OUTLINE_MIN_IOU]
    scored = sorted(((frame.score(img, c), c) for c in shaped), key=lambda x: -x[0])

    def exact(group):
        # outlines a pixel apart score alike: keep the one the edge picker prefers
        if hint:
            return max(group, key=lambda x: detect._iou(rough, x[1]) - 0.05 * detect._rectangularity(x[1]))
        return max(group, key=lambda x: area(x[1]) * float(np.exp(-detect._rectangularity(x[1]))))

    # a card frame, or an exact 63:88 rectangle (screenshots, store images; full-art
    # cards whose artwork hides the frame) — photos never give one of those by chance
    trusted = [x for x in scored if x[0] >= FRAME_EDGE_MIN or detect._rectangularity(x[1]) <= FRAME_EXACT_RECT]
    if trusted:
        top = trusted[0]
        sc, q = exact([x for x in trusted if x[0] >= top[0] - FRAME_TIE and detect._iou(top[1], x[1]) >= 0.85])
        method, confirmed = "edges", True
    else:
        if hint:
            seeds = [rough]
        else:
            seeds = [c for _, c in sorted(((area(c), c) for c in shaped), key=lambda x: -x[0])[:FRAME_SEEDS]]
            if 0.62 <= w / h <= 0.82:
                seeds.append(detect.whole_image_as_card(img))
        fitted = None
        for seed in seeds:
            r = frame.fit(img, seed)
            if r is None or r["score"] < frame.MIN_SCORE:
                continue
            fq = frame.snap_edges(img, r["quad"])
            if not detect.plausible_quad(fq, img.shape) or (hint and detect._iou(rough, fq) < OUTLINE_MIN_IOU):
                continue
            if fitted is None or r["score"] > fitted[0]:
                fitted = (r["score"], fq)
        if fitted is None:
            return None
        sc, q, method, confirmed = fitted[0], fitted[1], "frame", False
        # an edge outline where the frame landed has the exact corners
        side = float(np.linalg.norm(q[1] - q[0]))
        near = [x for x in scored if float(np.abs(_oriented_like(x[1], q) - q).max()) < FRAME_AGREE * side]
        if near:
            sc, q = exact(near)
            method, confirmed = "edges", True
        elif scored and scored[0][0] >= frame.score(img, q) - FRAME_EDGE_SLACK:
            # they disagree and the edge outline looks about as much like a card
            sc, q = exact([x for x in scored if x[0] >= scored[0][0] - FRAME_TIE and detect._iou(scored[0][1], x[1]) >= 0.85])
            method = "edges"
    out = np.clip(q / [w, h], 0.0, 1.0)
    return {"quad": np.round(out, 4).tolist(), "method": method, "score": sc, "frame_ok": confirmed,
            "iou": round(float(detect._iou(rough, q)), 3) if hint else None, "candidates": len(cands)}


def _outline_request(data: bytes, quad: str, ref_ids: list[str] | None = None) -> dict:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "unreadable image")
    h, w = img.shape[:2]
    k = min(1.0, 1600 / max(h, w))
    if k < 1.0:
        img = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    try:
        q = json.loads(quad) if quad else None
        rough = _upright(np.float32(q) * [w, h]) if isinstance(q, list) and len(q) == 4 else None
    except (ValueError, TypeError):
        rough = None
    if rough is None:
        rough = detect.whole_image_as_card(img)
    area_of = lambda c: float(cv2.contourArea(np.float32(c)))   # noqa: E731
    if ref_ids and state["status"] == "ready":
        found = _outline_by_reference(img, rough, ref_ids)
        if found is not None:
            return found
    cands = [d.quad for d in detect.detect_cards(img, max_cards=24, nested=True)]
    # also look closely around the rough corners (a small card in a big screenshot)
    x, y, bw, bh = cv2.boundingRect(rough.astype(np.int32))
    mx, my = int(0.2 * bw), int(0.2 * bh)
    x0, y0, x1, y1 = max(0, x - mx), max(0, y - my), min(w, x + bw + mx), min(h, y + bh + my)
    if (x1 - x0) * (y1 - y0) < 0.8 * w * h and x1 - x0 > 40 and y1 - y0 > 40:
        cands += [d.quad + np.float32([x0, y0]) for d in detect.detect_cards(img[y0:y1, x0:x1], max_cards=12, nested=True)]
    found = _outline_by_frame(img, rough, area_of(rough) <= 0.9 * w * h, cands)
    if found is not None:
        return found

    area = lambda c: float(cv2.contourArea(c.astype(np.float32)))   # noqa: E731
    card_shaped = [c for c in cands if detect._rectangularity(c) <= 2.5]
    card_image = 0.62 <= w / h <= 0.82
    if area(rough) > 0.9 * w * h:
        # no real hint (corners on the image border): the most card-like big outline
        pool = [c for c in card_shaped if area(c) >= 0.05 * w * h]
        best = max(pool, key=lambda c: area(c) * float(np.exp(-detect._rectangularity(c)))) if pool else None
        # a big outline inside a card-shaped picture is the card's own border
        # frame unless what lies around it is plain background (white/black)
        if best is not None and card_image and area(best) > 0.6 * w * h and not _plain_margin(img, best):
            best = None
    else:
        good = [c for c in card_shaped if detect._iou(rough, c) >= OUTLINE_MIN_IOU]
        best = max(good, key=lambda c: detect._iou(rough, c) - 0.05 * detect._rectangularity(c)) if good else None
    if best is None and card_image and area(rough) > 0.9 * w * h:
        best = detect.whole_image_as_card(img)      # the picture is the card itself
    if best is None:
        return {"quad": None, "method": "edges", "iou": 0.0, "candidates": len(cands)}
    best = detect.portrait_order(best)
    if area(rough) <= 0.9 * w * h:
        best = _oriented_like(best, rough)          # the way the user holds the card
    out = (np.clip(best / [w, h], 0.0, 1.0)).round(4).tolist()
    return {"quad": out, "method": "edges", "iou": round(float(detect._iou(rough, best)), 3),
            "candidates": len(cands)}


STAMP_MAX_SAMPLES = 300


@app.post("/stamp")
async def find_stamp(file: UploadFile = File(...), quad: str = Form("")) -> dict:
    """Play! stamp in a straightened card photo (around `quad`, card fractions, if given)."""
    data = await file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "empty or oversized image")
    try:
        hint = json.loads(quad) if quad else None
        hint = hint if isinstance(hint, list) and len(hint) == 4 else None
    except ValueError:
        hint = None
    return await run_in_threadpool(_stamp_request, data, hint)


def _stamp_request(data: bytes, hint: list | None) -> dict:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "unreadable image")
    if stamp.load_template() is None:
        return {"quad": None, "score": None, "ok": False, "template": False}
    r = stamp.find(img, hint)
    if r is None:
        return {"quad": None, "score": None, "ok": False, "template": True}
    return {**r, "ok": r["score"] >= stamp.MIN_SCORE, "sure": r["score"] >= stamp.SURE_SCORE, "template": True}


@app.post("/foil")
async def foil_check(file: UploadFile = File(...), ref: UploadFile = File(...)) -> dict:
    """
    Reverse holo? A straightened card photo against the card's plain catalog
    image (app/foil.py). Without a model (tools/foil_eval.py --save, then
    PUT /foil/model) only {"model": false}.
    """
    photo, reference = await file.read(), await ref.read()
    if not photo or not reference or len(photo) > MAX_BYTES or len(reference) > MAX_BYTES:
        raise HTTPException(400, "empty or oversized image")
    return await run_in_threadpool(_foil_request, photo, reference)


def _foil_request(photo: bytes, reference: bytes) -> dict:
    model = foil.load_model()
    if model is None:
        return {"model": False}
    img, ref = foil.decode(photo), foil.decode(reference)
    if img is None or ref is None:
        raise HTTPException(400, "unreadable image")
    with _match_lock, timing.phase("foil"):
        m = foil.measure(img, ref)
    if m is None:
        return {"model": True, "aligned": False}
    return {"model": True, "aligned": True, **m,
            **foil.judge(model, m["art"], m["frame"], m.get("code_corr"), m.get("box_corr"))}


@app.get("/foil/model")
def foil_model() -> dict:
    m = foil.load_model()
    if m is None:
        return {"exists": False}
    return {"exists": True, **{k: m.get(k) for k in ("precision", "recall", "threshold", "trained_on", "saved_at",
                                                     "features")}}


@app.put("/foil/model")
async def put_foil_model(file: UploadFile = File(...)) -> dict:
    """Model file from tools/foil_eval.py --save (the app's admin page uploads it)."""
    try:
        m = json.loads((await file.read()).decode("utf-8"))
        m["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        foil.save_model(m)
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise HTTPException(422, f"not a foil model: {exc}") from None
    return foil_model()


@app.delete("/foil/model")
def delete_foil_model() -> dict:
    try:
        foil.MODEL_PATH.unlink()
    except FileNotFoundError:
        pass
    foil._cache.clear()
    return {"exists": False}


@app.get("/stamp/template")
def stamp_template() -> dict:
    tpl = stamp.load_template()
    if tpl is None:
        return {"exists": False}
    meta = tpl[1]
    return {"exists": True, "samples": meta.get("samples"), "marked": meta.get("marked"),
            "w": meta.get("w"), "h": meta.get("h"), "built_at": meta.get("built_at")}


@app.post("/stamp/learn")
async def learn_stamp(photos: list[UploadFile] = File(...), refs: list[UploadFile] = File(default=[]),
                      samples: str = Form(...)) -> dict:
    """
    Learn the stamp template from the collection: `samples` = JSON list of
    {"photo": index into photos, "box": [x0, y0, x1, y1] (hand-marked) or null,
    "ref": index into refs (the unstamped catalog image) or null}.
    """
    try:
        spec = json.loads(samples)
    except ValueError:
        raise HTTPException(400, "samples: invalid JSON") from None
    if not isinstance(spec, list) or len(spec) > STAMP_MAX_SAMPLES:
        raise HTTPException(400, "samples: list of at most %d" % STAMP_MAX_SAMPLES)
    photo_data = [await f.read() for f in photos]
    ref_data = [await f.read() for f in refs]
    return await run_in_threadpool(_learn_stamp, spec, photo_data, ref_data)


def _learn_stamp(spec: list, photo_data: list[bytes], ref_data: list[bytes]) -> dict:
    def decode(blobs, i, flags=cv2.IMREAD_COLOR):
        if not isinstance(i, int) or not 0 <= i < len(blobs) or len(blobs[i]) > MAX_BYTES:
            return None
        return cv2.imdecode(np.frombuffer(blobs[i], np.uint8), flags)
    use, marked, guessed = [], 0, 0
    for s in spec:
        if not isinstance(s, dict):
            continue
        card = decode(photo_data, s.get("photo"))
        if card is None:
            continue
        box = s.get("box")
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box) \
                and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1:
            use.append((card, tuple(float(v) for v in box), True))
            marked += 1
            continue
        ref = decode(ref_data, s.get("ref"), cv2.IMREAD_UNCHANGED)
        if ref is None:
            continue
        b, strength = stamp.difference_box(card, ref)
        if strength >= 1.8:          # a clear difference where stamps sit
            use.append((card, b, False))
            guessed += 1
    if len(use) < 3:
        raise HTTPException(422, f"too few stamps to learn from ({len(use)}; at least 3)")
    built = stamp.build(use)
    if built is None:
        raise HTTPException(422, "no template")
    tpl, meta = built
    meta["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stamp.save(tpl, meta)
    log.info("stamp template: %d samples (%d marked, %d from catalog differences)", meta["samples"], marked, guessed)
    return {"samples": meta["samples"], "marked": marked, "guessed": guessed, "w": meta["w"], "h": meta["h"],
            "built_at": meta["built_at"]}


@app.post("/match")
async def match(file: UploadFile = File(...), mode: str = Form("single"),
                rows: int = Form(0), cols: int = Form(0), top_k: int = Form(5), page: str = Form(""),
                skip: str = Form("")) -> dict:
    """
    page (binder and multi mode, optional): the grid's four corners in the
    photo, JSON [[x, y], …] as fractions of width/height, top left first,
    clockwise — the app's tilted grid when the photo was taken at an angle
    (against glare). The photo is straightened by it first: a binder page is
    then split into pockets (rows × cols), loose cards are found anywhere.
    skip (binder mode, optional): pockets to leave out, "0,4,5" — the live
    scan already knows them, so a frame only spends time on the rest.
    """
    if state["status"] != "ready":
        raise HTTPException(503, f"matcher not ready: {state['status']}")
    data = await file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "empty or oversized image")
    corners = _page_corners(page) if mode in ("binder", "multi") else None
    skipped = _skip_pockets(skip, rows * cols) if mode == "binder" else frozenset()
    # The work is CPU-bound: run it in a worker thread so /health keeps
    # answering (the backend's status check has a 2 s timeout).
    return await run_in_threadpool(_match_request, data, mode, rows, cols, max(1, min(top_k, 10)), corners, skipped)


def _skip_pockets(skip: str, pockets: int) -> frozenset[int]:
    """Pocket numbers from the form ("0,4,5"); anything else is ignored."""
    out = set()
    for part in skip.split(","):
        part = part.strip()
        if part.isdigit() and int(part) < pockets:
            out.add(int(part))
    return frozenset(out)


def _page_corners(page: str) -> np.ndarray | None:
    """The binder page's corners (fractions) from the form, or None if absent or not a sane quadrilateral."""
    if not page:
        return None
    try:
        q = np.float32(json.loads(page))
    except (ValueError, TypeError):
        raise HTTPException(400, "page: four [x, y] corners expected")
    if q.shape != (4, 2) or not np.all(np.isfinite(q)) or np.abs(q).max() > 3:
        raise HTTPException(400, "page: four [x, y] corners expected")
    # convex, clockwise in image coordinates (y down), not degenerate
    def turn(a: np.ndarray, b: np.ndarray) -> float:
        return float(a[0] * b[1] - a[1] * b[0])
    cross = [turn(q[(i + 1) % 4] - q[i], q[(i + 2) % 4] - q[(i + 1) % 4]) for i in range(4)]
    if not (all(c > 0 for c in cross) and cv2.contourArea(q) > 0.05):
        return None
    return q


def _match_request(data: bytes, mode: str, rows: int, cols: int, top_k: int, page: np.ndarray | None = None,
                   skip: frozenset[int] = frozenset()) -> dict:
    t0 = time.perf_counter()
    with _match_lock:
        queued = time.perf_counter() - t0
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "unreadable image")
        res = _run_match(img, mode, rows, cols, top_k, page, skip)
        state["matches"] += 1
    if queued >= 0.05:
        res["timing"]["queued"] = round(queued * 1000)
    log.info("match %s %dx%d: %d card(s) in %d ms %s%s%s", mode, img.shape[1], img.shape[0],
             len(res["cards"]), res["ms"], res["timing"],
             f" ({len(skip)} pocket(s) skipped)" if skip else "",
             " (first since start)" if state["matches"] == 1 else "")
    return res


@app.post("/verify")
async def verify(file: UploadFile = File(...), card_id: str = Form(...),
                 original: UploadFile | None = File(None), quad: str = Form("")) -> dict:
    """Scan quality check (admin tool in the app): the saved crop against its
    card's catalog images only; with the original and the corners also a
    corrected set of corners."""
    if state["status"] != "ready":
        raise HTTPException(503, f"matcher not ready: {state['status']}")
    data = await file.read()
    orig = await original.read() if original is not None else b""
    if not data or len(data) > MAX_BYTES or len(orig) > 4 * MAX_BYTES:
        raise HTTPException(400, "empty or oversized image")
    return await run_in_threadpool(_verify_request, data, card_id, orig, quad)


def _verify_request(data: bytes, card_id: str, orig: bytes, quad: str) -> dict:
    crop = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if crop is None:
        raise HTTPException(400, "unreadable image")
    snap = store.snapshot()
    with _match_lock:
        res = matcher.verify_card(crop, snap, card_id)
    res["suggested_quad"] = None
    if res.get("outline") and orig and quad:
        try:
            q = json.loads(quad)
            original = cv2.imdecode(np.frombuffer(orig, np.uint8), cv2.IMREAD_COLOR)
            if original is not None and isinstance(q, list) and len(q) == 4:
                res.update(recrop.suggest_quad(original, crop, q, res["outline"]))
                if res.get("suggested_quad"):
                    def again(img):
                        with _match_lock:
                            return matcher.verify_card(img, snap, card_id)
                    res.update(recrop.refine(original, crop, res, res["suggested_quad"], again))
        except (ValueError, cv2.error) as exc:
            log.info("verify %s: no corner suggestion (%s)", card_id, exc)
    return res


PAGE_MARGIN = 0.08      # around the straightened page, of its long side
POCKET_SLACK = 0.12     # each pocket is searched this much wider/taller (the grid is only roughly on the page)
PAGE_MAX_SIDE = 2400    # straightened page, px


def _straighten_page(img: np.ndarray, page: np.ndarray, rows: int, cols: int):
    """
    The binder page (corners as fractions) warped upright: → (page image, its
    page rectangle (x, y, w, h), homography photo → page image), or None.
    The page keeps its grid's shape (cols × 63 by rows × 88), sized like its
    longer edges in the photo.
    """
    h, w = img.shape[:2]
    q = page * np.float32([w, h])
    lens = [float(np.linalg.norm(q[(i + 1) % 4] - q[i])) for i in range(4)]   # top, right, bottom, left
    aspect = (cols * 63.0) / (rows * 88.0)
    pw = max(lens[0], lens[2], max(lens[1], lens[3]) * aspect)
    pw = min(pw, PAGE_MAX_SIDE, PAGE_MAX_SIDE * aspect)
    ph = pw / aspect
    if pw < 60 * cols or ph < 80 * rows:
        return None
    m = round(PAGE_MARGIN * max(pw, ph))
    dst = np.float32([[m, m], [m + pw, m], [m + pw, m + ph], [m, m + ph]])
    H = cv2.getPerspectiveTransform(q.astype(np.float32), dst)
    out = cv2.warpPerspective(img, H, (int(round(pw + 2 * m)), int(round(ph + 2 * m))),
                              flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out, (float(m), float(m), float(pw), float(ph)), H


def _binder_straightened(img: np.ndarray, page: np.ndarray, rows: int, cols: int, top_k: int,
                         skip: frozenset[int] = frozenset()) -> list[dict] | None:
    """
    Binder page shot at an angle: straighten it, find the cards on it (anywhere,
    like loose cards — the grid the app guessed from the phone's tilt is only
    roughly on the page), put each in the pocket under its centre, search the
    pockets still empty one by one, corners back into the photo. Pockets in
    skip are neither matched nor searched.
    """
    with timing.phase("page"):
        st = _straighten_page(img, page, rows, cols)
    if st is None:
        return None
    flat, (px, py, pw, ph), H = st
    Hinv = np.linalg.inv(H)
    fh, fw = flat.shape[:2]
    cw, ch = pw / cols, ph / rows
    found: dict[int, tuple[matcher.Decision, np.ndarray]] = {}

    def pocket(q: np.ndarray) -> int | None:
        cx, cy = np.float32(q).mean(axis=0)
        r, c = int((cy - py) // ch), int((cx - px) // cw)
        return r * cols + c if 0 <= r < rows and 0 <= c < cols else None

    for d, oq in _multi_cards(flat, top_k, leave_out=(lambda q: pocket(q) in skip) if skip else None):
        p = pocket(oq)
        if p is not None and p not in found and p not in skip:
            found[p] = (d, np.float32(oq))
    cards: list[dict] = []
    for r in range(rows):
        for c in range(cols):
            if r * cols + c in skip:
                continue
            if r * cols + c in found:
                d, q = found[r * cols + c]
                back = cv2.perspectiveTransform(q.reshape(-1, 1, 2), Hinv).reshape(-1, 2)
                cards.append(_result(d, back, img, r * cols + c))
                continue
            x0, y0 = px + c * cw, py + r * ch
            ex, ey = POCKET_SLACK * cw, POCKET_SLACK * ch
            X0, Y0 = int(max(0, x0 - ex)), int(max(0, y0 - ey))
            X1, Y1 = int(min(fw, x0 + cw + ex)), int(min(fh, y0 + ch + ey))
            cell = flat[Y0:Y1, X0:X1]
            opts = [d.quad + [X0, Y0] for d in _detect(cell, max_cards=2, nested=True) if d.area_frac > 0.3]
            opts.append(np.float32([[x0, y0], [x0 + cw, y0], [x0 + cw, y0 + ch], [x0, y0 + ch]]))
            d, q = _best_of(flat, opts, top_k, snap_outline=True)
            if d.candidates and d.candidates[0].inliers >= matcher.MIN_GOOD:  # skip empty pockets
                back = cv2.perspectiveTransform(np.float32(q).reshape(-1, 1, 2), Hinv).reshape(-1, 2)
                cards.append(_result(d, back, img, r * cols + c))
    return cards


def _multi_cards(img: np.ndarray, top_k: int,
                 leave_out: Callable[[np.ndarray], bool] | None = None) -> list[tuple[matcher.Decision, np.ndarray]]:
    """
    Loose cards anywhere: match every outline, then keep the strongest matches
    greedily and drop anything overlapping a kept one. Mats, merged pairs and
    partial sub-rectangles match worse than a true card outline, so they lose.
    Outlines for which leave_out() says yes are not matched at all.
    → (decision, corners) in reading order.
    """
    snap = store.snapshot()
    scored = []
    for det in _detect(img, max_cards=24, nested=True):
        if leave_out is not None and leave_out(det.quad):
            continue
        card = detect.warp(img, det.quad)
        cands = _match(card, snap, top_k)
        if not cands or cands[0].inliers < matcher.MIN_GOOD:  # not a card outline
            continue
        q = det.quad
        if not cands[0].geo_ok:
            # sloppy or merged outline: let the homography find the real card
            card, cands, q = _refine(img, card, cands, q, snap, top_k)
        # plausible geometry first: a merged two-card outline can rack up
        # art inliers but never maps cleanly onto one template
        scored.append(((cands[0].geo_ok, cands[0].art_inliers, cands[0].inliers), q, card, cands))
    scored.sort(key=lambda t: t[0], reverse=True)
    kept = []
    for key, q, card, cands in scored:
        if all(_overlap(q, k[1]) < 0.3 for k in kept):
            kept.append((key, q, card, cands))
    return [_decide(card, cands, q) for _, q, card, cands in
            sorted(kept, key=lambda t: (t[1][:, 1].mean(), t[1][:, 0].mean()))]


VIEW_MAX_SIDE = 3200    # straightened photo (loose cards), px
VIEW_REACH = 0.6        # loose cards: how far past the grid (of its size) the straightened view reaches


def _multi_straightened(img: np.ndarray, page: np.ndarray, rows: int, cols: int, top_k: int) -> list[dict] | None:
    """
    Loose cards shot at an angle: the whole photo turned upright by the grid's
    corners (cards may lie anywhere, also outside the grid), cards found as
    usual, corners mapped back into the photo.
    """
    h, w = img.shape[:2]
    q = (page * np.float32([w, h])).astype(np.float32)
    lens = [float(np.linalg.norm(q[(i + 1) % 4] - q[i])) for i in range(4)]
    aspect = (cols * 63.0) / (rows * 88.0) if rows > 0 and cols > 0 else (lens[0] + lens[2]) / max(lens[1] + lens[3], 1)
    pw = max(lens[0], lens[2], max(lens[1], lens[3]) * aspect)
    ph = pw / aspect
    H = cv2.getPerspectiveTransform(q, np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]))
    with timing.phase("page"):
        corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        try:
            at = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
        except cv2.error:
            return None
        if not np.all(np.isfinite(at)):
            return None
        x0 = max(float(at[:, 0].min()), -VIEW_REACH * pw)
        y0 = max(float(at[:, 1].min()), -VIEW_REACH * ph)
        x1 = min(float(at[:, 0].max()), (1 + VIEW_REACH) * pw)
        y1 = min(float(at[:, 1].max()), (1 + VIEW_REACH) * ph)
        if x1 - x0 < 100 or y1 - y0 < 100:
            return None
        k = min(1.0, VIEW_MAX_SIDE / max(x1 - x0, y1 - y0))
        S = np.float64([[k, 0, -x0 * k], [0, k, -y0 * k], [0, 0, 1]])
        Hv = S @ H
        view = cv2.warpPerspective(img, Hv, (int(round((x1 - x0) * k)), int(round((y1 - y0) * k))),
                                   flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    back = np.linalg.inv(Hv)
    return [_result(d, cv2.perspectiveTransform(np.float32(oq).reshape(-1, 1, 2), back).reshape(-1, 2), img, None)
            for d, oq in _multi_cards(view, top_k)]


def _run_match(img: np.ndarray, mode: str, rows: int, cols: int, top_k: int, page: np.ndarray | None = None,
               skip: frozenset[int] = frozenset()) -> dict:
    timing.start()
    t0 = time.perf_counter()
    h, w = img.shape[:2]
    cards: list[dict] = []
    straightened = None
    if mode == "binder" and rows > 0 and cols > 0 and page is not None:
        straightened = _binder_straightened(img, page, rows, cols, top_k, skip)
    elif mode == "multi" and page is not None:
        straightened = _multi_straightened(img, page, rows, cols, top_k)

    if straightened is not None:
        cards = straightened
    elif mode == "binder" and rows > 0 and cols > 0:
        ch, cw = h // rows, w // cols
        for r in range(rows):
            for c in range(cols):
                if r * cols + c in skip:
                    continue
                x0, y0 = c * cw, r * ch
                cell = img[y0:y0 + ch, x0:x0 + cw]
                opts = [d.quad + [x0, y0] for d in _detect(cell, max_cards=2, nested=True)
                        if d.area_frac > 0.3]
                opts.append(detect.whole_image_as_card(cell) + [x0, y0])
                d, q = _best_of(img, opts, top_k)
                if d.candidates and d.candidates[0].inliers >= matcher.MIN_GOOD:  # skip empty pockets
                    cards.append(_result(d, q, img, r * cols + c))
    elif mode == "multi":
        cards = [_result(d, oq, img, None) for d, oq in _multi_cards(img, top_k)]
    elif mode == "card":
        # Already a straightened card (the user set its corners in the app):
        # no detection, match the whole image as the card.
        quad = detect.whole_image_as_card(img)
        card = detect.warp(img, quad)
        d, q = _decide(card, _match(card, store.snapshot(), top_k), quad)
        cards.append(_result(d, q, img, None))
    else:  # single
        dets = [x for x in _detect(img, max_cards=5, nested=True) if x.area_frac > 0.05]
        opts = [x.quad for x in dets] + [detect.whole_image_as_card(img)]
        d, q = _best_of(img, opts, top_k)
        cards.append(_result(d, q, img, None))

    res = {"mode": mode, "cards": cards, "ms": round((time.perf_counter() - t0) * 1000),
           "timing": timing.take(), "shortlist": "dinov2" if shortlist.active else "phash"}
    if straightened is not None:
        res["page"] = "straightened"
    return res
