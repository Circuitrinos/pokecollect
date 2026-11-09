"""
Card image features — must stay byte-compatible with the PokemonScanner index
(card_index1.pkl, version 2): ORB 800 features on an 88 % center crop, a 63-bit
DCT pHash packed MSB-first, and a 16x4x4 HSV histogram. Changing any parameter
here makes new features incomparable with the converted index.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

NFEATURES = 800
CROP_FRAC = 0.88
HIST_BINS = (16, 4, 4)


@dataclass
class Features:
    kp_xy: np.ndarray   # (N, 2) float32
    des: np.ndarray     # (N, 32) uint8
    phash: int          # 63-bit pHash, packed MSB-first
    hist: np.ndarray    # (256,) float32


def center_crop(img: np.ndarray, frac: float = CROP_FRAC) -> np.ndarray:
    h, w = img.shape[:2]
    nw, nh = int(w * frac), int(h * frac)
    x0, y0 = (w - nw) // 2, (h - nh) // 2
    return img[y0:y0 + nh, x0:x0 + nw]


def orb_detector(nfeatures: int = NFEATURES) -> cv2.ORB:
    return cv2.ORB_create(nfeatures=nfeatures, scaleFactor=1.2, nlevels=8,
                          edgeThreshold=31, patchSize=31)


def phash63(img_bgr: np.ndarray) -> int:
    """DCT pHash: 8x8 low frequencies of a 32x32 gray image, DC dropped (63 bits)."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(small))
    rest = dct[:8, :8].flatten()[1:]
    bits = rest > rest.mean()
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def hsv_hist(img_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, list(HIST_BINS), [0, 180, 0, 256, 0, 256])
    return cv2.normalize(hist, None).flatten().astype(np.float32)


def extract(card_bgr: np.ndarray, min_keypoints: int = 10) -> Features | None:
    """Features of an upright card image (template scan or straightened photo)."""
    if card_bgr is None or card_bgr.size == 0:
        return None
    crop = center_crop(card_bgr)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    kps, des = orb_detector().detectAndCompute(gray, None)
    if des is None or len(kps) < min_keypoints:
        return None
    return Features(
        kp_xy=np.float32([kp.pt for kp in kps]),
        des=np.ascontiguousarray(des, dtype=np.uint8),
        phash=phash63(crop),
        hist=hsv_hist(crop),
    )
