"""Render Brian's six cards at the real camera poses from v66 and run the app's measure on them."""
from paths import data, work
import sys, json, time
import numpy as np, cv2
import render as R
import appflash as A

REAL = json.load(open(work("real_pairs.json")))
# card outside the picture (black patch) or no reading: not usable
REAL = [r for r in REAL if r["dark_level"] > 20 and r["lit_level"] > 20 and r["g_art"] is not None
        and np.isfinite(r["g_art"]) and np.isfinite(r["g_frame"])]
FIN = {1: ("Reverse Holo", True, False), 2: ("Holo", True, True), 3: ("Normal", True, False),
       4: ("Reverse Holo", False, False), 5: ("Holo", False, True), 6: ("Normal", False, True)}
SCALE = 0.25


def cards(p, seed=0):
    return {cid: R.Card(cid, f, s, st, {**R.DEFAULT, **p}, seed, SCALE) for cid, (f, s, st) in FIN.items()}


def flash_pos(cam, rot, off):
    rot = np.asarray(rot)
    return np.asarray(cam, np.float32) + off[0] * rot[0] + off[1] * rot[1]


def radiance(card, cam, rot, scene, flash_on, off):
    P = card.world(1, 0.5)                       # card centred at the origin
    cam = np.asarray(cam, np.float32)
    lights = []
    if scene.get("lamp_power", 0) > 0:
        lights.append(R.lamp(scene))
    if flash_on:
        lights.append({"samples": [(flash_pos(cam, rot, off).astype(np.float32), 1.0)], "power": scene["flash_power"],
                       "color": np.array([1.0, 1.0, 1.04], np.float32)})
    return R.shade(card, P, cam, lights, scene)


def shift(rad, dx, dy):
    """move the picture by (dx, dy) patch pixels (sub-pixel): the card outline in the lit frame is a little off"""
    if not dx and not dy:
        return rad
    k = rad.shape[1] / A.FLASH_W
    M = np.float32([[1, 0, dx * k], [0, 1, dy * k]])
    return cv2.warpAffine(rad, M, (rad.shape[1], rad.shape[0]), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def to_patch(rad, gain, rng=None, noise=0.8):
    x = R.lin_to_srgb(rad * gain)
    img = x * 255
    if rng is not None and noise:
        img = img + rng.normal(0, noise, img.shape)
    img = cv2.resize(np.clip(img, 0, 255).astype(np.float32), (A.FLASH_W, A.FLASH_H), interpolation=cv2.INTER_AREA)
    img = np.clip(np.round(img), 0, 255).astype(np.uint8)
    return np.dstack([img, np.full((A.FLASH_H, A.FLASH_W), 255, np.uint8)])


def lum_med(rad):
    y = rad @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    small = cv2.resize(y, (A.FLASH_W, A.FLASH_H), interpolation=cv2.INTER_AREA)
    return float(np.median(small[A.ART | A.FRAME]))


def simulate(p, scene, off, rows=None, seed=0, noise=0.8, jitter=0.0):
    """One simulated reading per real reading (same card, camera pose, exposure matched on the dark frame)."""
    cs = cards(p, seed)
    rng = np.random.default_rng(seed)
    out = []
    for r in rows or REAL:
        c = cs[r["cid"]]
        dark = radiance(c, r["cam"], r["rot"], scene, False, off)
        lit = radiance(c, r["cam"], r["rot"], scene, True, off)
        # exposure: the dark frame's median brightness as in the real frame (on: dark-set; off: lit-set exposure)
        want = A.LIN[int(round(min(254, r["dark_level"])))]
        gain = want / max(lum_med(dark), 1e-6)
        if jitter:
            lit = shift(lit, *rng.normal(0, jitter, 2))
        d, l = to_patch(dark, gain, rng, noise), to_patch(lit, gain, rng, noise)
        g = A.region_gains(d, l)
        out.append(dict(cid=r["cid"], share=A.flash_share(d, l), g_art=None if g is None else g[0],
                        g_frame=None if g is None else g[1], lit_level=A.lit_level(l), dark_level=A.lit_level(d),
                        dark_px=d, lit_px=l))
    return out


def deg_of(r):
    c = r["cam"]
    return float(np.degrees(np.arctan(np.hypot(c[0], c[1]) / c[2])))


def loss(sim, real=REAL, detail=False):
    tot, n = 0.0, 0
    per = {}
    for s, r in zip(sim, real):
        if s["g_art"] is None:          # the simulation lost a reading the real video has
            tot += 1.0; n += 1.0
            continue
        w = 2.0 if 3 <= deg_of(r) <= 20 else 0.7
        e = (s["g_art"] - r["g_art"]) ** 2 + (s["g_frame"] - r["g_frame"]) ** 2
        if r["share"] is not None and s["share"] is not None:
            e += 0.5 * (s["share"] - r["share"]) ** 2
        e += 0.3 * ((s["lit_level"] - r["lit_level"]) / 50) ** 2
        tot += w * e; n += w
        per.setdefault(r["cid"], []).append(e)
    if detail:
        return tot / max(n, 1), {k: float(np.mean(v)) for k, v in per.items()}
    return tot / max(n, 1)


def report(sim, real=REAL):
    names = {1: "RH sleeve", 2: "Holo sleeve", 3: "Normal sleeve", 4: "RH", 5: "Holo", 6: "Normal"}
    for cid in range(1, 7):
        ss = [s for s, r in zip(sim, real) if r["cid"] == cid and r["g_art"] is not None and s["g_art"] is not None]
        rr = [r for s, r in zip(sim, real) if r["cid"] == cid and r["g_art"] is not None and s["g_art"] is not None]
        med = lambda xs: float(np.median([x for x in xs if x is not None]))
        print(f"  {names[cid]:13s} share sim {med([s['share'] for s in ss]):+.3f} real {med([r['share'] for r in rr]):+.3f}"
              f" | art {med([s['g_art'] for s in ss]):.3f}/{med([r['g_art'] for r in rr]):.3f}"
              f" frame {med([s['g_frame'] for s in ss]):.3f}/{med([r['g_frame'] for r in rr]):.3f}"
              f" | lit {med([s['lit_level'] for s in ss]):.0f}/{med([r['lit_level'] for r in rr]):.0f}")


SCENE0 = dict(env=0.10, lamp_power=0.0, lamp_pos=(350.0, -700.0, 1900.0), lamp_r=120.0, flash_power=2.5e4)

if __name__ == "__main__":
    t0 = time.time()
    sim = simulate({}, SCENE0, (10.0, 0.0))
    print(f"{time.time() - t0:.1f}s, loss {loss(sim):.4f}")
    report(sim)
