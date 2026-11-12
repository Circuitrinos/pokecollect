"""
PokéCollect card matcher — identifies cards by artwork.

POST /match   photo (+ mode, rows, cols) -> per detected card: corners,
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

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

import json

from . import detect, embedding, frame, learn, matcher, numocr, recrop, stamp, timing
from .catalog import CatalogCard, load_catalog, map_pokemontcg, num_key
from .convert import convert
from .extend import Extender
from .store import Store, load_segment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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


def _best_of(img: np.ndarray, quads: list[np.ndarray], top_k: int) -> tuple[matcher.Decision, np.ndarray]:
    """
    Match every outline hypothesis (cheap), keep the one with the strongest art
    match, and only then run the decision (which may need slow OCR) on it.
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


def _result(d: matcher.Decision, quad: np.ndarray, shape, position: int | None) -> dict:
    h, w = shape[:2]
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
    shaped = [detect.portrait_order(c) for c in cands if detect._rectangularity(c) <= 2.5 and area(c) >= 0.02 * w * h]
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
        near = [x for x in scored if float(np.abs(detect.portrait_order(x[1]) - q).max()) < FRAME_AGREE * side]
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
        rough = np.float32(np.float32(q) * [w, h]) if isinstance(q, list) and len(q) == 4 else None
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
    return {**r, "ok": r["score"] >= stamp.MIN_SCORE, "template": True}


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
                rows: int = Form(0), cols: int = Form(0), top_k: int = Form(5)) -> dict:
    if state["status"] != "ready":
        raise HTTPException(503, f"matcher not ready: {state['status']}")
    data = await file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "empty or oversized image")
    # The work is CPU-bound: run it in a worker thread so /health keeps
    # answering (the backend's status check has a 2 s timeout).
    return await run_in_threadpool(_match_request, data, mode, rows, cols, max(1, min(top_k, 10)))


def _match_request(data: bytes, mode: str, rows: int, cols: int, top_k: int) -> dict:
    t0 = time.perf_counter()
    with _match_lock:
        queued = time.perf_counter() - t0
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "unreadable image")
        res = _run_match(img, mode, rows, cols, top_k)
        state["matches"] += 1
    if queued >= 0.05:
        res["timing"]["queued"] = round(queued * 1000)
    log.info("match %s %dx%d: %d card(s) in %d ms %s%s", mode, img.shape[1], img.shape[0],
             len(res["cards"]), res["ms"], res["timing"],
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


def _run_match(img: np.ndarray, mode: str, rows: int, cols: int, top_k: int) -> dict:
    timing.start()
    t0 = time.perf_counter()
    h, w = img.shape[:2]
    cards: list[dict] = []

    if mode == "binder" and rows > 0 and cols > 0:
        ch, cw = h // rows, w // cols
        for r in range(rows):
            for c in range(cols):
                x0, y0 = c * cw, r * ch
                cell = img[y0:y0 + ch, x0:x0 + cw]
                opts = [d.quad + [x0, y0] for d in _detect(cell, max_cards=2, nested=True)
                        if d.area_frac > 0.3]
                opts.append(detect.whole_image_as_card(cell) + [x0, y0])
                d, q = _best_of(img, opts, top_k)
                if d.candidates and d.candidates[0].inliers >= matcher.MIN_GOOD:  # skip empty pockets
                    cards.append(_result(d, q, img.shape, r * cols + c))
    elif mode == "multi":
        # Match every outline, then keep the strongest matches greedily and drop
        # anything overlapping a kept one. Mats, merged pairs and partial
        # sub-rectangles match worse than a true card outline, so they lose.
        snap = store.snapshot()
        scored = []
        for det in _detect(img, max_cards=24, nested=True):
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
        for _, q, card, cands in sorted(kept, key=lambda t: (t[1][:, 1].mean(), t[1][:, 0].mean())):
            d, oq = _decide(card, cands, q)
            cards.append(_result(d, oq, img.shape, None))
    elif mode == "card":
        # Already a straightened card (the user set its corners in the app):
        # no detection, match the whole image as the card.
        quad = detect.whole_image_as_card(img)
        card = detect.warp(img, quad)
        d, q = _decide(card, _match(card, store.snapshot(), top_k), quad)
        cards.append(_result(d, q, img.shape, None))
    else:  # single
        dets = [x for x in _detect(img, max_cards=5, nested=True) if x.area_frac > 0.05]
        opts = [x.quad for x in dets] + [detect.whole_image_as_card(img)]
        d, q = _best_of(img, opts, top_k)
        cards.append(_result(d, q, img.shape, None))

    return {"mode": mode, "cards": cards, "ms": round((time.perf_counter() - t0) * 1000),
            "timing": timing.take(), "shortlist": "dinov2" if shortlist.active else "phash"}
