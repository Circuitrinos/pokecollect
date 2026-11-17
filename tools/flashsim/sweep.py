"""Whole simulated scans with the app's own flash code: old rule vs new rule, rooms × heights × hand paths."""
from paths import data, work
import sys, json, time, itertools, numpy as np
import session as S

def tour(h, dwell=2.0, move=1.0, order=(1, 2, 3, 6, 5, 4)):
    lay = {1: (-1, -0.5), 2: (0, -0.5), 3: (1, -0.5), 4: (-1, 0.5), 5: (0, 0.5), 6: (1, 0.5)}
    pts = [np.array([lay[c][0] * S.PITCH[0], lay[c][1] * S.PITCH[1], h]) for c in order]
    seg = dwell + move
    def f(t):
        i = int(t // seg) % len(pts); u = t % seg
        a, b = pts[i], pts[(i + 1) % len(pts)]
        if u < dwell: return a
        s = (u - dwell) / move; s = s * s * (3 - 2 * s)
        return a + s * (b - a)
    return f

def scenarios(seeds=(0, 1)):
    for amb, h, path, seed in itertools.product((1.0, 0.1, 0.04), (280.0, 380.0), ("hover", "tour"), seeds):
        yield dict(amb=amb, h=h, path=path, seed=seed)

def run_one(cfg, harness, duration=30.0):
    path = tour(cfg["h"]) if cfg["path"] == "tour" else None
    sc = S.Scenario(seed=cfg["seed"] * 1000 + int(cfg["h"]) + int(cfg["amb"] * 100), height=cfg["h"], ambient=cfg["amb"], path=path,
                    wander=25.0 if path is None else 8.0, duration=duration)
    r = S.run(sc, harness=harness)
    out = []
    for cid in sc.cids:
        k = r["kinds"].get(cid)
        settled, tentative, n = k if k else (None, None, 0)
        out.append(dict(cfg, cid=cid, truth=S.TRUTH[cid], settled=settled, tentative=tentative, n=n, done=r["done"]))
    return out

if __name__ == "__main__":
    which, part, nparts = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    harness = work(f"harness_{which}.cjs")
    res = []
    for i, cfg in enumerate(scenarios()):
        if i % nparts != part: continue
        t0 = time.time()
        res += run_one(cfg, harness)
        print(which, cfg, f"{time.time() - t0:.0f}s", flush=True)
        json.dump(res, open(work(f"sweep_{which}_{part}.json"), "w"))
