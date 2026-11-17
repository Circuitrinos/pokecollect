"""
Cards on one page are all the same size — an outline that is clearly smaller
or bigger than the others is not the card.

Brian 17.11.: on a sheet of six, the Dunsparce outline was smaller than the
others. The detector had taken only part of the card (its white border on the
white sheet barely shows), and with a clear art match nothing corrected the
outline afterwards.

The check: from the other cards, the size a card should have at its place
(seen at an angle, cards further away are smaller: the size is fitted as a
plane over the picture, leaving the card itself out). An outline off by more
than SIZE_TOL, or not shaped like a card (ASPECT_TOL), is suspect. It is then
replaced by the matched card's own outline (from the match's homography), if
that one has the expected size and shape and does not run into another card;
otherwise it stays as it was (logged). Placing a card-sized rectangle by the
edges in the picture alone was tried and is not reliable enough (17.11.: on
the sheet clips about a third of such placements landed on the card).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from itertools import combinations

import cv2
import numpy as np

from . import detect

log = logging.getLogger("matcher")

SIZE_TOL = 0.15            # side (√area) more than this off the size expected at that place: suspect
ASPECT_TOL = 0.25          # height:width more than this off a card's 88:63: suspect (generous: seen at an angle
                           # the outline stretches — 17.11. up to 1.62 for a good card; half a card is ~0.75)
PLANE_MIN = 5              # cards needed for the size plane (with four, three of them fit any plane exactly)
CARD_ASPECT = detect.CARD_H / detect.CARD_W
MIN_OTHERS = 2             # cards needed besides this one to say how big it should be
MAX_OVERLAP = 0.3          # a replacement may cover this much of another card at most
MIN_KEEP = 0.5             # … and must still cover this much of the suspect outline (or be covered by it)


def shape(q) -> tuple[float, float, np.ndarray]:
    """Size (√area), height:width (portrait) and centre of an outline."""
    p = detect.portrait_order(np.float32(q))
    w = (np.linalg.norm(p[1] - p[0]) + np.linalg.norm(p[2] - p[3])) / 2
    h = (np.linalg.norm(p[3] - p[0]) + np.linalg.norm(p[2] - p[1])) / 2
    return float(np.sqrt(abs(cv2.contourArea(p)))), float(h / max(w, 1e-6)), p.mean(0)


def expected(qs: list) -> list[float | None]:
    """
    For each outline, the size the cards say it should have at its place.
    PLANE_MIN or more cards: a plane over the picture (perspective) through the
    largest group that agrees on one (every three cards propose a plane; the
    one at least four cards lie close to, most of them, wins, then fitted to
    those) — so a bad outline neither counts nor makes the good ones look off.
    Otherwise: the median of all of them (one bad outline among three or four
    does not move it).
    """
    sh = [shape(q) for q in qs]
    n = len(qs)
    s = np.array([x[0] for x in sh])
    if n - 1 < MIN_OTHERS:
        return [None] * n
    if n < PLANE_MIN:
        return [float(np.median(s))] * n
    A = np.array([[1.0, x[2][0], x[2][1]] for x in sh])
    best = np.ones(n, bool)
    best_n, best_res = -1, np.inf
    for tri in combinations(range(n), 3):
        idx = list(tri)
        try:
            coef = np.linalg.solve(A[idx], s[idx])
        except np.linalg.LinAlgError:
            continue
        res = np.abs(A @ coef / s - 1)
        agree = res <= SIZE_TOL / 2
        k, r = int(agree.sum()), float(res[agree].mean()) if agree.any() else np.inf
        if k > best_n or (k == best_n and r < best_res):
            best, best_n, best_res = agree, k, r
    if best_n < 4:
        return [float(np.median(s))] * n
    coef, *_ = np.linalg.lstsq(A[best], s[best], rcond=None)
    lo, hi = float(s[best].min()), float(s[best].max())
    return [min(max(float(a @ coef), lo * (1 - SIZE_TOL)), hi * (1 + SIZE_TOL)) for a in A]   # no wild extrapolation


def odd(q, exp: float) -> bool:
    s, a, _ = shape(q)
    return abs(s / exp - 1) > SIZE_TOL or abs(a / CARD_ASPECT - 1) > ASPECT_TOL


def overlap(a, b) -> float:
    """Intersection over the smaller outline (1.0 when one contains the other)."""
    a, b = np.float32(a), np.float32(b)
    inter, _ = cv2.intersectConvexConvex(a, b)
    return float(inter) / max(1e-6, min(cv2.contourArea(a), cv2.contourArea(b)))


def fits(q, exp: float, others: list, around=None) -> bool:
    """A replacement: the right size and shape, clear of the other cards, still where the suspect outline was."""
    if odd(q, exp) or any(overlap(q, o) > MAX_OVERLAP for o in others):
        return False
    return around is None or overlap(q, around) >= MIN_KEEP


def fix_sizes(img: np.ndarray, items: list, own_outline: Callable[[tuple], np.ndarray | None],
              what: str = "loose cards") -> list:
    """
    items: (key, quad, crop, candidates) per card, quads in `img`. Each outline
    far off the size the others give it is replaced by own_outline(item) (the
    matched card's own outline, or None) if that fits — with a fresh crop.
    """
    if len(items) <= MIN_OTHERS:
        return items
    qs = [np.float32(it[1]).reshape(4, 2) for it in items]
    exp = expected(qs)
    out = list(items)
    for i, it in enumerate(items):
        if exp[i] is None or not odd(qs[i], exp[i]):
            continue
        others = [qs[j] for j in range(len(qs)) if j != i]
        size, aspect, _ = shape(qs[i])
        new = own_outline(it)
        ok = new is not None and detect.plausible_quad(np.float32(new).reshape(4, 2), img.shape) \
            and fits(np.float32(new).reshape(4, 2), exp[i], others, around=qs[i])
        log.info("%s: outline %d is %.2f× the size the other %d card(s) give it (height:width %.2f)%s", what, i,
                 size / exp[i], len(others), aspect, " → the matched card's own outline" if ok
                 else " — kept (the match gives no outline of the right size)")
        if ok:
            q2 = np.float32(new).reshape(4, 2)
            out[i] = (it[0], q2, detect.warp(img, q2), it[3])
    return out
