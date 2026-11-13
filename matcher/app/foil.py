"""
Foil in a scanned card: align the photo to the card's plain catalog image,
even out the lighting and subtract — what is left is mostly foil. A reverse
holo lights up the frame around the art box, a holo the art, a plain card
neither (if the photo caught the light).

Scarlet & Violet and Mega Evolution sets print the set code box inverted on
reverse holos: dark letters on a light box instead of light on dark (holos keep
it like the catalog image; on 860 scans, 11 of 12 sets). box_corr finds
that box in the catalog image and compares just it with the photo: ≈ +1 the
same print, ≈ −1 the negative of it, None when the photo's corner is too
blurred, dark or washed out to see it. (code_corr, the lowest correlation of
any small window in that corner, reacts to foil texture there too — but also to
sharpening halos and smeared edges, so it is only a model feature, never proof.)

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
FEATURES = ["log_art", "log_frame", "log_frame_art", "code_corr", "box_corr"]   # all a model may use
# set code box: bottom left (regulation mark, set code, number) — searched with
# windows about the size of the box, where the catalog image has print
CODE_BAND = (0.02, 0.88, 0.48, 0.99)          # x0, y0, x1, y1 (card fractions)
CODE_WIN = (60, 30)                           # window (px at W×H)
CODE_STEP = 6
# the set code box itself (e.g. "SCR EN"), found in the catalog image on the set code row
BOX_BAND = (0.03, 0.90, 0.40, 0.99)
BOX_W, BOX_H = range(30, 57, 2), range(16, 31, 2)   # px at W×H (the box is ≈ 42×25)
BOX_RING = 4                                  # what is around it, px
BOX_SHIFT = 3                                 # alignment slack, px
BOX_SEEN = 0.35                               # the photo's box needs this much of the catalog's contrast

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


def _rect(ii: np.ndarray, xa, ya, xb, yb):
    return ii[yb, xb] - ii[ya, xb] - ii[yb, xa] + ii[ya, xa]


def find_box(ref: np.ndarray) -> tuple[int, int, int, int] | None:
    """
    The set code box in a catalog image (W×H): a box-sized rectangle on the set
    code row that is mostly one tone (dark, or light) with letters in it, while
    what is around it isn't. → (x0, y0, x1, y1) px, or None (older cards have none).
    """
    g = cv2.GaussianBlur(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), (0, 0), 0.8)
    x0, y0, x1, y1 = (int(BOX_BAND[0] * W), int(BOX_BAND[1] * H), int(BOX_BAND[2] * W), int(BOX_BAND[3] * H))
    t, _ = cv2.threshold(g[y0:y1, x0:x1], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    gf = g.astype(np.float64)
    gi = cv2.integral(gf)
    r = BOX_RING
    ys, xs = np.mgrid[y0 + r:y1 - r, x0 + r:x1 - r]
    best: tuple[float, tuple[int, int, int, int] | None] = (0.0, None)
    for tone in (g < t, g > t):
        tf = tone.astype(np.float64)
        ti, tgi = cv2.integral(tf), cv2.integral(tf * gf)
        for h in BOX_H:
            for w in BOX_W:
                ok = (ys + h + r <= y1) & (xs + w + r <= x1)
                ya, xa = ys[ok], xs[ok]
                if ya.size == 0:
                    continue
                xo, yo, xe, ye = xa - r, ya - r, xa + w + r, ya + h + r
                n_in, n_ring = w * h, (w + 2 * r) * (h + 2 * r) - w * h
                f_in = _rect(ti, xa, ya, xa + w, ya + h)
                frac, frac_ring = f_in / n_in, (_rect(ti, xo, yo, xe, ye) - f_in) / n_ring
                g_in = _rect(gi, xa, ya, xa + w, ya + h)
                ring_mean = (_rect(gi, xo, yo, xe, ye) - g_in) / n_ring
                tone_mean = _rect(tgi, xa, ya, xa + w, ya + h) / np.maximum(f_in, 1)
                good = (frac >= 0.5) & (frac <= 0.85) & (np.abs(tone_mean - ring_mean) >= 50)
                score = np.where(good, frac - frac_ring, 0)
                k = int(np.argmax(score))
                if score[k] > best[0]:
                    best = (float(score[k]), (int(xa[k]), int(ya[k]), int(xa[k]) + w, int(ya[k]) + h))
    return best[1] if best[0] >= 0.45 else None


def box_corr(photo: np.ndarray, ref: np.ndarray) -> tuple[float | None, tuple[int, int, int, int] | None]:
    """
    The set code box of the photo (aligned, W×H) against the catalog image's:
    ≈ +1 the same print, ≈ −1 the negative of it. → (correlation, box px) —
    correlation None when there is no box or the photo is too blurred, dark or
    washed out there to tell.
    """
    b = find_box(ref)
    if b is None:
        return None, None
    s, r = BOX_SHIFT, BOX_RING - 1
    x0, y0, x1, y1 = b[0] - r, b[1] - r, b[2] + r, b[3] + r
    if x0 - s < 0 or y0 - s < 0 or x1 + s > W or y1 + s > H:
        return None, b
    gp = cv2.GaussianBlur(cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY), (0, 0), 1.2).astype(np.float32)
    gr = cv2.GaussianBlur(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), (0, 0), 1.2).astype(np.float32)
    tpl, area = gr[y0:y1, x0:x1], gp[y0 - s:y1 + s, x0 - s:x1 + s]
    if float(area[s:-s, s:-s].std()) < BOX_SEEN * float(tpl.std()):
        return None, b

    def edges(im: np.ndarray) -> np.ndarray:
        return cv2.magnitude(cv2.Sobel(im, cv2.CV_32F, 1, 0), cv2.Sobel(im, cv2.CV_32F, 0, 1))

    # where the photo's box sits (edges look the same whichever way it is printed), then which way it is
    at = cv2.matchTemplate(edges(area), edges(tpl), cv2.TM_CCOEFF_NORMED)
    dy, dx = np.unravel_index(int(np.argmax(at)), at.shape)
    i = r + 2                                  # just the inside: letters on the box, whatever the card around it
    a = area[dy + i:dy + tpl.shape[0] - i, dx + i:dx + tpl.shape[1] - i]
    c = tpl[i:-i, i:-i]
    if float(a.std()) < 1e-3 or float(c.std()) < 1e-3:
        return None, b
    return round(float(np.corrcoef(a.ravel(), c.ravel())[0, 1]), 3), b


def measure(photo: np.ndarray, ref: np.ndarray) -> dict | None:
    """Photo vs catalog image (both W×H) → {"art", "frame", "code_corr", "box_corr", "inliers"} or None if they don't align."""
    warped, inl = align(photo, ref)
    if warped is None:
        return None
    art, frame = regions(difference(match_light(warped, ref), ref))
    return {"art": round(art, 2), "frame": round(frame, 2), "code_corr": code_corr(warped, ref),
            "box_corr": box_corr(warped, ref)[0], "inliers": inl}


def feature_values(art: float, frame: float, code: float | None = None, box: float | None = None) -> dict[str, float]:
    """What a model may see (tools/foil_eval.py uses the same). Nothing to compare = same print."""
    a, f = max(float(art), 0.1), max(float(frame), 0.1)
    return {"log_art": float(np.log(a)), "log_frame": float(np.log(f)), "log_frame_art": float(np.log(f / a)),
            "code_corr": 1.0 if code is None else float(code), "box_corr": 1.0 if box is None else float(box)}


def features(art: float, frame: float, code: float | None = None, names: list[str] | None = None,
             box: float | None = None) -> list[float]:
    v = feature_values(art, frame, code, box)
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


def judge(m: dict, art: float, frame: float, code: float | None = None, box: float | None = None) -> dict:
    """Reverse-holo score (logit, Reverse Holo vs Normal) and whether it is sure enough to ask."""
    x = (np.array(features(art, frame, code, m["features"], box)) - np.array(m["mu"])) / np.array(m["sd"])
    z = np.append(x, 1.0) @ np.array(m["w"])
    score = float(z[1] - z[0])
    return {"score": round(score, 3), "hint": score >= float(m["threshold"])}
