"""
Page map for the live scan. The first frame of a live session that shows
cards is kept as the map (only its ORB features, no image). Later frames —
also close-ups of one or two cards — are placed on it by a homography, so the
app knows which pocket a card seen up close belongs to.

Coordinates are fractions of the image (x / width, y / height), in the frame
and in the map alike; h maps a frame point onto the map.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

import cv2
import numpy as np

SIDE = 1100             # features on the frame scaled to this long side …
NFEATURES = 4000
MAP_SIDE = 1600         # … and on the map finer, so a close-up still finds its details
MAP_NFEATURES = 6000
RATIO = 0.8             # Lowe's ratio test
MIN_INLIERS = 25        # fewer → the frame is not placed (another page, too blurred)
MAX_MAPS = 16           # live sessions kept at once
TTL_S = 30 * 60
WHOLE_PAGE = 0.6        # a frame covering this much of the map shows the whole page

log = logging.getLogger("matcher.pagemap")


@dataclass
class Seen:
    quad: np.ndarray    # 4×2 corners on the map (fractions)
    ids: list[str]      # its best candidates from the last frame that saw it, best first


@dataclass
class PageMap:
    xy: np.ndarray      # (N, 2) keypoints in pixels of the scaled map
    des: np.ndarray     # (N, 32) ORB descriptors
    w: int
    h: int
    used: float
    seen: list[Seen] = field(default_factory=list)   # cards earlier frames found on this page


@dataclass
class Located:
    h: np.ndarray       # 3×3, frame fractions → map fractions
    area: float         # share of the map the frame covers (1 ≈ same view)
    inliers: int

    @property
    def whole(self) -> bool:
        return self.area >= WHOLE_PAGE


_maps: OrderedDict[str, PageMap] = OrderedDict()
_lock = threading.Lock()


def _features(img: np.ndarray, side: int, nfeatures: int) -> tuple[np.ndarray, np.ndarray | None, int, int]:
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    k = min(1.0, side / max(gray.shape[:2]))
    if k < 1.0:
        gray = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    h, w = gray.shape[:2]
    orb = cv2.ORB_create(nfeatures=nfeatures, scaleFactor=1.2, nlevels=8, edgeThreshold=31, patchSize=31,
                         fastThreshold=12)
    kp, des = orb.detectAndCompute(gray, None)
    xy = np.float32([p.pt for p in kp]) if kp else np.zeros((0, 2), np.float32)
    return xy, des, w, h


def store(img: np.ndarray) -> str | None:
    """Keep this frame's features as a new map → its id (None: too little texture)."""
    xy, des, w, h = _features(img, MAP_SIDE, MAP_NFEATURES)
    if des is None or len(xy) < 4 * MIN_INLIERS:
        return None
    map_id = uuid.uuid4().hex
    now = time.monotonic()
    with _lock:
        for k in [k for k, m in _maps.items() if now - m.used > TTL_S]:
            del _maps[k]
        _maps[map_id] = PageMap(xy, des, w, h, now)
        while len(_maps) > MAX_MAPS:
            _maps.popitem(last=False)
    return map_id


def get(map_id: str) -> PageMap | None:
    with _lock:
        m = _maps.get(map_id)
        if m is None or time.monotonic() - m.used > TTL_S:
            _maps.pop(map_id, None)
            return None
        m.used = time.monotonic()
        _maps.move_to_end(map_id)
        return m


def _area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def locate(img: np.ndarray, m: PageMap) -> Located | None:
    """Where this frame lies on the map, or None if it can't be placed."""
    xy, des, w, h = _features(img, SIDE, NFEATURES)
    if des is None or len(xy) < MIN_INLIERS:
        return None
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des, m.des, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
    log.debug("locate: %d features, %d good matches (map %d features)", len(xy), len(good), len(m.xy))
    if len(good) < MIN_INLIERS:
        return None
    src = np.float32([xy[g.queryIdx] for g in good])
    dst = np.float32([m.xy[g.trainIdx] for g in good])
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    if H is None or mask is None or int(mask.sum()) < MIN_INLIERS:
        log.debug("locate: no consistent fit (%s inliers)", None if mask is None else int(mask.sum()))
        return None
    # pixels → fractions on both sides
    Hn = np.diag([1 / m.w, 1 / m.h, 1.0]) @ H @ np.diag([w, h, 1.0])
    corners = project([[0, 0], [1, 0], [1, 1], [0, 1]], Hn)
    if corners is None:
        return None
    c = np.float32(corners)
    def turn(a: np.ndarray, b: np.ndarray) -> float:
        return float(a[0] * b[1] - a[1] * b[0])
    cross = [turn(c[(i + 1) % 4] - c[i], c[(i + 2) % 4] - c[(i + 1) % 4]) for i in range(4)]
    area = _area(c)
    if not (all(x > 0 for x in cross) or all(x < 0 for x in cross)) or not 0.01 < area < 6:
        log.debug("locate: implausible fit (area %.3f)", area)
        return None     # folded or absurd: a wrong fit
    return Located(Hn / Hn[2, 2], area, int(mask.sum()))


def project(quad, H: np.ndarray) -> list[list[float]] | None:
    """Points (fractions) through H; None if one lands at infinity."""
    q = np.float64(quad).reshape(-1, 2)
    p = np.hstack([q, np.ones((len(q), 1))]) @ H.T
    if np.any(np.abs(p[:, 2]) < 1e-9):
        return None
    out = p[:, :2] / p[:, 2:3]
    if not np.all(np.isfinite(out)):
        return None
    return [[round(float(x), 4), round(float(y), 4)] for x, y in out]


MAX_SEEN = 64           # cards remembered per page


def remember(map_id: str, cards: list[tuple[list, list[str]]]) -> None:
    """
    Cards a frame found, with their corners on the map: kept so the next frames
    can check them again without the image model (see known()). A card where one
    is already remembered replaces it.
    """
    with _lock:
        m = _maps.get(map_id)
        if m is None:
            return
        for quad, ids in cards:
            q = np.float32(quad).reshape(4, 2)
            if not ids or not np.all(np.isfinite(q)):
                continue
            c = q.mean(axis=0)
            size = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0]))
            m.seen = [s for s in m.seen if np.linalg.norm(s.quad.mean(axis=0) - c) > 0.4 * size]
            m.seen.append(Seen(q, list(ids)[:5]))
        del m.seen[:-MAX_SEEN]


def known(map_id: str, h: np.ndarray) -> list[tuple[list[list[float]], list[str]]]:
    """The remembered cards in a frame that lies on the map by h (frame → map): (corners in the frame, ids)."""
    m = get(map_id)
    if m is None or not m.seen:
        return []
    try:
        back = np.linalg.inv(h)
    except np.linalg.LinAlgError:
        return []
    out = []
    for s in m.seen:
        q = project(s.quad, back)
        if q is not None:
            out.append((q, s.ids))
    return out


def clear() -> None:
    with _lock:
        _maps.clear()
