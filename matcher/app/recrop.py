"""
Better corners for a saved scan photo, in its original photo.

The app stores, per scanned card, the straightened crop, the whole original
photo and the corners it cut along (normalized, but in whatever order the app
used — the crop may also have been flipped or turned). verify_card() tells
where the card really lies inside the crop; this maps that outline back into
the original, after finding which corner order produced the crop.
"""
from __future__ import annotations

import cv2
import numpy as np

_SMALL = (48, 67)        # comparison size (portrait card)
MIN_SIMILARITY = 0.5     # below this no corner order reproduces the crop (other photo, edited elsewhere)


def _norm_small(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.resize(g, _SMALL, interpolation=cv2.INTER_AREA).astype(np.float32)
    g -= g.mean()
    n = float(np.linalg.norm(g))
    return g / n if n else g


def crop_mapping(original: np.ndarray, crop: np.ndarray, quad_px: np.ndarray) -> tuple[np.ndarray | None, float]:
    """Homography crop px → original px, trying all 8 corner orders (4 turns × mirror)."""
    h, w = crop.shape[:2]
    rect = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    target = _norm_small(crop)
    q = np.float32(quad_px)
    best, best_sim = None, -1.0
    for base in (q, q[::-1].copy()):
        for r in range(4):
            order = np.roll(base, -r, axis=0)
            m = cv2.getPerspectiveTransform(rect, order)
            try:
                inv = np.linalg.inv(m)
            except np.linalg.LinAlgError:
                continue
            warped = cv2.warpPerspective(original, inv, (w, h))
            sim = float((_norm_small(warped) * target).sum())
            if sim > best_sim:
                best, best_sim = m, sim
    return (best if best_sim >= MIN_SIMILARITY else None), round(best_sim, 3)


def suggest_quad(original: np.ndarray, crop: np.ndarray, quad_norm: list, outline_norm: list) -> dict:
    """Card outline (fractions of the crop) → corners in the original (fractions), card's TL first."""
    oh, ow = original.shape[:2]
    ch, cw = crop.shape[:2]
    quad_px = np.float32(quad_norm) * [ow, oh]
    m, sim = crop_mapping(original, crop, quad_px)
    if m is None:
        return {"suggested_quad": None, "crop_similarity": sim}
    pts = np.float32(outline_norm) * [cw, ch]
    mapped = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), m).reshape(4, 2) / [ow, oh]
    mapped = np.clip(mapped, 0.0, 1.0)
    return {"suggested_quad": mapped.round(4).tolist(), "crop_similarity": sim}
