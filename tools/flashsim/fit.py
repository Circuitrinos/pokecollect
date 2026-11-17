"""Fit the renderer to the real readings in stages: light + gloss + sleeve on the Normal cards, then the
Holo foil on the Holo cards, then the Reverse Holo foil — each stage with Nelder–Mead on bounded params."""
from paths import data, work
import json, sys, time
import numpy as np
from scipy.optimize import minimize
import sim

STAGES = [
    ("light+gloss", [3, 6], {"flash_power": (7e3, 1e3, 6e4, "log"), "coat": (1.0, 0.05, 4, "log"), "coat_rough": (0.32, 0.04, 0.9, "lin"),
                             "sleeve_rough": (0.08, 0.01, 0.5, "log"), "off_x": (10, -25, 25, "lin"), "off_y": (0, -25, 25, "lin")}),
    ("holo", [2, 5], {"metal_refl": (0.75, 0.05, 1.5, "lin"), "foil_diffuse": (0.45, 0.05, 1.0, "lin"), "holo_rough": (0.28, 0.04, 0.9, "lin"),
                      "flake_share": (0.12, 0.0, 0.7, "lin"), "flake_tilt": (10, 1, 35, "lin"), "flake_rough": (0.07, 0.02, 0.5, "log"),
                      "holo_edge": (0.05, 0.0, 1.0, "lin")}),
    ("reverse holo", [1, 4], {"foil_rough": (0.28, 0.04, 0.9, "lin"), "pattern_rough": (0.14, 0.02, 0.7, "log"), "emboss": (6, 0, 25, "lin"),
                              "rh_metal": (0.75, 0.05, 1.5, "lin"), "rh_diffuse": (0.45, 0.05, 1.0, "lin")}),
]


def unpack(x, spec):
    out = {}
    for xi, (k, (init, lo, hi, kind)) in zip(x, spec.items()):
        s = 1 / (1 + np.exp(-xi))
        out[k] = float(np.exp(np.log(lo) + s * (np.log(hi) - np.log(lo)))) if kind == "log" else float(lo + s * (hi - lo))
    return out


def pack(vals, spec):
    x = []
    for k, (init, lo, hi, kind) in spec.items():
        v = vals.get(k, init)
        s = (np.log(v) - np.log(lo)) / (np.log(hi) - np.log(lo)) if kind == "log" else (v - lo) / (hi - lo)
        s = min(max(s, 1e-3), 1 - 1e-3)
        x.append(np.log(s / (1 - s)))
    return np.array(x)


def split(vals):
    p = {k: v for k, v in vals.items() if k not in ("flash_power", "off_x", "off_y")}
    scene = {**sim.SCENE0, "flash_power": vals.get("flash_power", 7e3)}
    return p, scene, (vals.get("off_x", 10.0), vals.get("off_y", 0.0))


if __name__ == "__main__":
    vals = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}
    for name, cids, spec in STAGES:
        rows = [r for r in sim.REAL if r["cid"] in cids]
        t0, evals = time.time(), [0]

        def f(x):
            v = {**vals, **unpack(x, spec)}
            p, scene, off = split(v)
            evals[0] += 1
            return sim.loss(sim.simulate(p, scene, off, rows, noise=0), rows)

        x0 = pack(vals, spec)
        res = minimize(f, x0, method="Nelder-Mead", options={"maxfev": 260, "xatol": 0.02, "fatol": 1e-5, "adaptive": True})
        vals.update(unpack(res.x, spec))
        print(f"{name}: loss {res.fun:.4f} after {evals[0]} renders, {time.time() - t0:.0f}s", flush=True)
        print("   ", {k: round(v, 4) for k, v in unpack(res.x, spec).items()}, flush=True)
        json.dump(vals, open(work("fit.json"), "w"), indent=1)
    p, scene, off = split(vals)
    s = sim.simulate(p, scene, off, noise=0.8)
    print("all cards, loss", round(sim.loss(s), 4))
    sim.report(s)
