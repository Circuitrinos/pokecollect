"""Usage: gen_table.py SEED N [ROOMS] [SUFFIX] — rooms as env factors, e.g. 0.012,0.004 dark.
Readings for each card with the camera anywhere within ±0.38 (tan) of straight above it: rooms from
bright to dark, three heights — the material for the expected-reading tables (one seed) and a held-out
check (another seed). Exposure re-set while lit (AE)."""
from paths import data, work
import pickle, sys, numpy as np, sim, appflash as A, session as S
seed = int(sys.argv[1])
cs = sim.cards(S.P, 0)
Rm = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], float)
rng = np.random.default_rng(100 + seed)
out = []
ROOMS = (1.0, 0.3, 0.1, 0.04) if len(sys.argv) < 4 else tuple(float(x) for x in sys.argv[3].split(","))
for amb in ROOMS:
    scene = {**sim.SCENE0, "flash_power": S.FIT["flash_power"], "env": sim.SCENE0["env"] * amb}
    for h in (250.0, 300.0, 400.0):
        for cid in range(1, 7):
            for i in range(int(sys.argv[2]) if len(sys.argv) > 2 else 50):
                tx, ty = rng.uniform(-0.38, 0.38, 2)
                hh = h * rng.uniform(0.93, 1.07)
                cam = np.array([tx * hh, ty * hh, hh])
                d = sim.radiance(cs[cid], cam, Rm, scene, False, S.OFF); l = sim.radiance(cs[cid], cam, Rm, scene, True, S.OFF)
                ratio = sim.lum_med(l) / sim.lum_med(d)
                g = A.LIN[119] / sim.lum_med(d); k = ratio ** -0.73
                dp = sim.to_patch(sim.shift(d, *rng.normal(0, 0.3, 2)), g, rng)
                lp = sim.to_patch(sim.shift(l, *rng.normal(0, 0.3, 2)), g * k, rng)
                out.append(dict(amb=amb, h=hh, cid=cid, kind=S.TRUTH[cid], cam=cam.tolist(), tx=tx, ty=ty, room=float(ratio),
                                share=A.flash_share(dp, lp), dark_px=dp, lit_px=lp))
        print(amb, h, len(out), flush=True)
pickle.dump(out, open(work(f"table_{seed}{sys.argv[4] if len(sys.argv) > 4 else ''}.pkl"), "wb"))
