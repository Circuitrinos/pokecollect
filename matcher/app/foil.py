"""
Foil in a scanned card: align the photo to the card's plain catalog image,
even out the lighting and subtract — what is left is mostly foil. A reverse
holo lights up the frame around the art box, a holo the art, a plain card
neither (if the photo caught the light).

Newer sets (Mega Evolution era, e.g. Chaos Rising) also print the set code box
inverted on foil cards (holo and reverse holo): dark letters on a light box
instead of light on dark. That doesn't depend on the light at all — code_corr
compares the set-code corner of the photo with the catalog image: ≈ +1 the
same print, ≈ −1 the negative of it.

tools/holo_diff.py runs this over the scan dataset, tools/foil_eval.py tests
how well the numbers separate Normal from Reverse Holo on unseen cards and
saves a small model (foil_model.json, in the data volume); with a model the
scan asks "reverse holo?" for plain cards whose print exists as reverse holo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MODEL_PATH = DATA_DIR / "foil_model.json"
W, H = 630, 880
# Art box of a modern card (fractions of the card) — rough, era-dependent
ART = (0.08, 0.11, 0.92, 0.55)
FEATURES = ["log_art", "log_frame", "log_frame_art", "code_corr"]   # all a model may use
# set code box: bottom left (regulation mark, set code, number) — searched with
# windows about the size of the box, where the catalog image has print
CODE_BAND = (0.02, 0.88, 0.48, 0.99)          # x0, y0, x1, y1 (card fractions)
CODE_WIN = (60, 30)                           # window (px at W×H)
CODE_STEP = 6

_orb = cv2.ORB_create(nfeatures=3000)
_bf = cv2.BFMatcher(cv2.NORM_HAMMING)


def decode(data: bytes | None) -> np.ndarray | None:
    if not data:
        return None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return None if img is None else cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)


def align(photo: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray | None, int]:
    """Warp the photo onto the reference (homography from ORB matches). → (warped, inliers)."""
    g1, g2 = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY), cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    k1, d1 = _orb.detectAndCompute(g1, None)
    k2, d2 = _orb.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        return None, 0
    good = [m for m, n in (p for p in _bf.knnMatch(d1, d2, k=2) if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return None, len(good)
    src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    hom, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    inl = int(mask.sum()) if mask is not None else 0
    if hom is None or inl < 12:
        return None, inl
    # Sub-pixel refinement (ECC): leftover misalignment shows up as bright edges
    try:
        hom = hom.astype(np.float32)
        _, hom = cv2.findTransformECC(cv2.GaussianBlur(g2, (5, 5), 0), cv2.GaussianBlur(g1, (5, 5), 0),
                                      np.linalg.inv(hom).astype(np.float32), cv2.MOTION_HOMOGRAPHY,
                                      (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), None, 5)
        warped = cv2.warpPerspective(photo, hom, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                     borderMode=cv2.BORDER_REPLICATE)
    except cv2.error:
        warped = cv2.warpPerspective(photo, hom, (W, H), borderMode=cv2.BORDER_REPLICATE)
    return warped, inl


def match_light(photo: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Per-channel mean/std of the photo (Lab) set to the reference's — glare stays, lighting goes."""
    a = cv2.cvtColor(photo, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
    for ch in range(3):
        sa, sb = a[..., ch].std() or 1.0, b[..., ch].std()
        a[..., ch] = (a[..., ch] - a[..., ch].mean()) * (sb / sa) + b[..., ch].mean()
    return cv2.cvtColor(np.clip(a, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def difference(photo: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """
    Colour difference (Lab distance), 0..255, where the print is flat: both
    images get the same softness first, and differences on the reference's
    edges (text, outlines — left over from alignment and blur) are damped, so
    what remains is mostly foil texture and glare.
    """
    a = cv2.cvtColor(cv2.GaussianBlur(photo, (0, 0), 1.4), cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(cv2.GaussianBlur(ref, (0, 0), 1.4), cv2.COLOR_BGR2LAB).astype(np.float32)
    d = np.sqrt(((a - b) ** 2).sum(axis=2))
    g = cv2.cvtColor(cv2.GaussianBlur(ref, (0, 0), 1.4), cv2.COLOR_BGR2GRAY).astype(np.float32)
    edge = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    edge = cv2.dilate(edge, np.ones((7, 7), np.uint8))
    d = d / (1.0 + edge / 20.0)
    return np.clip(d * 3.0, 0, 255).astype(np.uint8)


def regions(diff: np.ndarray) -> tuple[float, float]:
    """Mean difference inside the art box and in the frame around it (card border trimmed)."""
    x0, y0, x1, y1 = (int(ART[0] * W), int(ART[1] * H), int(ART[2] * W), int(ART[3] * H))
    art = diff[y0:y1, x0:x1]
    inner = diff[int(0.04 * H):int(0.96 * H), int(0.05 * W):int(0.95 * W)].astype(np.float64)
    frame_sum = inner.sum() - art.astype(np.float64).sum()
    frame_n = inner.size - art.size
    return float(art.mean()), float(frame_sum / max(1, frame_n))


def code_corr(photo: np.ndarray, ref: np.ndarray) -> float | None:
    """
    Set code box of the aligned photo against the catalog image: the lowest
    correlation of the windows in the set-code corner where the catalog image
    has print. ≈ +1 same print, clearly negative = inverted (foil print of a
    newer set). None when the corner has no print to compare.
    """
    gp = cv2.GaussianBlur(cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY), (0, 0), 1.0).astype(np.float32)
    gr = cv2.GaussianBlur(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), (0, 0), 1.0).astype(np.float32)
    x0, y0, x1, y1 = (int(CODE_BAND[0] * W), int(CODE_BAND[1] * H), int(CODE_BAND[2] * W), int(CODE_BAND[3] * H))
    ww, wh = CODE_WIN
    best = None
    for y in range(y0, y1 - wh + 1, CODE_STEP):
        for x in range(x0, x1 - ww + 1, CODE_STEP):
            r = gr[y:y + wh, x:x + ww]
            if r.std() < 22:                      # no print here in the catalog image
                continue
            p = gp[y:y + wh, x:x + ww]
            if p.std() < 3:                       # flat in the photo (glare): no evidence either way
                continue
            c = float(np.corrcoef(r.ravel(), p.ravel())[0, 1])
            if best is None or c < best:
                best = c
    return None if best is None else round(best, 3)


def measure(photo: np.ndarray, ref: np.ndarray) -> dict | None:
    """Photo vs catalog image (both W×H) → {"art", "frame", "code_corr", "inliers"} or None if they don't align."""
    warped, inl = align(photo, ref)
    if warped is None:
        return None
    art, frame = regions(difference(match_light(warped, ref), ref))
    return {"art": round(art, 2), "frame": round(frame, 2), "code_corr": code_corr(warped, ref), "inliers": inl}


def feature_values(art: float, frame: float, code: float | None = None) -> dict[str, float]:
    """What a model may see (tools/foil_eval.py uses the same). No set code to compare = same print."""
    a, f = max(float(art), 0.1), max(float(frame), 0.1)
    return {"log_art": float(np.log(a)), "log_frame": float(np.log(f)), "log_frame_art": float(np.log(f / a)),
            "code_corr": 1.0 if code is None else float(code)}


def features(art: float, frame: float, code: float | None = None, names: list[str] | None = None) -> list[float]:
    v = feature_values(art, frame, code)
    return [v[n] for n in (names or FEATURES)]


# ── model (tools/foil_eval.py --save) ───────────────────────────────────────

_cache: dict[str, tuple[float, dict]] = {}


def valid_model(m) -> bool:
    """A model from foil_eval.py --save: any subset of FEATURES (older models lack code_corr)."""
    try:
        names = m.get("features")
        n = len(names)
        return (isinstance(m, dict) and n > 0 and all(f in FEATURES for f in names) and len(m["mu"]) == n
                and len(m["sd"]) == n and len(m["w"]) == n + 1 and all(len(r) == 2 for r in m["w"])
                and isinstance(m["threshold"], (int, float)))
    except (AttributeError, KeyError, TypeError):
        return False


def load_model(path: Path = MODEL_PATH) -> dict | None:
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(str(p))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        m = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if not valid_model(m):
        return None
    _cache[str(p)] = (mtime, m)
    return m


def save_model(m: dict, path: Path = MODEL_PATH) -> None:
    if not valid_model(m):
        raise ValueError("not a foil model from tools/foil_eval.py --save")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(m, indent=1))
    _cache.pop(str(path), None)


def judge(m: dict, art: float, frame: float, code: float | None = None) -> dict:
    """Reverse-holo score (logit, Reverse Holo vs Normal) and whether it is sure enough to ask."""
    x = (np.array(features(art, frame, code, m["features"])) - np.array(m["mu"])) / np.array(m["sd"])
    z = np.append(x, 1.0) @ np.array(m["w"])
    score = float(z[1] - z[0])
    return {"score": round(score, 3), "hint": score >= float(m["threshold"])}
