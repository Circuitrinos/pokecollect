"""Exact Python port of the app's flash measure (web/src/lib/flashCheck.ts): masks, flashShare, litLevel,
flashKind / flashSettled. Patches are RGBA uint8 arrays of shape (55, 40, 4) (FLASH_H × FLASH_W)."""
import numpy as np

FLASH_W, FLASH_H = 40, 55
ART_BOX = dict(x0=0.085, y0=0.12, x1=0.915, y1=0.5)
BORDER, EDGE = 0.05, 0.03
STAMP = dict(x0=0.74, y0=0.4, y1=0.56)
CLIP, LIFT = 250, 0.15
_c = np.arange(256) / 255
LIN = np.where(_c <= 0.04045, _c / 12.92, ((_c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def masks(w=FLASH_W, h=FLASH_H):
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    U, V = np.meshgrid(u, v)
    inside = ~((U < BORDER) | (U > 1 - BORDER) | (V < BORDER) | (V > 1 - BORDER))
    a = ART_BOX
    core = (U > a["x0"] + EDGE) & (U < a["x1"] - EDGE) & (V > a["y0"] + EDGE) & (V < a["y1"] - EDGE)
    near = (U > a["x0"] - EDGE) & (U < a["x1"] + EDGE) & (V > a["y0"] - EDGE) & (V < a["y1"] + EDGE)
    stamp = (U > STAMP["x0"]) & (V > STAMP["y0"]) & (V < STAMP["y1"])
    art = inside & core & ~stamp
    frame = inside & ~(core & ~stamp) & ~near
    return art, frame


ART, FRAME = masks()


def _lum(p):
    return 0.2126 * LIN[p[..., 0]] + 0.7152 * LIN[p[..., 1]] + 0.0722 * LIN[p[..., 2]]


def flash_share(off, on):
    off = np.asarray(off, np.uint8).reshape(FLASH_H, FLASH_W, 4); on = np.asarray(on, np.uint8).reshape(FLASH_H, FLASH_W, 4)
    use = (ART | FRAME) & (off[..., :3].max(-1) < CLIP) & (on[..., :3].max(-1) < CLIP)
    a, b = _lum(off), _lum(on)
    use &= a > 0.01
    if use[ART].sum() < 0.3 * ART.sum() or use[FRAME].sum() < 0.3 * FRAME.sum():
        return None
    g = np.full(a.shape, np.nan, np.float32)
    g[use] = np.log(np.maximum(b[use], 1e-3) / a[use])
    base = np.median(g[use])
    up = use & (g - base > LIFT)
    return float(up[ART].sum() / use[ART].sum() - up[FRAME].sum() / use[FRAME].sum())


def region_gains(off, on):
    """log flash gain (mean linear luma lit / dark) of the art and of the frame — for calibration."""
    off = np.asarray(off, np.uint8).reshape(FLASH_H, FLASH_W, 4); on = np.asarray(on, np.uint8).reshape(FLASH_H, FLASH_W, 4)
    ok = (off[..., :3].max(-1) < CLIP) & (on[..., :3].max(-1) < CLIP)
    a, b = _lum(off), _lum(on)
    out = []
    for m in (ART, FRAME):
        k = m & ok
        if k.sum() < 0.3 * m.sum():
            return None
        if a[k].mean() <= 1e-6 or b[k].mean() <= 1e-6:
            return None
        out.append(float(np.log(b[k].mean() / a[k].mean())))
    return out


def lit_level(p):
    p = np.asarray(p, np.uint8).reshape(FLASH_H, FLASH_W, 4)
    y = 0.299 * p[..., 0] + 0.587 * p[..., 1] + 0.114 * p[..., 2]
    return float(np.median(y[ART | FRAME]))


# verdict (flashCheck.ts, current values)
MIN_N, HOLD = 4, 2
HOLO, REV, FLAT_LO, FLAT_HI, FLAT_N, LATE_N, LATE_HOLO, LATE_REV = 0.22, -0.08, -0.04, 0.06, 6, 10, 0.18, -0.05


def kind(s, P=None):
    P = P or {}
    g = lambda k, d: P.get(k, d)
    n = len(s)
    if n < g("MIN_N", MIN_N): return None
    m = float(np.median(s))
    if m >= g("HOLO", HOLO): return "Holo"
    if m <= g("REV", REV): return "Reverse Holo"
    if n >= g("FLAT_N", FLAT_N) and g("FLAT_LO", FLAT_LO) <= m <= g("FLAT_HI", FLAT_HI): return "Normal"
    if n >= g("LATE_N", LATE_N):
        return "Holo" if m >= g("LATE_HOLO", LATE_HOLO) else "Reverse Holo" if m <= g("LATE_REV", LATE_REV) else "Normal"
    return None


def settled(s, P=None):
    k = kind(s, P)
    h = (P or {}).get("HOLD", HOLD)
    return k if k and len(s) > h and kind(s[:-h], P) == k else None
