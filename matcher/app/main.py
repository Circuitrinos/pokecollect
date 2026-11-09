"""
PokéCollect card matcher — identifies cards by artwork.

POST /match   photo (+ mode, rows, cols) -> per detected card: corners,
              top candidates (TCGdex ids), and whether the choice is clean
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

from . import detect, matcher, numocr
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
MAX_BYTES = 12 * 1024 * 1024

app = FastAPI(title="PokéCollect matcher")
store = Store()
state: dict = {"status": "loading", "error": None, "mapping": None}
catalog: dict[str, CatalogCard] = {}
extender = Extender(SEG_DIR, store, EXTEND_REGIONS)


def _load_catalog_with_retry() -> dict[str, CatalogCard]:
    delay = 5
    while True:
        try:
            return load_catalog(DATABASE_URL)
        except Exception as exc:
            log.warning("Catalog not reachable yet (%s); retrying in %ds", exc, delay)
            time.sleep(delay)
            delay = min(delay * 2, 300)


def _startup() -> None:
    global catalog
    try:
        SEG_DIR.mkdir(parents=True, exist_ok=True)
        base = SEG_DIR / "base"
        if not base.with_suffix(".npz").exists() and INDEX_PKL.exists():
            state["status"] = "converting"
            convert(INDEX_PKL, base)
        state["status"] = "loading"
        catalog = _load_catalog_with_retry()
        if base.with_suffix(".npz").exists():
            seg = load_segment(base)
            seg.card_ids, report = map_pokemontcg(seg.meta, catalog)
            state["mapping"] = report.as_dict()
            store.add(seg)
        n_ext = extender.load_existing(catalog)
        log.info("Ready: %s (+%d from earlier extension runs)", store.stats(), n_ext)
        state["status"] = "ready"
    except Exception as exc:
        log.exception("Startup failed")
        state.update(status="error", error=str(exc))
        return
    while EXTEND:
        extender.run_once(catalog)
        time.sleep(REFRESH_HOURS * 3600)
        catalog = _load_catalog_with_retry()


@app.on_event("startup")
def _on_startup() -> None:
    threading.Thread(target=_startup, name="startup", daemon=True).start()


@app.get("/health")
def health() -> dict:
    return {**state, "store": store.stats(), "extension": extender.status,
            "ocr": numocr.available(), "catalog": len(catalog)}


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
    decision = matcher.decide(cands, catalog,
                              lambda: numocr.read_numbers(card, upside_down=upside, wanted=wanted))
    q = detect.portrait_order(quad)
    if decision.candidates and decision.candidates[0].upside_down:
        q = np.roll(q, 2, axis=0)  # report corners in the card's real orientation
    return decision, q


def _identify(img: np.ndarray, quad: np.ndarray, top_k: int) -> tuple[matcher.Decision, np.ndarray]:
    card = detect.warp(img, quad)
    return _decide(card, matcher.match_card(card, store.snapshot(), top_k=top_k), quad)


def _best_of(img: np.ndarray, quads: list[np.ndarray], top_k: int) -> tuple[matcher.Decision, np.ndarray]:
    """
    Match every outline hypothesis (cheap), keep the one with the strongest art
    match, and only then run the decision (which may need slow OCR) on it.
    """
    snap = store.snapshot()
    best = None
    for q in quads:
        card = detect.warp(img, q)
        cands = matcher.match_card(card, snap, top_k=top_k)
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
        cands2 = matcher.match_card(card2, snap, top_k=top_k)
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


@app.post("/match")
async def match(file: UploadFile = File(...), mode: str = Form("single"),
                rows: int = Form(0), cols: int = Form(0), top_k: int = Form(5)) -> dict:
    if state["status"] != "ready":
        raise HTTPException(503, f"matcher not ready: {state['status']}")
    data = await file.read()
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(400, "empty or oversized image")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "unreadable image")
    top_k = max(1, min(top_k, 10))
    t0 = time.perf_counter()
    h, w = img.shape[:2]
    cards: list[dict] = []

    if mode == "binder" and rows > 0 and cols > 0:
        ch, cw = h // rows, w // cols
        for r in range(rows):
            for c in range(cols):
                x0, y0 = c * cw, r * ch
                cell = img[y0:y0 + ch, x0:x0 + cw]
                opts = [d.quad + [x0, y0] for d in detect.detect_cards(cell, max_cards=2, nested=True)
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
        for det in detect.detect_cards(img, max_cards=24, nested=True):
            card = detect.warp(img, det.quad)
            cands = matcher.match_card(card, snap, top_k=top_k)
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
    else:  # single
        dets = [x for x in detect.detect_cards(img, max_cards=5, nested=True) if x.area_frac > 0.05]
        opts = [x.quad for x in dets] + [detect.whole_image_as_card(img)]
        d, q = _best_of(img, opts, top_k)
        cards.append(_result(d, q, img.shape, None))

    return {"mode": mode, "cards": cards, "ms": round((time.perf_counter() - t0) * 1000)}
