"""
Generic card finder: "a Pokémon card", not "this exact card".

A mean card frame — the desaturated gradient map averaged over many catalog
images, so every artwork washes out and what stays is what all cards share:
border, name bar, HP, attack box, weakness line, bottom text — is fitted to the
picture. The artwork window and everything outside the card are left out of
the fit (they would pull the template onto the art box, a sleeve or the
binder pocket).

1. coarse search: scale × rotation × translation by normalised correlation
   around the starting corners (rough user corners or a detector candidate)
2. fine fit: ECC homography on the gradient maps, two pyramid levels
3. score: the ECC correlation (≈0.3+ is a card frame, lower is a guess)

tools/frame_template.py rebuilds app/frame_template.png from catalog images.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

TEMPLATE_PATH = Path(__file__).with_name("frame_template.png")
CW, CH, PAD = 240, 336, 16                  # card size in the template, padding around it
ART = (0.09, 0.91, 0.12, 0.50)              # artwork window: x0, x1, y0, y1 (card fractions)
MIN_SCORE = 0.3
SEARCH_SCALES = np.arange(0.82, 1.21, 0.03)
SEARCH_ANGLES = np.arange(-6.0, 6.1, 1.5)
SEARCH_SHIFT = 0.15                         # of the card width, around the start
CARD = np.float32([[PAD, PAD], [PAD + CW, PAD], [PAD + CW, PAD + CH], [PAD, PAD + CH]])


def gradient(img: np.ndarray) -> np.ndarray:
    """Desaturated (luminance) gradient magnitude, normalised to the 95th percentile."""
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = g.astype(np.float32)
    m = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    p = float(np.percentile(m, 95)) or 1.0
    return np.clip(m / p, 0, 1.5).astype(np.float32)


def build_template(images) -> np.ndarray | None:
    """Mean gradient map (CH×CW) of upright, tightly cropped card images."""
    acc = np.zeros((CH, CW), np.float32)
    n = 0
    for im in images:
        if im is None or im.size == 0:
            continue
        if im.ndim == 3 and im.shape[2] == 4:
            im = cv2.cvtColor(im, cv2.COLOR_BGRA2BGR)
        acc += gradient(cv2.resize(im, (CW, CH), interpolation=cv2.INTER_AREA))
        n += 1
    if not n:
        return None
    mean = acc / n
    mean[:2, :] = mean[-2:, :] = 0          # the image border (transparent rounded corners)
    mean[:, :2] = mean[:, -2:] = 0
    return mean


def save_template(mean: np.ndarray, path: Path = TEMPLATE_PATH) -> None:
    cv2.imwrite(str(path), np.clip(mean / max(float(mean.max()), 1e-6) * 255, 0, 255).astype(np.uint8))


@lru_cache(maxsize=4)
def _template(path: str = str(TEMPLATE_PATH)) -> tuple[np.ndarray, float, np.ndarray] | None:
    mean = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if mean is None:
        return None
    mean = cv2.resize(mean, (CW, CH), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    t = np.zeros((CH + 2 * PAD, CW + 2 * PAD), np.float32)
    t[PAD:PAD + CH, PAD:PAD + CW] = mean
    t = cv2.GaussianBlur(t, (0, 0), 1.0)
    keep = np.zeros(t.shape, bool)
    keep[PAD:PAD + CH, PAD:PAD + CW] = True
    keep[PAD + int(ART[2] * CH):PAD + int(ART[3] * CH), PAD + int(ART[0] * CW):PAD + int(ART[1] * CW)] = False
    fill = float(t[keep].mean())
    t[~keep] = fill                          # neither pulls nor pushes the fit
    return t.astype(np.float32), fill, keep


def available() -> bool:
    return _template() is not None


def _ecc(t: np.ndarray, g: np.ndarray, h: np.ndarray) -> tuple[float | None, np.ndarray]:
    try:
        cc, w = cv2.findTransformECC(t, g, h.astype(np.float32), cv2.MOTION_HOMOGRAPHY,
                                     (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), None, 3)
        return float(cc), w.astype(np.float64)
    except cv2.error:
        return None, h


def _search(t: np.ndarray, fill: float, g: np.ndarray, start: np.ndarray, top: int) -> list[np.ndarray]:
    """Best few similarity placements of the template near `start` (template→picture homographies)."""
    f = 0.5
    gs = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    c = start.mean(0) * f
    a0 = float(np.degrees(np.arctan2(start[1][1] - start[0][1], start[1][0] - start[0][0])))
    rad = SEARCH_SHIFT * CW * f
    hyps = []
    for sc in SEARCH_SCALES:
        k = float(sc) * f
        tw, th = int(t.shape[1] * k * 1.08) | 1, int(t.shape[0] * k * 1.08) | 1
        x0, y0 = int(max(0, c[0] - tw / 2 - rad)), int(max(0, c[1] - th / 2 - rad))
        x1, y1 = int(min(gs.shape[1], c[0] + tw / 2 + rad)), int(min(gs.shape[0], c[1] + th / 2 + rad))
        win = gs[y0:y1, x0:x1]
        if win.shape[0] <= th or win.shape[1] <= tw:
            continue
        for a in a0 + SEARCH_ANGLES:
            m = cv2.getRotationMatrix2D((t.shape[1] / 2, t.shape[0] / 2), -float(a), k)
            m[0, 2] += tw / 2 - t.shape[1] / 2
            m[1, 2] += th / 2 - t.shape[0] / 2
            tt = cv2.warpAffine(t, m, (tw, th), borderValue=fill)
            _, v, _, loc = cv2.minMaxLoc(cv2.matchTemplate(win, tt, cv2.TM_CCOEFF_NORMED))
            shift = np.array([[1, 0, x0 + loc[0]], [0, 1, y0 + loc[1]], [0, 0, 1]], float)
            hyps.append((float(v), np.diag([1 / f, 1 / f, 1]) @ shift @ np.vstack([m, [0, 0, 1]])))
    hyps.sort(key=lambda x: -x[0])
    picked: list[tuple[np.ndarray, np.ndarray]] = []
    for _, h in hyps:
        q = cv2.perspectiveTransform(CARD.reshape(-1, 1, 2), h.astype(np.float32)).reshape(4, 2)
        if all(float(np.abs(q - p).max()) > 0.04 * CW for p, _ in picked):
            picked.append((q, h))
        if len(picked) >= top:
            break
    return [h for _, h in picked]


def fit(img: np.ndarray, start_px: np.ndarray, search: bool = True, top: int = 3) -> dict | None:
    """
    Fit the card frame near `start_px` (4 corners in pixels, card's TL first).
    → {"quad": float32 4×2 px (TL, TR, BR, BL), "score": ECC correlation} or None.
    """
    tpl = _template()
    if tpl is None:
        return None
    t, fill, _ = tpl
    start = np.float32(start_px)
    side = (np.linalg.norm(start[1] - start[0]) + np.linalg.norm(start[2] - start[3])) / 2
    if side < 20:
        return None
    s = CW / float(side)                     # work where the card is about template size
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    g = cv2.GaussianBlur(gradient(small), (0, 0), 1.0)
    st = start * s
    seeds = _search(t, fill, g, st, top) if search else []
    seeds.append(cv2.getPerspectiveTransform(CARD, st).astype(np.float64))
    best: tuple[np.ndarray | None, float] = (None, -1.0)
    for h in seeds:
        cc = None
        for lvl in (2, 1):
            k = 1.0 / lvl
            sm = np.diag([k, k, 1])
            cc, hl = _ecc(cv2.resize(t, None, fx=k, fy=k, interpolation=cv2.INTER_AREA),
                          cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA),
                          sm @ h @ np.linalg.inv(sm))
            if cc is None:
                break
            h = np.linalg.inv(sm) @ hl @ sm
        if cc is not None and cc > best[1]:
            best = (h, cc)
    if best[0] is None:
        return None
    quad = cv2.perspectiveTransform(CARD.reshape(-1, 1, 2), best[0].astype(np.float32)).reshape(4, 2) / s
    return {"quad": quad.astype(np.float32), "score": round(best[1], 3)}


def _gradient_at_card_scale(img: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, float] | None:
    side = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    if side < 20:
        return None
    s = CW / float(side)
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    return cv2.GaussianBlur(gradient(small), (0, 0), 1.0), s


def score(img: np.ndarray, quad_px: np.ndarray) -> float:
    """
    How much the outline `quad_px` (TL first) looks like a card frame: the
    correlation of the picture's gradients inside it with the mean frame,
    artwork left out. A card's own outline scores high; a sleeve, a binder
    pocket, the art box or a random rectangle score low.
    """
    tpl = _template()
    q = np.float32(quad_px)
    gs = _gradient_at_card_scale(img, q) if tpl is not None else None
    if gs is None:
        return 0.0
    t, _, keep = tpl
    g, s = gs
    h = cv2.getPerspectiveTransform(CARD, q * s)
    warped = cv2.warpPerspective(g, h, (t.shape[1], t.shape[0]), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    a, b = t[keep], warped[keep]
    a, b = a - a.mean(), b - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return round(float((a * b).sum()) / den, 3) if den > 0 else 0.0


SNAP_BAND = 0.035                            # of the card width, each way
SNAP_SPAN = (0.12, 0.88)                     # along each side (skip the rounded corners)


def _line_intersect(p1, d1, p2, d2) -> np.ndarray:
    a = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], float)
    t = np.linalg.solve(a, np.asarray(p2, float) - np.asarray(p1, float))
    return np.asarray(p1, float) + t[0] * np.asarray(d1, float)


def snap_edges(img: np.ndarray, quad_px: np.ndarray, band: float = SNAP_BAND) -> np.ndarray:
    """
    Move each side of the quad onto the strongest straight luminance edge
    running parallel to it within ±band card widths; the corners are the
    intersections of the four snapped sides. A side without a clear edge stays.
    """
    q = np.float64(quad_px)
    cw = float(np.linalg.norm(q[1] - q[0]))
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    k = min(1.0, 600.0 / max(cw, 1.0))       # card ~600 px wide is plenty
    if k < 1:
        gray = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    qs = q * k
    ctr = qs.mean(0)
    r = max(2, int(round(band * cw * k)))
    offs = np.arange(-r, r + 1, dtype=np.float64)
    lines = []
    for i in range(4):
        a, b = qs[i], qs[(i + 1) % 4]
        d = (b - a) / (np.linalg.norm(b - a) or 1.0)
        n = np.array([-d[1], d[0]])
        if np.dot(ctr - (a + b) / 2, n) < 0:
            n = -n                            # inward
        ts = np.linspace(SNAP_SPAN[0], SNAP_SPAN[1], 60)
        base = a[None, :] + ts[:, None] * (b - a)[None, :]
        pts = base[None, :, :] + offs[:, None, None] * n[None, None, :]
        mx, my = pts[..., 0].astype(np.float32), pts[..., 1].astype(np.float32)
        proj = np.abs(cv2.remap(gx, mx, my, cv2.INTER_LINEAR) * n[0] + cv2.remap(gy, mx, my, cv2.INTER_LINEAR) * n[1])
        # a straight edge is strong along most of the side: median, not mean
        prof = np.median(proj, axis=1)
        j = int(np.argmax(prof))
        shift = 0.0
        if prof[j] > 1.6 * float(np.median(prof)) and 0 < j < len(prof) - 1:
            y0, y1, y2 = prof[j - 1], prof[j], prof[j + 1]
            den = y0 - 2 * y1 + y2
            shift = offs[j] + (0.5 * (y0 - y2) / den if den else 0.0)
        lines.append((a + shift * n, d))
    out = []
    for i in range(4):
        p1, d1 = lines[(i - 1) % 4]
        p2, d2 = lines[i]
        try:
            out.append(_line_intersect(p1, d1, p2, d2))
        except np.linalg.LinAlgError:
            return np.float32(quad_px)
    return (np.array(out) / k).astype(np.float32)
