"""Expected reading per kind as a function of where the camera is (tx, ty = camera offset / height, card
coordinates: x right, y down the card) — kernel-smoothed from simulated readings; spread from the
residuals. Optionally per room (bright / dark, by the lit/dark ratio at equal exposure)."""
import numpy as np
KINDS = ("Holo", "Normal", "Reverse Holo")
GRID = np.round(np.linspace(-0.36, 0.36, 13), 3)      # 0.06 steps
BW = 0.05

def build(rows, room_split=None, key="share"):
    """→ {room_index: {kind: (mu[13x13], sd[13x13])}}"""
    groups = [rows] if room_split is None else [[r for r in rows if r["room"] < room_split], [r for r in rows if r["room"] >= room_split]]
    out = []
    for g in groups:
        t = {}
        for k in KINDS:
            rr = [r for r in g if r["kind"] == k and r.get(key) is not None]
            X = np.array([[r["tx"], r["ty"]] for r in rr]); y = np.array([r[key] for r in rr])
            mu = np.zeros((len(GRID), len(GRID))); sd = np.zeros_like(mu)
            for j, gy in enumerate(GRID):
                for i, gx in enumerate(GRID):
                    w = np.exp(-((X[:, 0] - gx) ** 2 + (X[:, 1] - gy) ** 2) / (2 * BW ** 2))
                    m = (w * y).sum() / w.sum()
                    mu[j, i] = m
                    sd[j, i] = np.sqrt((w * (y - m) ** 2).sum() / w.sum())
            t[k] = (mu, np.maximum(sd, 0.06))
        out.append(t)
    return out

def lookup(tab, tx, ty):
    """bilinear in the grid; outside: clamped"""
    fx = np.clip((tx - GRID[0]) / (GRID[1] - GRID[0]), 0, len(GRID) - 1.001)
    fy = np.clip((ty - GRID[0]) / (GRID[1] - GRID[0]), 0, len(GRID) - 1.001)
    i, j = int(fx), int(fy); ax, ay = fx - i, fy - j
    def b(a):
        return (a[j, i] * (1 - ax) * (1 - ay) + a[j, i + 1] * ax * (1 - ay) + a[j + 1, i] * (1 - ax) * ay + a[j + 1, i + 1] * ax * ay)
    return {k: (b(mu), b(sd)) for k, (mu, sd) in tab.items()}

def score(tabs, readings, room_split=None, c=1.0):
    """readings: (share, tx, ty, room) → {kind: cost}; robust (Cauchy) residuals"""
    cost = {k: 0.0 for k in KINDS}
    for s, tx, ty, room in readings:
        tab = tabs[0] if room_split is None or room < room_split else tabs[1]
        e = lookup(tab, tx, ty)
        for k, (mu, sd) in e.items():
            z = (s - mu) / sd
            cost[k] += np.log1p((z / c) ** 2) + np.log(sd)
    return cost


def score_multi(tabsets, readings, room_split=None, c=1.0):
    """tabsets: list of tables (one per feature); readings: (features tuple, tx, ty, room)"""
    cost = {k: 0.0 for k in KINDS}
    for feats, tx, ty, room in readings:
        for tabs, s in zip(tabsets, feats):
            tab = tabs[0] if room_split is None or room < room_split else tabs[1]
            for k, (mu, sd) in lookup(tab, tx, ty).items():
                z = (s - mu) / sd
                cost[k] += np.log1p((z / c) ** 2) + np.log(sd)
    return cost
