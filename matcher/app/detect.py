"""
Card detection and straightening (edge-based, no ML model needed).

Ported from PokemonScanner's make_card_edges / _approx_quads_from_edges_gap_safe
with two changes: the edge distance transform is computed once per image, and
warps are always portrait (a card lying sideways is rotated so its long side is
vertical; the remaining 0/180 ambiguity is handled by the matcher).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

CARD_W, CARD_H, PAD = 630, 880, 12


@dataclass
class Detection:
    quad: np.ndarray          # (4, 2) float32, image px, TL TR BR BL (portrait order)
    area_frac: float          # quad area / image area


def order_points(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, np.float32).reshape(4, 2)
    rect = np.zeros((4, 2), np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).reshape(4)
    rect[0], rect[2] = pts[np.argmin(s)], pts[np.argmax(s)]
    rect[1], rect[3] = pts[np.argmin(d)], pts[np.argmax(d)]
    return rect


def portrait_order(quad: np.ndarray) -> np.ndarray:
    """TL,TR,BR,BL such that TL->BL (the left side) is a long side."""
    q = order_points(quad)
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    if w > h:  # lying sideways: rotate the corner order by one
        q = np.roll(q, -1, axis=0)
    return q


WARP_W, WARP_H = CARD_W + 2 * PAD, CARD_H + 2 * PAD


def warp_matrix(quad: np.ndarray) -> np.ndarray:
    """Photo -> straightened-card transform (portrait, with padding)."""
    q = portrait_order(quad)
    dst = np.float32([[PAD, PAD], [WARP_W - PAD - 1, PAD], [WARP_W - PAD - 1, WARP_H - PAD - 1],
                      [PAD, WARP_H - PAD - 1]])
    return cv2.getPerspectiveTransform(q, dst)


def warp(img: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Straighten a card to 630x880 (+12 px white padding), portrait."""
    return cv2.warpPerspective(img, warp_matrix(quad), (WARP_W, WARP_H), borderValue=(255, 255, 255))


def plausible_quad(q: np.ndarray, shape) -> bool:
    """Convex, card-shaped, and mostly inside the photo."""
    h, w = shape[:2]
    if q is None or not np.all(np.isfinite(q)) or not cv2.isContourConvex(q.astype(np.float32).reshape(-1, 1, 2)):
        return False
    if not (np.all(q[:, 0] > -0.1 * w) and np.all(q[:, 0] < 1.1 * w)
            and np.all(q[:, 1] > -0.1 * h) and np.all(q[:, 1] < 1.1 * h)):
        return False
    a = cv2.contourArea(q.astype(np.float32))
    wq, hq = cv2.minAreaRect(q.astype(np.float32))[1]
    return a > 0.01 * h * w and min(wq, hq) > 1 and 1.15 <= max(wq, hq) / min(wq, hq) <= 1.9


def whole_image_as_card(img: np.ndarray) -> np.ndarray:
    """Fallback when nothing is detected: assume the photo is framed on the card."""
    h, w = img.shape[:2]
    return np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])


# ── edge map ─────────────────────────────────────────────────────────────────

def _card_edges(img: np.ndarray, max_side: int = 1600) -> tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    scale = min(1.0, max_side / float(max(h, w)))
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img
    H, W = small.shape[:2]

    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
    L = cv2.bilateralFilter(lab[:, :, 0], 7, 50, 50)
    L = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(L)
    gx = cv2.Scharr(L, cv2.CV_32F, 1, 0)
    gy = cv2.Scharr(L, cv2.CV_32F, 0, 1)
    mag = cv2.GaussianBlur(cv2.magnitude(gx, gy), (0, 0), 1.2)
    sat = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32) / 255.0
    mag_u8 = cv2.normalize(mag * (1.0 - 0.7 * sat), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, edge0 = cv2.threshold(mag_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    k_len = max(5, int(round(0.01 * max(H, W))))
    edges = cv2.bitwise_or(
        cv2.morphologyEx(edge0, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k_len, 1))),
        cv2.morphologyEx(edge0, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k_len))))
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    # Hough re-paints long straight card sides; pointless (and slow) when most
    # of the image is already "edge" (busy background).
    lines = None
    if (edges > 0).mean() < 0.35:
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=80,
                                minLineLength=int(0.15 * min(H, W)), maxLineGap=int(0.03 * max(H, W)))
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0, :]:
            cv2.line(edges, (int(x1), int(y1)), (int(x2), int(y2)), 255, 2, cv2.LINE_AA)
    return edges, scale


def _quads(edge: np.ndarray, min_area: float, aspect_lo=1.2, aspect_hi=1.8,
           support_tol=2.0, support_min=0.55) -> list[np.ndarray]:
    H, W = edge.shape[:2]
    bridge = max(1, int(round(0.002 * max(H, W))))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (bridge, bridge))
    bridged = cv2.dilate(cv2.morphologyEx(edge, cv2.MORPH_CLOSE, k), k)
    dist = cv2.distanceTransform(np.where(bridged > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 3)

    out = []
    cnts, _ = cv2.findContours(bridged, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        if cv2.contourArea(c) < min_area:
            continue
        peri = cv2.arcLength(c, True)
        if peri < 60:
            continue
        cand = None
        for eps in (0.02, 0.03, 0.05):
            a = cv2.approxPolyDP(c, eps * peri, True)
            if len(a) == 4 and cv2.isContourConvex(a):
                cand = a
                break
        if cand is None:
            hull = cv2.convexHull(c)
            a = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
            if len(a) == 4 and cv2.isContourConvex(a):
                cand = a
        if cand is None:
            r = cv2.minAreaRect(c)
            if r[1][0] > 1 and r[1][1] > 1:
                cand = cv2.boxPoints(r).astype(np.int32).reshape(-1, 1, 2)
        if cand is None:
            continue
        wq, hq = cv2.minAreaRect(cand)[1]
        if wq < 1 or hq < 1 or not (aspect_lo <= max(wq, hq) / min(wq, hq) <= aspect_hi):
            continue
        mask = np.zeros((H, W), np.uint8)
        pts = cand.reshape(-1, 2).astype(np.int32)
        for i in range(4):
            cv2.line(mask, tuple(map(int, pts[i])), tuple(map(int, pts[(i + 1) % 4])), 255, max(1, bridge // 2))
        vals = dist[mask > 0]
        if vals.size and float(np.mean(vals <= support_tol)) >= support_min:
            out.append(cand.reshape(4, 2).astype(np.float32))
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1, w1, h1 = cv2.boundingRect(a.astype(np.int32))
    x2, y2, w2, h2 = cv2.boundingRect(b.astype(np.int32))
    iw = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
    ih = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
    inter = iw * ih
    return inter / (w1 * h1 + w2 * h2 - inter + 1e-6)


def _rectangularity(q: np.ndarray) -> float:
    """Lower is better: aspect off 63/88, uneven opposite sides, corner angles off 90."""
    o = order_points(q)
    wa, wb = np.linalg.norm(o[2] - o[3]), np.linalg.norm(o[1] - o[0])
    ha, hb = np.linalg.norm(o[1] - o[2]), np.linalg.norm(o[0] - o[3])
    aspect = min((wa + wb), (ha + hb)) / max((wa + wb), (ha + hb), 1e-6)
    skew = max(1 - min(wa, wb) / max(wa, wb, 1e-6), 1 - min(ha, hb) / max(ha, hb, 1e-6))
    angs = []
    for i in range(4):
        v1, v2 = o[i - 1] - o[i], o[(i + 1) % 4] - o[i]
        cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6)
        angs.append(abs(90 - np.degrees(np.arccos(np.clip(cos, -1, 1)))))
    return abs(aspect - 63 / 88) * 4 + skew * 10 + float(np.mean(angs)) * 0.5


def _inside(a: np.ndarray, b: np.ndarray) -> bool:
    x1, y1, w1, h1 = cv2.boundingRect(a.astype(np.int32))
    x2, y2, w2, h2 = cv2.boundingRect(b.astype(np.int32))
    return x1 > x2 and y1 > y2 and x1 + w1 < x2 + w2 and y1 + h1 < y2 + h2


def _blur_canny_edges(img: np.ndarray, k: int, max_side: int = 1200) -> tuple[np.ndarray, float]:
    """Median blur wipes out table/playmat texture but keeps the card's border step."""
    h, w = img.shape[:2]
    scale = min(1.0, max_side / float(max(h, w)))
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img
    gray = cv2.medianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), k)
    med = float(np.median(gray))
    e = cv2.Canny(gray, int(max(0, 0.66 * med)), int(min(255, 1.33 * med)))
    e = cv2.dilate(e, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    return e, scale


def _hull_quads(edges: np.ndarray, min_area: float, k_frac: float = 0.04,
                aspect_lo=1.2, aspect_hi=1.8) -> list[np.ndarray]:
    """
    Gap-tolerant: group nearby edge fragments (heavy dilation), then fit a quad
    to each group's convex hull. Works when a card's outline is broken into
    pieces (glare, low contrast against the table) but nothing else is close.
    """
    H, W = edges.shape[:2]
    k = max(5, int(k_frac * max(H, W)))
    groups = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, labels = cv2.connectedComponents(groups)
    out = []
    for lab in range(1, n):
        ys, xs = np.nonzero((labels == lab) & (edges > 0))
        if len(xs) < 50:
            continue
        hull = cv2.convexHull(np.column_stack([xs, ys]).astype(np.int32))
        if cv2.contourArea(hull) < min_area:
            continue
        quad = None
        peri = cv2.arcLength(hull, True)
        for eps in (0.02, 0.04, 0.06, 0.08):
            a = cv2.approxPolyDP(hull, eps * peri, True)
            if len(a) == 4:
                quad = a.reshape(4, 2).astype(np.float32)
                break
        if quad is None:
            quad = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)
        wq, hq = cv2.minAreaRect(quad)[1]
        if wq > 1 and hq > 1 and aspect_lo <= max(wq, hq) / min(wq, hq) <= aspect_hi:
            out.append(quad)
    return out


def _line_quads(edges: np.ndarray, min_area: float, aspect_lo=1.2, aspect_hi=1.8,
                max_sides: int = 16, max_out: int = 12) -> list[np.ndarray]:
    """
    Fit quads from straight card sides: Hough segments -> merged collinear
    sides -> pairs of parallel sides crossed with a roughly perpendicular pair.
    Tolerates gaps in the outline, glare, and blobs touching the border.
    """
    H, W = edges.shape[:2]
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40,
                            minLineLength=int(0.06 * min(H, W)), maxLineGap=int(0.03 * max(H, W)))
    if lines is None:
        return []
    segs = lines[:, 0, :].astype(np.float64)
    d = segs[:, 2:] - segs[:, :2]
    length = np.hypot(d[:, 0], d[:, 1])
    order = np.argsort(-length)[:80]

    # side = (unit normal pointing away from the origin, rho >= 0, supporting length)
    sides: list[list] = []
    rho_tol = 0.015 * max(H, W)
    for i in order:
        n = np.array([-d[i, 1], d[i, 0]]) / max(length[i], 1e-9)
        mid = (segs[i, :2] + segs[i, 2:]) / 2
        rho = float(n @ mid)
        if rho < 0:
            n, rho = -n, -rho
        for s in sides:
            if float(n @ s[0]) > math.cos(math.radians(3)) and abs(rho - s[1]) < rho_tol:
                w = s[2] + length[i]
                s[0] = (s[0] * s[2] + n * length[i]) / w
                s[0] /= np.linalg.norm(s[0])
                s[1] = (s[1] * s[2] + rho * length[i]) / w
                s[2] = w
                break
        else:
            sides.append([n, rho, float(length[i])])
    sides.sort(key=lambda s: -s[2])
    sides = sides[:max_sides]

    dist = cv2.distanceTransform(np.where(edges > 0, 0, 255).astype(np.uint8), cv2.DIST_L2, 3)
    min_gap = 0.08 * min(H, W)
    par = [(a, b) for a in range(len(sides)) for b in range(a + 1, len(sides))
           if float(sides[a][0] @ sides[b][0]) > math.cos(math.radians(6))
           and abs(sides[a][1] - sides[b][1]) > min_gap]

    def cross(s1, s2):
        A = np.array([s1[0], s2[0]])
        if abs(np.linalg.det(A)) < 1e-6:
            return None
        return np.linalg.solve(A, np.array([s1[1], s2[1]]))

    scored = []
    for a, b in par:
        for c, e in par:
            if c <= a:
                continue
            if abs(float(sides[a][0] @ sides[c][0])) > math.sin(math.radians(20)):  # not ~perpendicular
                continue
            pts = [cross(sides[a], sides[c]), cross(sides[c], sides[b]),
                   cross(sides[b], sides[e]), cross(sides[e], sides[a])]
            if any(p is None for p in pts):
                continue
            q = order_points(np.float32(pts))
            if not cv2.isContourConvex(q.reshape(-1, 1, 2)) or cv2.contourArea(q) < min_area:
                continue
            wq, hq = cv2.minAreaRect(q)[1]
            if wq < 1 or hq < 1 or not (aspect_lo <= max(wq, hq) / min(wq, hq) <= aspect_hi):
                continue
            mask = np.zeros((H, W), np.uint8)
            cv2.polylines(mask, [q.astype(np.int32)], True, 255, 2)
            vals = dist[mask > 0]
            support = float(np.mean(vals <= 3.0)) if vals.size else 0.0
            if support >= 0.5:
                scored.append((support, q))
    scored.sort(key=lambda t: -t[0])
    return [q for _, q in scored[:max_out]]


def _hypotheses(img: np.ndarray) -> list[np.ndarray]:
    """Card outline candidates from several edge strategies (image px)."""
    out = []
    grad, gs = _card_edges(img)
    blurred = [_blur_canny_edges(img, 7), _blur_canny_edges(img, 15)]
    for edges, scale in [(grad, gs)] + blurred:
        Hs, Ws = edges.shape[:2]
        out += [q / scale for q in _quads(edges, min_area=0.0025 * Hs * Ws)]
    # Several grouping distances: small keeps neighbouring cards apart, large
    # joins a badly broken outline.
    for edges, scale in blurred:
        Hs, Ws = edges.shape[:2]
        for k_frac in (0.015, 0.03, 0.06):
            out += [q / scale for q in _hull_quads(edges, min_area=0.0025 * Hs * Ws, k_frac=k_frac)]
        out += [q / scale for q in _line_quads(edges, min_area=0.01 * Hs * Ws)]
    # Drop the photo's own frame (a contour along the image border).
    h, w = img.shape[:2]
    return [q for q in out if cv2.contourArea(q) < 0.9 * h * w]


def _nms(quads: list[np.ndarray], iou: float = 0.7) -> list[np.ndarray]:
    kept: list[np.ndarray] = []
    for q in sorted(quads, key=_rectangularity):
        if all(_iou(q, k) < iou for k in kept):
            kept.append(q)
    return kept


# Where the artwork sits on an upright card (modern Pokémon and trainer cards):
# about 83 % of the card's width, starting ~12 % from the top, ~38 % of its height.
ART_W, ART_TOP, ART_H = 0.83, 0.12, 0.38


def cards_around_art(quad: np.ndarray, shape) -> list[np.ndarray]:
    """
    A card's own edges often barely show (silver border on a light table) while
    its artwork frame does. For an outline shaped like an artwork frame on an
    upright card, the card around it: once with the art in the upper part, once
    upside down. The match then snaps to the real outline. [] if it isn't art-shaped.
    """
    q = order_points(quad)
    top, bottom = q[1] - q[0], q[2] - q[3]
    left, right = q[3] - q[0], q[2] - q[1]
    a = (np.linalg.norm(top) + np.linalg.norm(bottom)) / 2
    b = (np.linalg.norm(left) + np.linalg.norm(right)) / 2
    if b < 1 or not 1.2 <= a / b <= 2.2:
        return []
    u = (top + bottom) / max(np.linalg.norm(top + bottom), 1e-6)
    v = (left + right) / max(np.linalg.norm(left + right), 1e-6)
    w = a / ART_W
    h = w * CARD_H / CARD_W
    if not 0.25 <= b / h <= 0.55:
        return []
    m = (w - a) / 2
    up = np.float32([q[0] - m * u - ART_TOP * h * v, q[1] + m * u - ART_TOP * h * v])
    upright = np.float32([up[0], up[1], up[1] + h * v, up[0] + h * v])
    down = np.float32([q[3] - m * u + ART_TOP * h * v, q[2] + m * u + ART_TOP * h * v])
    flipped = np.float32([down[0] - h * v, down[1] - h * v, down[1], down[0]])
    return [c for c in (upright, flipped) if plausible_quad(c, shape)]


def detect_cards(img: np.ndarray, max_cards: int = 20, nested: bool = False) -> list[Detection]:
    """
    Card-shaped quads, largest first.

    nested=False (several loose cards): a quad holding two or more others is a
    mat or a group, not a card, and is dropped; a quad inside a remaining one
    (a card's art frame) is dropped too.
    nested=True (one card expected): keep everything; the matcher tries the
    largest few and keeps whichever matches best.
    """
    kept = _nms(_hypotheses(img))
    if not nested:
        holds = {id(o): sum(_inside(q, o) for q in kept if q is not o) for o in kept}
        kept = [o for o in kept if holds[id(o)] < 2]
        kept = [q for q in kept if not any(_inside(q, o) for o in kept if o is not q)]
    h, w = img.shape[:2]
    dets = [Detection(order_points(q), float(cv2.contourArea(q)) / (h * w)) for q in kept]
    dets.sort(key=lambda d: d.area_frac, reverse=True)
    return dets[:max_cards]
