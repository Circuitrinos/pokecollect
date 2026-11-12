"""
Identify a straightened card: shortlist (DINOv2 embedding + pHash, or pHash
alone) -> ORB + RANSAC refine -> decide whether there is a clean choice or the
user must pick.
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from . import features
from .catalog import CatalogCard, num_key
from .store import Snapshot

# Decision thresholds (tune on real scans). They count "art inliers": RANSAC
# inliers whose template keypoint lies in the artwork window. Inliers on the
# card frame, text box and symbols are shared by every card of an era and say
# little about *which* card it is.
MIN_ART = 10              # art match alone is trusted from here ...
LEAD = 2.0                # ... if it also beats the runner-up by this factor
MIN_ART_NUMBER = 6        # a number-confirmed candidate needs at least this much art
ART_Y = (0.05, 0.52)      # art window in the template's center crop (fractions)
ART_X = (0.04, 0.96)
DEFAULT_TEMPLATE_WH = (734, 1024)  # pokemontcg.io hires scan size
LOWE = 0.75
MIN_GOOD = 12
RANSAC_PX = 5.0
RANSAC_TOP = 20           # geometric check only for the candidates with most good matches
MAX_CORNER_ERR = 0.15     # straightened photo vs template: corners may be off by this
                          # fraction of the template diagonal, else the fit is a fluke
RANSAC_ITERS = 1000

# OpenCV releases the GIL in knnMatch/findHomography, so threads give real speed-up.
_pool = ThreadPoolExecutor(max_workers=max(2, (os.cpu_count() or 4)))


@dataclass
class Candidate:
    card_id: str
    score: float
    inliers: int
    art_inliers: int
    good: int
    phash_dist: int
    upside_down: bool = False
    H: np.ndarray | None = None          # query-crop -> template-crop homography
    template_wh: tuple[int, int] | None = None
    geo_ok: bool = False                 # homography is a plausible card-to-card mapping
    learned: bool = False                # best template is a user's own scan photo (learn.py)
    catalog_art: int = 0                 # best art inliers on this card's catalog templates


@dataclass
class Decision:
    auto: bool
    reason: str                 # art | number | weak | close | none
    confidence: float
    number_read: str | None = None
    candidates: list[Candidate] = field(default_factory=list)


def _bhattacharyya(q: np.ndarray, hs: np.ndarray) -> np.ndarray:
    """Vectorised cv2.HISTCMP_BHATTACHARYYA for one query against many histograms."""
    n = q.size
    qm, hm = q.mean(), hs.mean(axis=1)
    bc = np.sqrt(q[None, :] * hs).sum(axis=1) / np.sqrt(qm * hm * n * n + 1e-12)
    return np.sqrt(np.clip(1.0 - bc, 0.0, 1.0))


def match_card(card_bgr: np.ndarray, snap: Snapshot, top_k: int = 5, preselect: int = 300,
               shortlist: tuple[list[str], int] | None = None) -> list[Candidate]:
    """
    shortlist = (card ids from the embedding, n): check those cards' templates
    plus the n best pHash templates (n < 0: the full pHash preselection, used
    while embeddings don't cover every card yet). None = pHash only.
    """
    if len(snap) == 0:
        return []
    f = features.extract(card_bgr)
    if f is None:
        return []
    crop180 = cv2.rotate(features.center_crop(card_bgr), cv2.ROTATE_180)
    q0, q180 = np.uint64(f.phash), np.uint64(features.phash63(crop180))
    d0 = np.bitwise_count(snap.phash ^ q0).astype(np.int32)
    d180 = np.bitwise_count(snap.phash ^ q180).astype(np.int32)
    dist = np.minimum(d0, d180)

    n_phash = preselect if shortlist is None or shortlist[1] < 0 else shortlist[1]
    chosen = np.zeros(0, np.int64)
    if n_phash > 0:
        pool = min(len(dist), 2 * n_phash)
        near = np.argpartition(dist, pool - 1)[:pool] if pool < len(dist) else np.arange(len(dist))
        hd = _bhattacharyya(f.hist, snap.hist[near])
        chosen = near[np.lexsort((hd, dist[near]))[:n_phash]]
    if shortlist is not None:
        emb_t = [t for cid in shortlist[0] for t in snap.by_card.get(cid, ())]
        chosen = np.array(list(dict.fromkeys(emb_t + [int(t) for t in chosen])), np.int64)

    def ratio_matches(t: int):
        return t, _ratio_matches(f, snap, t)

    def geometry(t: int, good: list[tuple[int, int]]):
        inl, art, upside, H, twh, err = _geometry(f, snap, t, good, qcw, qch)
        return inl, art, upside, H, twh, err <= MAX_CORNER_ERR

    qch, qcw = features.center_crop(card_bgr).shape[:2]
    goods = dict(_pool.map(ratio_matches, [int(t) for t in chosen]))
    ranked = sorted((t for t, g in goods.items() if len(g) >= MIN_GOOD),
                    key=lambda t: len(goods[t]), reverse=True)[:RANSAC_TOP]
    geo = dict(zip(ranked, _pool.map(lambda t: geometry(t, goods[t]), ranked)))

    best: dict[str, Candidate] = {}
    cat_art: dict[str, int] = {}
    learned = snap.learned if len(snap.learned) == len(snap.card_id) else np.zeros(len(snap.card_id), bool)
    for t, good in goods.items():
        inliers, art, upside, H, twh, geo_ok = geo.get(t, (0, 0, False, None, None, False))
        pd = int(dist[t])
        c = Candidate(snap.card_id[t], art * 4.0 + inliers + len(good) * 0.5 + (64 - pd) * 0.5,
                      inliers, art, len(good), pd, upside, H, twh, geo_ok, bool(learned[t]))
        if not c.learned:
            cat_art[c.card_id] = max(cat_art.get(c.card_id, 0), art)
        prev = best.get(c.card_id)
        if prev is None or c.score > prev.score:
            best[c.card_id] = c
    for c in best.values():
        c.catalog_art = cat_art.get(c.card_id, 0)
    return sorted(best.values(), key=lambda c: c.score, reverse=True)[:top_k]


def _ratio_matches(f: features.Features, snap: Snapshot, t: int) -> list[tuple[int, int]]:
    seg = snap.segments[int(snap.seg_of[t])]
    des, kp = seg.template(int(snap.idx_in_seg[t]))
    if len(des) < 2:
        return []
    knn = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(f.des, des, k=2)
    return [(m.queryIdx, m.trainIdx) for pair in knn if len(pair) == 2
            for m, n in [pair] if m.distance < LOWE * n.distance]


def _geometry(f: features.Features, snap: Snapshot, t: int, good: list[tuple[int, int]],
              qcw: int, qch: int):
    """(inliers, art inliers, upside down, H, template wh, corner error) of one template."""
    seg = snap.segments[int(snap.seg_of[t])]
    j = int(snap.idx_in_seg[t])
    _, kp = seg.template(j)
    src = f.kp_xy[[q for q, _ in good]].reshape(-1, 1, 2)
    dst = kp[[r for _, r in good]].reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PX, maxIters=RANSAC_ITERS)
    if mask is None or H is None:
        return 0, 0, False, None, None, math.inf
    inl = mask.ravel().astype(bool)
    meta = seg.meta[j]
    cw = int((meta.get("w") or DEFAULT_TEMPLATE_WH[0]) * features.CROP_FRAC)
    ch = int((meta.get("h") or DEFAULT_TEMPLATE_WH[1]) * features.CROP_FRAC)
    p = dst.reshape(-1, 2)[inl]
    art = int(((p[:, 0] >= ART_X[0] * cw) & (p[:, 0] <= ART_X[1] * cw)
               & (p[:, 1] >= ART_Y[0] * ch) & (p[:, 1] <= ART_Y[1] * ch)).sum())
    upside = abs(math.degrees(math.atan2(H[1, 0], H[0, 0]))) > 90
    twh = (int(meta.get("w") or DEFAULT_TEMPLATE_WH[0]), int(meta.get("h") or DEFAULT_TEMPLATE_WH[1]))
    # Both images are straightened cards, so a true match maps the query
    # crop's corners onto the template crop's corners (maybe rotated 180°).
    qc = np.float32([[0, 0], [qcw, 0], [qcw, qch], [0, qch]])
    tc = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    if upside:
        tc = np.roll(tc, 2, axis=0)
    mapped = cv2.perspectiveTransform(qc.reshape(-1, 1, 2), H).reshape(4, 2)
    err = float(np.mean(np.linalg.norm(mapped - tc, axis=1))) / math.hypot(cw, ch)
    return int(inl.sum()), art, upside, H, twh, err


def verify_card(card_bgr: np.ndarray, snap: Snapshot, card_id: str) -> dict:
    """
    How well a saved, straightened scan photo fits its confirmed card — against
    that card's CATALOG images only (a user's own photo is also indexed as a
    reference, and would always match itself). A sloppy crop shows as a large
    corner error; a wrong card or a bad photo as few inliers. `outline` is where
    the card really lies in the photo (fractions of its width/height, card's
    top-left first), for suggesting better corners.
    """
    learned = snap.learned if len(snap.learned) == len(snap.card_id) else np.zeros(len(snap.card_id), bool)
    rows = [t for t in snap.by_card.get(card_id, []) if not learned[t]]
    out: dict = {"card_id": card_id, "templates": len(rows), "inliers": 0, "art_inliers": 0,
                 "corner_err": None, "geo_ok": False, "upside_down": False, "outline": None}
    if not rows:
        return out
    f = features.extract(card_bgr)
    if f is None:
        return out
    qch, qcw = features.center_crop(card_bgr).shape[:2]
    best = None
    for t in rows:
        good = _ratio_matches(f, snap, t)
        if len(good) < 4:
            continue
        g = _geometry(f, snap, t, good, qcw, qch)
        if best is None or g[0] > best[0]:
            best = g
    if best is None or best[3] is None:
        return out
    inl, art, upside, H, twh, err = best
    out.update(inliers=inl, art_inliers=art, corner_err=round(err, 4), geo_ok=bool(err <= MAX_CORNER_ERR),
               upside_down=bool(upside))
    if inl >= MIN_GOOD:
        c = Candidate(card_id, 0.0, inl, art, 0, 0, upside, H, twh)
        pts = card_outline_in_query(c, (card_bgr.shape[1], card_bgr.shape[0]))
        if pts is not None:
            out["outline"] = (pts / [card_bgr.shape[1], card_bgr.shape[0]]).round(4).tolist()
    return out


LOCATE_FEATURES = 3000      # ORB features on a whole picture (screenshot, store image)


def locate_card(img_bgr: np.ndarray, snap: Snapshot, card_ids: list[str],
                roi: tuple[int, int, int, int] | None = None, max_templates: int = 24) -> dict | None:
    """
    Find a card in an arbitrary picture (not straightened) by its artwork: ORB on
    the whole picture (or the roi x0,y0,x1,y1) against the catalog templates of
    card_ids, homography from the best one, and that template's full card corners
    mapped into the picture. For pictures of a card that has no catalog image
    yet, card_ids are other prints of the same name — promos and reprints
    usually share the artwork. → {"quad" (px, card's TL first), "inliers", "card_id"}.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    mask = None
    if roi is not None:
        mask = np.zeros(gray.shape, np.uint8)
        x0, y0, x1, y1 = roi
        mask[y0:y1, x0:x1] = 255
    kps, des = features.orb_detector(LOCATE_FEATURES).detectAndCompute(gray, mask)
    if des is None or len(kps) < MIN_GOOD:
        return None
    f = features.Features(np.float32([k.pt for k in kps]), np.ascontiguousarray(des, np.uint8), 0,
                          np.zeros(256, np.float32))
    learned = snap.learned if len(snap.learned) == len(snap.card_id) else np.zeros(len(snap.card_id), bool)
    rows = [(cid, t) for cid in card_ids for t in snap.by_card.get(cid, []) if not learned[t]][:max_templates]
    best = None
    for cid, t in rows:
        good = _ratio_matches(f, snap, t)
        if len(good) < MIN_GOOD:
            continue
        seg = snap.segments[int(snap.seg_of[t])]
        j = int(snap.idx_in_seg[t])
        _, kp = seg.template(j)
        src = f.kp_xy[[q for q, _ in good]].reshape(-1, 1, 2)
        dst = kp[[r for _, r in good]].reshape(-1, 1, 2)
        H, m = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PX, maxIters=RANSAC_ITERS)
        if H is None or m is None:
            continue
        inl = int(m.sum())
        if best is None or inl > best[0]:
            meta = seg.meta[j]
            twh = (int(meta.get("w") or DEFAULT_TEMPLATE_WH[0]), int(meta.get("h") or DEFAULT_TEMPLATE_WH[1]))
            best = (inl, cid, H, twh)
    if best is None or best[0] < MIN_GOOD:
        return None
    inl, cid, H, (tw, th) = best
    try:
        Hinv = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return None
    tx0, ty0 = (tw - int(tw * features.CROP_FRAC)) // 2, (th - int(th * features.CROP_FRAC)) // 2
    corners = np.float32([[-tx0, -ty0], [tw - tx0, -ty0], [tw - tx0, th - ty0], [-tx0, th - ty0]])
    quad = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), Hinv).reshape(4, 2)
    return {"quad": quad, "inliers": inl, "card_id": cid}


def card_outline_in_query(c: Candidate, query_wh: tuple[int, int]) -> np.ndarray | None:
    """
    Where the matched template's full card lies in the straightened query image,
    via the inverse homography. Lets a partial or sloppy detection be corrected
    to the card's true outline (and orientation: TL is the card's real top-left).
    """
    if c.H is None or c.template_wh is None:
        return None
    try:
        Hinv = np.linalg.inv(c.H)
    except np.linalg.LinAlgError:
        return None
    tw, th = c.template_wh
    tx0, ty0 = (tw - int(tw * features.CROP_FRAC)) // 2, (th - int(th * features.CROP_FRAC)) // 2
    corners = np.float32([[-tx0, -ty0], [tw - tx0, -ty0], [tw - tx0, th - ty0], [-tx0, th - ty0]])
    pts = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), Hinv).reshape(4, 2)
    qw, qh = query_wh
    pts += [(qw - int(qw * features.CROP_FRAC)) // 2, (qh - int(qh * features.CROP_FRAC)) // 2]
    return pts


def art_clear(cands: list[Candidate]) -> bool:
    """The art match alone decides (no OCR needed)."""
    if not cands:
        return False
    top = cands[0]
    rival = max((c.art_inliers for c in cands[1:]), default=0)
    if not (top.geo_ok and top.art_inliers >= MIN_ART and top.art_inliers >= LEAD * max(rival, 1)):
        return False
    if not top.learned:
        return True
    # The lead comes from the user's own photo of this card. A reprint with the
    # same art would match that photo just as well, so only trust it when no
    # rival shares the art in the catalog (else the printed number decides).
    rival_cat = max((c.catalog_art for c in cands[1:]), default=0)
    return rival_cat < MIN_ART_NUMBER or top.catalog_art >= LEAD * rival_cat


def _number_matches(card: CatalogCard | None, num: str, total: str) -> bool:
    if card is None or num_key(card.local_id) != num_key(num):
        return False
    if total.isdigit() and card.set_official:
        return int(total) == card.set_official
    return True  # gallery totals (TG30) or unknown set size: number alone


def decide(cands: list[Candidate], catalog: dict[str, CatalogCard],
           read_numbers: Callable[[], list[tuple[str, str]]]) -> Decision:
    if not cands:
        return Decision(False, "none", 0.0)
    top = cands[0]
    if art_clear(cands):
        return Decision(True, "art", round(min(0.99, 0.8 + top.art_inliers / 200), 2), candidates=cands)

    # A number can only confirm a candidate with some art support; skip the
    # (slow) OCR when nothing qualifies, e.g. an empty binder pocket.
    numbers = read_numbers() if top.art_inliers >= MIN_ART_NUMBER else []
    shown = f"{numbers[0][0]}/{numbers[0][1]}" if numbers else None
    if numbers:
        hits = [c for c in cands if c.geo_ok and c.art_inliers >= MIN_ART_NUMBER
                and any(_number_matches(catalog.get(c.card_id), n, t) for n, t in numbers)]
        if len(hits) == 1:
            hit = hits[0]
            n, t = next((n, t) for n, t in numbers if _number_matches(catalog.get(hit.card_id), n, t))
            ordered = [hit] + [c for c in cands if c is not hit]
            return Decision(True, "number", 0.9, f"{n}/{t}", ordered)
    reason = "weak" if top.art_inliers < MIN_ART else "close"
    return Decision(False, reason, 0.25 if reason == "weak" else 0.45, shown, cands)
