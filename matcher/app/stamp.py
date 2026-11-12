"""
Play! Pokémon stamp (Prize Pack cards) in a straightened card photo.

The template is learned from the collection itself, never shipped:
tools/stamp_template.py averages the stamps the user marked (card page →
"Play! stamp") and, for prize-pack cards nobody marked yet, the spot where the
photo differs from the unstamped catalog image. A few self-training rounds
re-align every sample to the current mean and drop the ones that don't fit.
The result lives in the data volume (stamp_template.png + .json).

find() runs normalised correlation of that mean over the two places the stamp
sits (bottom corners of the artwork, left or right) or around a hint, at a
few scales, on desaturated, contrast-normalised greys (foil glare and colour
shifts matter less).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
TEMPLATE_PATH = DATA_DIR / "stamp_template.png"
W, H = 460, 643                              # card size everything runs at
BOX = (0.17, 0.09)                           # stamp size (card fractions) before a template exists
ZONES = ((0.0, 0.28, 0.34, 0.68), (0.66, 0.28, 1.0, 0.68))   # x0, y0, x1, y1: left / right of the art
SCALES = np.arange(0.84, 1.20, 0.04)
MIN_SCORE = 0.75                             # stamps score ~0.9+, other artwork ≤ ~0.65
MARGIN = 0.35                                # crop margin around a sample, of the stamp size


def gray(img: np.ndarray) -> np.ndarray:
    """Card at W×H, desaturated, local contrast evened out (glare, lighting)."""
    if img.ndim == 3:
        img = cv2.cvtColor(img[:, :, :3] if img.shape[2] == 4 else img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
    return g.astype(np.float32)


def meta_path(path: Path) -> Path:
    return Path(path).with_suffix(".json")


_cache: dict[str, tuple[float, np.ndarray, dict]] = {}


def load_template(path: Path = TEMPLATE_PATH) -> tuple[np.ndarray, dict] | None:
    p = Path(path)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(str(p))
    if hit and hit[0] == mtime:
        return hit[1], hit[2]
    t = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if t is None:
        return None
    try:
        meta = json.loads(meta_path(p).read_text())
    except (OSError, ValueError):
        meta = {}
    meta.setdefault("w", BOX[0])
    meta.setdefault("h", BOX[1])
    _cache[str(p)] = (mtime, t.astype(np.float32), meta)
    return t.astype(np.float32), meta


def _best(g: np.ndarray, t: np.ndarray, size: tuple[float, float],
          zones: list[tuple[float, float, float, float]]) -> tuple[float, tuple[int, int, int, int]] | None:
    best = None
    for sc in SCALES:
        tw, th = max(8, int(round(size[0] * W * sc))), max(6, int(round(size[1] * H * sc)))
        tt = cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA)
        for x0, y0, x1, y1 in zones:
            X0, Y0, X1, Y1 = int(x0 * W), int(y0 * H), int(np.ceil(x1 * W)), int(np.ceil(y1 * H))
            win = g[max(0, Y0):min(H, Y1), max(0, X0):min(W, X1)]
            if win.shape[0] <= th or win.shape[1] <= tw:
                continue
            _, v, _, loc = cv2.minMaxLoc(cv2.matchTemplate(win, tt, cv2.TM_CCOEFF_NORMED))
            if best is None or v > best[0]:
                best = (float(v), (max(0, X0) + loc[0], max(0, Y0) + loc[1], tw, th))
    return best


def find(card: np.ndarray, hint: list | None = None, path: Path = TEMPLATE_PATH) -> dict | None:
    """
    → {"quad": 4 corners (card fractions, TL first), "score"} or None without a
    template. `hint` (4 corners, fractions): look around there instead of both zones.
    """
    tpl = load_template(path)
    if tpl is None:
        return None
    t, meta = tpl
    g = gray(card)
    size = (float(meta["w"]), float(meta["h"]))
    zones = list(ZONES)
    if hint:
        h = np.float32(hint)
        cx, cy = h[:, 0].mean(), h[:, 1].mean()
        zones = [(cx - 1.2 * size[0], cy - 1.4 * size[1], cx + 1.2 * size[0], cy + 1.4 * size[1])]
    best = _best(g, t, size, zones)
    if best is None:
        return None
    v, (x, y, tw, th) = best
    x0, y0, x1, y1 = x / W, y / H, (x + tw) / W, (y + th) / H
    quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return {"quad": [[round(a, 4), round(b, 4)] for a, b in quad], "score": round(v, 3)}


# ── learning the template ───────────────────────────────────────────────────

def difference_box(card: np.ndarray, ref: np.ndarray) -> tuple[tuple[float, float, float, float], float]:
    """
    Where a stamped photo differs most from the unstamped catalog image, in the
    stamp zones: a stamp-sized window. → ((x0, y0, x1, y1) fractions, strength).
    """
    def lab(im):
        if im.ndim == 3 and im.shape[2] == 4:
            a = im[:, :, 3:] / 255.0
            im = (im[:, :, :3] * a + 128 * (1 - a)).astype(np.uint8)
        im = cv2.resize(im, (W, H), interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(cv2.GaussianBlur(im, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    a, b = lab(card), lab(ref)
    for k in range(3):
        b[:, :, k] = (b[:, :, k] - b[:, :, k].mean()) / (b[:, :, k].std() + 1e-6) * a[:, :, k].std() + a[:, :, k].mean()
    d = np.sqrt(((a - b) ** 2).sum(2))
    bw, bh = int(BOX[0] * W), int(BOX[1] * H)
    s = cv2.boxFilter(d, -1, (bw, bh))
    mask = np.zeros(s.shape, bool)
    for x0, y0, x1, y1 in ZONES:
        mask[int((y0 + BOX[1] / 2) * H):int((y1 - BOX[1] / 2) * H), int((x0 + BOX[0] / 2) * W):int((x1 - BOX[0] / 2) * W)] = True
    s[~mask] = 0
    y, x = np.unravel_index(int(np.argmax(s)), s.shape)
    strength = float(s[y, x]) / (float(np.median(d)) + 1e-6)
    return (x / W - BOX[0] / 2, y / H - BOX[1] / 2, x / W + BOX[0] / 2, y / H + BOX[1] / 2), strength


def _crop(g: np.ndarray, box: tuple[float, float, float, float], out: tuple[int, int]) -> np.ndarray:
    """The box plus MARGIN around it, resized to `out` (template size incl. margin)."""
    x0, y0, x1, y1 = box
    mw, mh = (x1 - x0) * MARGIN, (y1 - y0) * MARGIN
    src = np.float32([[(x0 - mw) * W, (y0 - mh) * H], [(x1 + mw) * W, (y0 - mh) * H], [(x0 - mw) * W, (y1 + mh) * H]])
    dst = np.float32([[0, 0], [out[0], 0], [0, out[1]]])
    return cv2.warpAffine(g, cv2.getAffineTransform(src, dst), out, flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)


def _tighten(grays, boxes, use, full, size_px):
    """Each side of the boxes onto the stamp's outline: the strongest edge in the averaged crops near it."""
    crops = [_crop(g, b, full) for g, b, u in zip(grays, boxes, use, strict=True) if u]
    if len(crops) < 2:
        return boxes
    m = np.mean(crops, axis=0)
    gx = np.abs(cv2.Sobel(m, cv2.CV_32F, 1, 0))
    gy = np.abs(cv2.Sobel(m, cv2.CV_32F, 0, 1))
    tw, th = size_px
    mx, my = full[0] * MARGIN / (1 + 2 * MARGIN), full[1] * MARGIN / (1 + 2 * MARGIN)
    rows = slice(int(my + 0.2 * th), int(my + 0.8 * th))
    cols = slice(int(mx + 0.2 * tw), int(mx + 0.8 * tw))
    colp, rowp = gx[rows].mean(0), gy[:, cols].mean(1)

    def peak(prof, at, span):
        lo, hi = max(0, int(at - span)), min(len(prof), int(at + span) + 1)
        return lo + int(np.argmax(prof[lo:hi]))
    sx, sy = 0.15 * tw, 0.15 * th
    left, right = peak(colp, mx, sx), peak(colp, mx + tw, sx)
    top, bottom = peak(rowp, my, sy), peak(rowp, my + th, sy)
    if right - left < 0.6 * tw or bottom - top < 0.6 * th:
        return boxes
    # crop pixels → fractions of each box (the outline itself stays inside: +-1 px)
    fl, fr = (left - 1 - mx) / tw, (right + 1 - mx) / tw
    ft, fb = (top - 1 - my) / th, (bottom + 1 - my) / th
    return [(x0 + fl * (x1 - x0), y0 + ft * (y1 - y0), x0 + fr * (x1 - x0), y0 + fb * (y1 - y0))
            for x0, y0, x1, y1 in boxes]


def build(samples: list[tuple[np.ndarray, tuple[float, float, float, float], bool]],
          rounds: int = 4, keep: float = 0.35) -> tuple[np.ndarray, dict] | None:
    """
    samples: (card photo, rough stamp box (fractions), marked by hand?).
    Self-training: mean of the crops → re-find each sample near its box with
    the mean → mean of the samples that fit (score ≥ keep; hand marks always
    count). → (template, meta {"w","h","samples","marked"}).
    """
    if not samples:
        return None
    grays = [gray(c) for c, _, _ in samples]
    boxes = [tuple(b) for _, b, _ in samples]
    marked = [m for _, _, m in samples]
    size = (float(np.median([b[2] - b[0] for b in boxes])), float(np.median([b[3] - b[1] for b in boxes])))
    tw, th = max(16, int(round(size[0] * W))), max(12, int(round(size[1] * H)))
    full = (int(round(tw * (1 + 2 * MARGIN))), int(round(th * (1 + 2 * MARGIN))))
    inner = (slice(int(th * MARGIN), int(th * MARGIN) + th), slice(int(tw * MARGIN), int(tw * MARGIN) + tw))
    use = [True] * len(samples)
    scores = [0.0] * len(samples)
    for _ in range(rounds):
        crops = [_crop(g, b, full) for g, b in zip(grays, boxes, strict=True)]
        mean = np.mean([c for c, u in zip(crops, use, strict=True) if u], axis=0)
        t = mean[inner]
        for i, g in enumerate(grays):
            if marked[i]:
                use[i] = True
                continue
            x0, y0, x1, y1 = boxes[i]
            zone = (x0 - (x1 - x0) * 0.6, y0 - (y1 - y0) * 0.8, x1 + (x1 - x0) * 0.6, y1 + (y1 - y0) * 0.8)
            best = _best(g, t, size, [zone])
            if best is None:
                use[i] = False
                continue
            v, (x, y, w_, h_) = best
            scores[i] = v
            boxes[i] = (x / W, y / H, (x + w_) / W, (y + h_) / H)
            use[i] = v >= keep
        if sum(use) < 2:
            use = [True] * len(samples)
    boxes = _tighten(grays, boxes, use, full, (tw, th))
    crops = [_crop(g, b, full) for g, b in zip(grays, boxes, strict=True)]
    kept = [c for c, u in zip(crops, use, strict=True) if u]
    mean = np.mean(kept, axis=0)[inner]
    size = (float(np.median([b[2] - b[0] for b, u in zip(boxes, use, strict=True) if u])),
            float(np.median([b[3] - b[1] for b, u in zip(boxes, use, strict=True) if u])))
    meta = {"w": round(size[0], 4), "h": round(size[1], 4), "samples": len(kept), "marked": sum(marked),
            "found": [{"box": [round(v, 4) for v in b], "score": round(s, 3), "used": u}
                      for b, s, u in zip(boxes, scores, use, strict=True)]}
    return np.clip(mean, 0, 255).astype(np.uint8), meta


def save(template: np.ndarray, meta: dict, path: Path = TEMPLATE_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), template)
    m = {k: v for k, v in meta.items() if k != "found"}
    meta_path(path).write_text(json.dumps(m, indent=1))
    _cache.pop(str(path), None)
