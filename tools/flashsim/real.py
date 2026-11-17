"""Calibration targets from Brian's manual-flash video (v66, camera app, 1080×1920): per light switch and
card, the flash gain of art and frame, the app's reading, brightness levels, and where the camera was
(card coordinates, mm) — from the per-frame card homographies (catalog 300×412 → image)."""
from paths import data, work
import pickle, json
import numpy as np, cv2
import appflash as A

ON = [101, 152, 204, 253, 300, 346, 397, 490, 552, 597, 674]
OFF = [127, 179, 227, 277, 325, 370, 460, 516, 574, 619, 699]
CW, CH = 300, 412
W, H = 40, 55
S = np.diag([W / CW, H / CH, 1.0])
F = 0.73 * 1920
K = np.array([[F, 0, 540], [0, F, 960], [0, 0, 1]])
MM = np.array([[CW / 63.0, 0, CW / 2], [0, CH / 88.0, CH / 2], [0, 0, 1]])   # card mm (centre 0) → catalog px
hs = pickle.load(open(data("v66_H.pkl"), "rb"))
_cache = {}


def frame(n):
    if n not in _cache:
        _cache[n] = cv2.imread(data("v66", f"{n:04d}.jpg"))
    return _cache[n]


def patch(n, cid):
    p = cv2.warpPerspective(frame(n), S @ np.linalg.inv(hs[n][cid]), (W, H), flags=cv2.INTER_AREA)
    return np.dstack([p[..., ::-1], np.full((H, W), 255, np.uint8)])


def pose(n, cid):
    """camera centre and axes in the card's frame (mm; x right, y down the card, z toward the camera)"""
    M = np.linalg.inv(K) @ hs[n][cid] @ MM
    lam = 1 / np.mean([np.linalg.norm(M[:, 0]), np.linalg.norm(M[:, 1])])
    r1, r2, t = M[:, 0] * lam, M[:, 1] * lam, M[:, 2] * lam
    R = np.stack([r1, r2, np.cross(r1, r2)], 1)
    U, _, Vt = np.linalg.svd(R); R = U @ Vt
    C = -R.T @ t
    if C[2] < 0:            # card faces the camera
        R = R @ np.diag([-1, -1, 1]); t = -t; C = -R.T @ t
    return C, R             # R: card → camera; camera x axis in card coords = R[0]


def have(*ns):
    return all(hs.get(n) is not None for n in ns)


rows = []
for i, t in enumerate(ON):
    if have(t - 2, t + 1):
        rows.append(dict(kind="on", dark=t - 2, lit=t + 1))
for i, t in enumerate(OFF):
    if have(t + 1, t - 2):
        rows.append(dict(kind="off", dark=t + 1, lit=t - 2))
out = []
for r in rows:
    for cid in range(1, 7):
        d, l = patch(r["dark"], cid), patch(r["lit"], cid)
        C, R = pose(r["lit"], cid)
        g = A.region_gains(d, l)
        out.append(dict(cid=cid, kind=r["kind"], dark_frame=r["dark"], lit_frame=r["lit"], share=A.flash_share(d, l),
                        g_art=None if g is None else g[0], g_frame=None if g is None else g[1],
                        dark_level=A.lit_level(d), lit_level=A.lit_level(l), cam=C.tolist(), rot=R.tolist(),
                        dark_px=d.tolist(), lit_px=l.tolist()))
json.dump(out, open(work("real_pairs.json"), "w"))
print(len(out), "card readings")
FIN = {1: "RH sleeve", 2: "Holo sleeve", 3: "Normal sleeve", 4: "RH", 5: "Holo", 6: "Normal"}
for cid in range(1, 7):
    rs = [o for o in out if o["cid"] == cid and o["g_art"] is not None]
    deg = [np.degrees(np.arctan(np.hypot(o["cam"][0], o["cam"][1]) / o["cam"][2])) for o in rs]
    print(f"{cid} {FIN[cid]:13s} n={len(rs)} share {np.median([o['share'] for o in rs if o['share'] is not None]):+.3f}"
          f"  gain art {np.median([o['g_art'] for o in rs]):.3f} frame {np.median([o['g_frame'] for o in rs]):.3f}"
          f"  dark {np.median([o['dark_level'] for o in rs]):.0f} lit {np.median([o['lit_level'] for o in rs]):.0f}"
          f"  dist {np.median([o['cam'][2] for o in rs]):.0f}mm angle {np.median(deg):.1f}° ({min(deg):.1f}–{max(deg):.1f})")
