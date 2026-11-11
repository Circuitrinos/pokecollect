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

from . import detect, embedding, learn, matcher, numocr, recrop, timing
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
    with _match_lock:
        res = matcher.verify_card(crop, store.snapshot(), card_id)
    res["suggested_quad"] = None
    if res.get("outline") and orig and quad:
        try:
            q = json.loads(quad)
            original = cv2.imdecode(np.frombuffer(orig, np.uint8), cv2.IMREAD_COLOR)
            if original is not None and isinstance(q, list) and len(q) == 4:
                res.update(recrop.suggest_quad(original, crop, q, res["outline"]))
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
