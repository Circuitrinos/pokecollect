"""Uneven room light (shadow of phone / arm, one lamp): the room light on a card falls off smoothly in some
direction. Applied to stored readings (linear: dark' = dark·G, lit' = lit − k·dark·(1−G)), then measured
with the app's share and with a share after taking out a plane fitted on the frame."""
import numpy as np, appflash as A
W, H = A.FLASH_W, A.FLASH_H
U, V = np.meshgrid((np.arange(W) + 0.5) / W, (np.arange(H) + 0.5) / H)
ENC = lambda x: np.clip(np.round(np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(np.maximum(x, 0), 1 / 2.4) - 0.055) * 255), 0, 255).astype(np.uint8)

def shade(dark_px, lit_px, k, rng, depth):
    """random smooth ambient falloff across the card: G from 1 down to (1 - depth)"""
    a = rng.uniform(0, 2 * np.pi)
    d = (U - 0.5) * np.cos(a) * 0.72 + (V - 0.5) * np.sin(a)      # card is 63×88: scale u
    d = (d - d.min()) / (d.max() - d.min())
    G = (1 - depth * d)[..., None]
    dl = A.LIN[np.asarray(dark_px, np.uint8)[..., :3]]; ll = A.LIN[np.asarray(lit_px, np.uint8)[..., :3]]
    d2 = dl * G; l2 = ll - k * dl * (1 - G)
    out = []
    for x in (d2, l2):
        out.append(np.dstack([ENC(x), np.full((H, W), 255, np.uint8)]))
    return out

def plane_share(off, on, mode=1, lift=A.LIFT):
    off = np.asarray(off, np.uint8).reshape(H, W, 4); on = np.asarray(on, np.uint8).reshape(H, W, 4)
    use = (A.ART | A.FRAME) & (off[..., :3].max(-1) < A.CLIP) & (on[..., :3].max(-1) < A.CLIP)
    a, b = A._lum(off), A._lum(on); use &= a > 0.01
    art, fr = A.ART & use, A.FRAME & use
    if art.sum() < 0.3 * A.ART.sum() or fr.sum() < 0.3 * A.FRAME.sum():
        return None
    g = np.log(np.maximum(b, 1e-3) / np.maximum(a, 1e-6))
    X = np.stack([np.ones(fr.sum()), U[fr], V[fr]], 1); y = g[fr]
    w = np.ones(len(y))
    for _ in range(4):                                  # robust (IRLS ~ L1)
        c, *_ = np.linalg.lstsq(X * w[:, None], y * w, rcond=None)
        w = 1 / np.sqrt(np.maximum(np.abs(X @ c - y), 0.02))
    r = g - (c[0] + c[1] * U + c[2] * V)
    ra, rf = r[art], r[fr]
    if mode == 1:
        return float((ra > lift).mean() - (ra < -lift).mean())
    return float((ra > lift).mean() - (rf > lift).mean())


def ratio_lift(room, r=2.24):
    """threshold on the log lift that means 'reflects the flash r× more, relative to room light, than the
    card's median' — room = lit/dark at equal exposure (1 + c·ρ_med); bright room (1.15) → 0.15, dark → log r"""
    m = max(room - 1.0, 0.02)
    return float(np.log((1 + r * m) / (1 + m)))


def share_ratio(off, on, room, flat=False):
    lift = ratio_lift(room)
    if flat:
        return plane_share(off, on, 1, lift=lift)
    off = np.asarray(off, np.uint8).reshape(H, W, 4); on = np.asarray(on, np.uint8).reshape(H, W, 4)
    use = (A.ART | A.FRAME) & (off[..., :3].max(-1) < A.CLIP) & (on[..., :3].max(-1) < A.CLIP)
    a, b = A._lum(off), A._lum(on); use &= a > 0.01
    if use[A.ART].sum() < 0.3 * A.ART.sum() or use[A.FRAME].sum() < 0.3 * A.FRAME.sum():
        return None
    g = np.full(a.shape, np.nan, np.float32); g[use] = np.log(np.maximum(b[use], 1e-3) / a[use])
    base = np.median(g[use]); up = use & (g - base > lift)
    return float(up[A.ART].sum() / use[A.ART].sum() - up[A.FRAME].sum() / use[A.FRAME].sum())
