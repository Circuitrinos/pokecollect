"""A whole live scan, simulated: cards on the table, a hand-held phone moving over them, the phone camera
(light delay, frame-synchronous switching, auto exposure with overshoot, rolling shutter, noise, frame
drops, tracking error) — and the app's own flash test (FlashLoop / FlashTally, TypeScript, run in node
through harness.cjs, built by build.sh) deciding when to switch the light and what each card is.

The cards come from the renderer calibrated to Brian's v66 video (fit.json); the camera timing from the
Fairphone 6 logs (light ~0.5 s after the request, first frame after a switch overshoots 120→225 / 150→55,
exposure settles in 2–3 frames at ~147 lit / ~119 dark)."""
from paths import data, work
import json, base64, subprocess, time
import numpy as np, cv2
import render as R
import sim
import appflash as A

FIT = json.load(open(work("fit.json")))
P = {k: v for k, v in FIT.items() if k not in ("flash_power", "off_x", "off_y")}
OFF = (FIT["off_x"], FIT["off_y"])
W, H = 720, 1280                      # video (portrait)
FOCAL = 0.73 * max(W, H)
PITCH = (68.0, 93.0)                  # card grid pitch on the table (mm)
NAMES = {1: "RH sleeve", 2: "Holo sleeve", 3: "Normal sleeve", 4: "RH", 5: "Holo", 6: "Normal"}
TRUTH = {1: "Reverse Holo", 2: "Holo", 3: "Normal", 4: "Reverse Holo", 5: "Holo", 6: "Normal"}

# Fairphone 6 in the /e/OS browser (from the logs)
FAIRPHONE = dict(fps=30.0, delay=(0.40, 0.60), sync=0.85, t_exp=0.010, t_read=0.020, ae_alpha=0.8, ae_beta=0.73,
                 dark_mean=119.0, torch_call=(0.06, 0.16), drop=0.03, track_px=0.3, track_drift=0.4, noise=0.8, quad_px=0.7)


def rot_xyz(ax, ay, az):
    cx, sx, cy, sy, cz, sz = np.cos(ax), np.sin(ax), np.cos(ay), np.sin(ay), np.cos(az), np.sin(az)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rx @ Ry @ Rz


def smooth_walk(rng, n, dt, sigma, tau):
    """Ornstein–Uhlenbeck: wanders with spread sigma, time scale tau (s)."""
    x = np.zeros(n); a = np.exp(-dt / tau); s = sigma * np.sqrt(1 - a * a)
    x[0] = rng.normal(0, sigma)
    for i in range(1, n):
        x[i] = a * x[i - 1] + rng.normal(0, s)
    return x


class Scenario:
    """layout: {cid: (col, row)} on the table; hand: where the phone is over time."""

    def __init__(self, seed=0, height=300.0, ambient=1.0, cards=None, layout=None, center=(0.0, 0.0),
                 wander=25.0, tilt_deg=3.0, tremor=1.5, cam=None, path=None, duration=20.0, flash_scale=1.0):
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.cam = {**FAIRPHONE, **(cam or {})}
        self.cids = cards or [1, 2, 3, 4, 5, 6]
        default = {1: (-1, -0.5), 2: (0, -0.5), 3: (1, -0.5), 4: (-1, 0.5), 5: (0, 0.5), 6: (1, 0.5)}
        self.layout = layout or {c: default[c] for c in self.cids}
        self.scene = {**sim.SCENE0, "flash_power": FIT["flash_power"] * flash_scale, "env": sim.SCENE0["env"] * ambient}
        self.height, self.center, self.duration = height, center, duration
        dt = 1.0 / self.cam["fps"]
        n = int(duration * self.cam["fps"]) + 2
        r = self.rng
        self.t = np.arange(n) * dt
        if path is not None:
            base = np.array([path(t) for t in self.t])
        else:
            base = np.zeros((n, 3)); base[:, 0], base[:, 1], base[:, 2] = center[0], center[1], height
        self.pos = base + np.stack([smooth_walk(r, n, dt, wander, 1.5) + smooth_walk(r, n, dt, tremor, 0.08),
                                    smooth_walk(r, n, dt, wander, 1.5) + smooth_walk(r, n, dt, tremor, 0.08),
                                    smooth_walk(r, n, dt, wander * 0.6, 2.0)], 1)
        td = np.radians(tilt_deg)
        self.ang = np.stack([smooth_walk(r, n, dt, td, 1.0), smooth_walk(r, n, dt, td, 1.0), smooth_walk(r, n, dt, td / 2, 2.0)], 1)
        self.track = np.stack([smooth_walk(r, n, dt, self.cam["track_drift"], 0.5) for _ in range(2 * len(self.cids))], 1)

    def card_center(self, cid):
        col, row = self.layout[cid]
        return np.array([col * PITCH[0], row * PITCH[1], 0.0])

    def pose(self, k):
        """camera centre (world mm) and R (rows: camera x, y, z axes in world) at frame k"""
        base = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1]], float)   # looking straight down, cards upright
        Rm = rot_xyz(*self.ang[k]) @ base
        return self.pos[k], Rm

    def quad(self, k, cid):
        C, Rm = self.pose(k)
        c = self.card_center(cid)
        pts = []
        for dx, dy in ((-31.5, -44), (31.5, -44), (31.5, 44), (-31.5, 44)):
            p = Rm @ (c + [dx, dy, 0] - C)
            if p[2] <= 1:
                return None
            pts.append([W / 2 + FOCAL * p[0] / p[2], H / 2 + FOCAL * p[1] / p[2]])
        return pts


class Session:
    def __init__(self, sc: Scenario, harness=work("harness.cjs"), method="app"):
        self.sc = sc
        self.cards = sim.cards(P, sc.seed)
        self.node = subprocess.Popen(["node", harness], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.rad = {}            # keyframe → {cid: (dark, lit)}
        self.method = method
        self.truepos = {}
        self.poslog = []

    def ask(self, msg):
        self.node.stdin.write(json.dumps(msg) + "\n"); self.node.stdin.flush()
        return json.loads(self.node.stdout.readline())

    def close(self):
        self.node.stdin.close(); self.node.wait()

    # ── light on the cards ──
    def radiance(self, k):
        kf = k - k % 3
        if kf not in self.rad:
            C, Rm = self.sc.pose(kf)
            out = {}
            for cid in self.sc.cids:
                cam = C - self.sc.card_center(cid)
                c = self.cards[cid]
                d = sim.radiance(c, cam, Rm, self.sc.scene, False, OFF)
                l = sim.radiance(c, cam, Rm, self.sc.scene, True, OFF)
                out[cid] = (d, l)
            self.rad = {kf: out}
        return self.rad[kf]

    def run(self, log=False):
        sc, cam, rng = self.sc, self.sc.cam, self.sc.rng
        dt = 1 / cam["fps"]
        switches = []                 # (time the light really changes, on)
        light = False
        req_until = -1.0              # the torch call is still running until then (no tick)
        gain = None
        prev_B = None
        frames, seen_logs, readings = 0, [], []
        state = {}
        done_at = None
        next_tick = 0.0
        t_end = sc.duration

        def lit_fraction(t0, t1):
            """share of [t0, t1] with the light on"""
            on = False; last = t0; tot = 0.0
            st = [(s, o) for s, o in switches]
            # state at t0
            for s, o in st:
                if s <= t0: on = o
            pts = [s for s, o in st if t0 < s < t1]
            cur = on; a = t0
            for s in pts:
                if cur: tot += s - a
                cur = [o for ss, o in st if ss == s][-1]; a = s
            if cur: tot += t1 - a
            return tot / max(t1 - t0, 1e-9)

        k = 0
        t = 0.0
        while t < t_end and k < len(sc.t) - 1:
            # ticks of the app (every 50 ms) up to this frame
            while next_tick <= t:
                if next_tick >= req_until:
                    w = self.ask({"op": "tick", "t": next_tick * 1000})["want"]
                    if w is not None:
                        self.ask({"op": "asked", "on": w, "t": next_tick * 1000})
                        req_until = next_tick + rng.uniform(*cam["torch_call"])
                        ts = next_tick + rng.uniform(*cam["delay"])
                        if rng.random() < cam["sync"]:           # switched with a capture request: at a frame boundary
                            ts = (np.floor((ts + cam["t_exp"]) / dt) + 1) * dt - cam["t_exp"] - 1e-4
                        switches.append((ts, w))
                next_tick += 0.05
            # this frame: rows exposed in [t + y*t_read - t_exp, t + y*t_read]
            rads = self.radiance(k)
            # whole picture brightness (linear), light share averaged over the rows
            wbar = np.mean([lit_fraction(t + y * cam["t_read"] - cam["t_exp"], t + y * cam["t_read"]) for y in (0.1, 0.3, 0.5, 0.7, 0.9)])
            Bd = np.mean([sim.lum_med(d) for d, l in rads.values()])
            Bl = np.mean([sim.lum_med(l) for d, l in rads.values()])
            B = Bd + wbar * (Bl - Bd)
            # auto exposure (one frame late): towards G*(B) = G0 * (B / Bd)^-beta, G0 puts the dark picture at dark_mean
            G0 = A.LIN[int(cam["dark_mean"])] / Bd
            target = np.log(G0) - cam["ae_beta"] * np.log(B / Bd)
            if gain is None:
                gain = target
            elif prev_B is not None:
                gain = gain + cam["ae_alpha"] * ((np.log(G0) - cam["ae_beta"] * np.log(prev_B / Bd)) - gain)
            prev_B = B
            g = np.exp(gain)
            mean = float(R.lin_to_srgb(np.array(B * g)) * 255)
            if rng.random() >= cam["drop"]:
                cards = []
                for i, cid in enumerate(sc.cids):
                    q = sc.quad(k, cid)
                    if q is None or not all(0 <= x <= W and 0 <= y <= H for x, y in q):
                        continue
                    d, l = rads[cid]
                    # rows of this card in the picture → light share per render row
                    y0 = (q[0][1] + q[1][1]) / 2 / H; y1 = (q[2][1] + q[3][1]) / 2 / H
                    rows = np.linspace(y0, y1, d.shape[0])
                    wr = np.array([lit_fraction(t + y * cam["t_read"] - cam["t_exp"], t + y * cam["t_read"]) for y in rows[::8]])
                    wr = np.interp(np.arange(d.shape[0]), np.arange(0, d.shape[0], 8), wr)[:, None, None]
                    rad = d + wr * (l - d)
                    # the outline from the tracker is a little off (drifting + per frame)
                    sx = sc.track[k, 2 * i] + rng.normal(0, cam["track_px"])
                    sy = sc.track[k, 2 * i + 1] + rng.normal(0, cam["track_px"])
                    rad = sim.shift(rad, sx, sy)
                    px = sim.to_patch(rad, g, rng, cam["noise"])
                    qn = [[x + rng.normal(0, cam["quad_px"]), y + rng.normal(0, cam["quad_px"])] for x, y in q]   # tracker outline noise
                    cards.append({"key": f"c{cid}", "quad": qn, "px": base64.b64encode(np.ascontiguousarray(px).tobytes()).decode()})
                    C_, _ = sc.pose(k); rel = C_ - sc.card_center(cid)
                    self.truepos[f"c{cid}"] = (rel[0] / rel[2], rel[1] / rel[2])
                res = self.ask({"op": "frame", "t": t * 1000, "mean": mean, "w": W, "h": H, "cards": cards})
                frames += 1
                state = res["cards"]
                for key, v in state.items():
                    if len(v) > 4 and v[4] and key in self.truepos:
                        self.poslog.append((key, self.truepos[key], (v[4]["tx"], v[4]["ty"])))
                for ev, data in res["log"]:
                    seen_logs.append((ev, data))
                    if ev == "live.flash.switch":
                        for key, v in data["cards"].items():
                            if v[0] is not None:
                                readings.append((int(key[1:]), v[0], v[3], t))
                if log:
                    print(f"{t:6.3f} light {wbar:.2f} gain {g:6.2f} mean {mean:5.1f}", " ".join(f"{e}" for e, _ in res["log"]))
                if state and all(v[2] for v in state.values()) and len(state) == len(sc.cids):
                    done_at = t
                    break
            k += 1
            t = sc.t[k] + rng.normal(0, 0.002)
        out = {"done": done_at, "frames": frames, "readings": readings, "logs": seen_logs, "poslog": self.poslog,
               "kinds": {int(key[1:]): (v[2], v[1], v[0]) for key, v in state.items()}}
        return out


def run(sc, **kw):
    s = Session(sc, **kw)
    try:
        return s.run()
    finally:
        s.close()


if __name__ == "__main__":
    t0 = time.time()
    s = Session(Scenario(seed=1, ambient=0.03, duration=6))
    r = s.run(log=True)
    s.close()
    print(time.time() - t0, "s")
    print(r["kinds"], r["done"])
