"""
A small physically based renderer for Brian's six Ethan's Adventure cards (flat cards on a
table, straight-down shading per texel, so card space = the straightened card the app sees).

Materials (per texel):
  paper   printed ink (albedo = catalog image) under a semi-gloss varnish (GGX, F0 0.04)
  foil    metal under the ink: tinted mirror-ish GGX lobe, less diffuse; same varnish on top
          Holo (Prize Pack cosmos): art box, with random tilted flakes (sparkle)
          Reverse Holo (Pokéball pattern): everything but the art box, embossed balls
  sleeve  clear glossy film over the card (dielectric, F0 0.04, very smooth)
Lights: a warm ceiling lamp (small disk), the room (dim, with a dark cone where the
photographer stands) and the phone's flashlight 1 cm beside the lens.
Camera: auto-exposure on the frame mean, clipping, sRGB, noise, slight blur.
"""
from __future__ import annotations
from paths import data, work

import numpy as np
import cv2

CAT = cv2.imread(data("catalog.png"))            # 600×825 sRGB, BGR
TW, TH = 300, 412                                    # texels (0.21 mm each)
CARD_W, CARD_H = 63.0, 88.0                          # mm
ART = (23, 57, 278, 215)                             # art box in texels (x0, y0, x1, y1)
STAMP = (236, 183, 278, 212)
PITCH_X, PITCH_Y = 75.0, 100.0


def srgb_to_lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


def catalog_tex():
    return cv2.resize(CAT, (TW, TH), interpolation=cv2.INTER_AREA)


def with_stamp(bgr):
    s = bgr.copy()
    x0, y0, x1, y1 = STAMP
    s[y0:y1, x0:x1] = (40, 40, 210)                   # red square
    cv2.circle(s, ((x0 + x1) // 2 - 4, (y0 + y1) // 2 - 3), 7, (245, 245, 245), -1)
    s[y1 - 9:y1, x0:x0 + 14] = (20, 20, 20)
    return s


def pokeball_mask():
    m = np.zeros((TH, TW), np.uint8)
    for cy in range(-30, TH + 60, 64):
        for cx in range(-30 + (cy // 64 % 2) * 32, TW + 60, 64):
            cv2.circle(m, (cx, cy), 22, 1, 3)
            cv2.line(m, (cx - 22, cy), (cx + 22, cy), 1, 3)
            cv2.circle(m, (cx, cy), 6, 1, 3)
    return m.astype(bool)


class Card:
    def __init__(self, cid: int, finish: str, sleeve: bool, stamp: bool, p: dict, seed: int = 0, scale: float = 1.0):
        self.cid, self.finish, self.sleeve, self.stamp = cid, finish, sleeve, stamp
        rng = np.random.default_rng(seed + cid)
        tex = catalog_tex()
        if stamp:
            tex = with_stamp(tex)
        self.tex_srgb = tex
        self.albedo = srgb_to_lin(tex[..., ::-1].astype(np.float32) / 255)          # RGB linear
        art = np.zeros((TH, TW), bool); x0, y0, x1, y1 = ART; art[y0:y1, x0:x1] = True
        stampm = np.zeros((TH, TW), bool)
        if stamp:
            sx0, sy0, sx1, sy1 = STAMP; stampm[sy0:sy1, sx0:sx1] = True
        border = np.zeros((TH, TW), bool); border[4:-4, 4:-4] = True               # card edge stays paper
        if finish == "Holo":
            foil = art & ~stampm
        elif finish == "Reverse Holo":
            foil = ~art & border
        else:
            foil = np.zeros((TH, TW), bool)
        # the Prize Pack holo's silver frame shines too (weaker): Brian's holos differ from the catalog
        # image around the art about twice as much as his normal cards do
        edge = np.zeros((TH, TW), bool); edge[4:-4, 4:-4] = True; edge[16:-16, 14:-14] = False
        self.edge_foil = edge if finish == "Holo" and p["holo_edge"] > 0 else np.zeros((TH, TW), bool)
        foil = foil | self.edge_foil
        self.foil = foil
        n = np.zeros((TH, TW, 3), np.float32); n[..., 2] = 1
        rough = np.full((TH, TW), p["holo_rough"] if finish == "Holo" else p["foil_rough"], np.float32)
        if finish == "Holo":                                                       # cosmos flakes
            cell = 4
            gh, gw = TH // cell + 1, TW // cell + 1
            tilt = np.radians(rng.normal(0, p["flake_tilt"], (gh, gw)))
            az = rng.uniform(0, 2 * np.pi, (gh, gw))
            flake = rng.random((gh, gw)) < p["flake_share"]
            tx = np.repeat(np.repeat(np.sin(tilt) * np.cos(az), cell, 0), cell, 1)[:TH, :TW]
            ty = np.repeat(np.repeat(np.sin(tilt) * np.sin(az), cell, 0), cell, 1)[:TH, :TW]
            fl = np.repeat(np.repeat(flake, cell, 0), cell, 1)[:TH, :TW] & foil
            n[fl, 0], n[fl, 1] = tx[fl], ty[fl]
            n[fl, 2] = np.sqrt(np.clip(1 - tx[fl] ** 2 - ty[fl] ** 2, 0, 1))
            rough[fl] = p["flake_rough"]
        if finish == "Reverse Holo":                                               # embossed balls
            pb = pokeball_mask() & foil
            rough[pb] = p["pattern_rough"]
            gy, gx = np.gradient(cv2.GaussianBlur(pb.astype(np.float32), (0, 0), 1.5))
            n[..., 0] -= gx * p["emboss"]; n[..., 1] -= gy * p["emboss"]
            n /= np.linalg.norm(n, axis=-1, keepdims=True)
        self.normal, self.rough = n, rough
        # ink over metal: the metal shows through tinted by the ink, the ink's own diffuse is weaker
        tint = np.clip(self.albedo / max(self.albedo.max(), 1e-3), 0, 1) ** 0.5
        metal = p.get("rh_metal", p["metal_refl"]) if finish == "Reverse Holo" else p["metal_refl"]
        strength = np.where(self.edge_foil & ~art, p["holo_edge"], metal)[..., None]
        self.foil_diffuse = p.get("rh_diffuse", p["foil_diffuse"]) if finish == "Reverse Holo" else p["foil_diffuse"]
        self.metal_col = (strength * tint).astype(np.float32)
        self.p = p
        self.w, self.h = TW, TH
        if scale != 1.0:                       # coarser texels (live frames are low-res anyway)
            w, h = round(TW * scale), round(TH * scale)
            rs = lambda a: cv2.resize(a.astype(np.float32), (w, h), interpolation=cv2.INTER_AREA)
            self.albedo, self.metal_col, self.rough = rs(self.albedo), rs(self.metal_col), rs(self.rough)
            nn = rs(self.normal); self.normal = nn / np.linalg.norm(nn, axis=-1, keepdims=True)
            self.foil, self.edge_foil = rs(self.foil) > 0.5, rs(self.edge_foil) > 0.5
            self.tex_srgb = cv2.resize(self.tex_srgb, (w, h), interpolation=cv2.INTER_AREA)
            self.w, self.h = w, h

    def world(self, col: int, row: int):
        cx, cy = (col - 1) * PITCH_X, (row - 0.5) * PITCH_Y
        u = (np.arange(self.w) + 0.5) / self.w - 0.5
        v = (np.arange(self.h) + 0.5) / self.h - 0.5
        X, Y = np.meshgrid(cx + u * CARD_W, cy + v * CARD_H)
        return np.stack([X, Y, np.zeros_like(X)], -1).astype(np.float32)


def _norm(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def ggx(n, v, l, alpha, f0):
    h = _norm(v + l)
    nl = np.clip((n * l).sum(-1), 0, 1); nv = np.clip((n * v).sum(-1), 1e-4, 1)
    nh = np.clip((n * h).sum(-1), 0, 1); vh = np.clip((v * h).sum(-1), 0, 1)
    a2 = alpha ** 2
    d = a2 / (np.pi * ((nh ** 2) * (a2 - 1) + 1) ** 2)
    k = alpha / 2
    g = (nl / (nl * (1 - k) + k)) * (nv / (nv * (1 - k) + k))
    fres = (1 - vh) ** 5
    if np.ndim(f0) == 3:
        f = f0 + (1 - f0) * fres[..., None]
        return (d * g / (4 * np.maximum(nl, 1e-4) * nv))[..., None] * f * nl[..., None]
    f = f0 + (1 - f0) * fres
    return d * g * f / (4 * np.maximum(nl, 1e-4) * nv) * nl


def shade(card: Card, P, cam, lights, scene):
    """Linear RGB radiance toward the camera, per texel."""
    p = card.p
    V = _norm(cam[None, None] - P)
    Ng = np.zeros_like(P); Ng[..., 2] = 1
    N = card.normal
    foil = card.foil[..., None]
    out = np.zeros(P.shape, np.float32)
    # room fill: diffuse only, plus mirror of the room in the specular lobes (dark toward the photographer)
    R = _norm(2 * (N * V).sum(-1, keepdims=True) * N - V)
    to_cam = V
    dark = np.clip(((R * to_cam).sum(-1) - np.cos(np.radians(25))) / (1 - np.cos(np.radians(25))) * 4, 0, 1)
    env = scene["env"] * (1 - 0.85 * dark)
    nv = np.clip((N * V).sum(-1), 0, 1)
    out += card.albedo * scene["env"] * np.where(foil, card.foil_diffuse, 1.0)
    blur = np.clip(card.rough * 2, 0, 1)
    env_spec = (1 - blur) * env + blur * scene["env"] * 0.9
    out += foil * card.metal_col * env_spec[..., None]
    fres_v = 0.04 + 0.96 * (1 - nv) ** 5
    out += (p["coat"] * fres_v * env_spec)[..., None]
    for L in lights:
        for pos, w in L["samples"]:
            Lv = pos[None, None] - P
            d2 = (Lv ** 2).sum(-1)
            Ld = _norm(Lv)
            E = L["power"] * w / d2                                   # irradiance factor (× cos in brdf)
            nl = np.clip((Ng * Ld).sum(-1), 0, 1)
            diff = card.albedo / np.pi * (nl * E)[..., None] * np.where(foil, card.foil_diffuse, 1.0)
            spec_coat = ggx(Ng, V, Ld, p["coat_rough"], 0.04) * p["coat"] * E
            spec_foil = ggx(N, V, Ld, card.rough, card.metal_col) * E[..., None] * foil
            out += (diff + spec_coat[..., None] + spec_foil) * L["color"]
    if card.sleeve:
        t = (1 - fres_v) ** 2
        out *= t[..., None]
        for L in lights:
            for pos, w in L["samples"]:
                Lv = pos[None, None] - P
                E = L["power"] * w / (Lv ** 2).sum(-1)
                out += (ggx(Ng, V, _norm(Lv), p["sleeve_rough"], 0.04) * E)[..., None] * L["color"]
        out += (fres_v * scene["env"] * 0.9 * (1 - 0.85 * np.clip(((_norm(2 * nv[..., None] * Ng - V) * to_cam).sum(-1)
                - np.cos(np.radians(25))) / (1 - np.cos(np.radians(25))) * 4, 0, 1)))[..., None]
    return out


def lamp(scene, n=4):
    c = np.array(scene["lamp_pos"], np.float32)
    pts = [c] + [c + scene["lamp_r"] * np.array([np.cos(a), np.sin(a), 0], np.float32)
                 for a in np.linspace(0, 2 * np.pi, n - 1, endpoint=False)]
    return {"samples": [(q, 1.0 / n) for q in pts], "power": scene["lamp_power"],
            "color": np.array([1.0, 0.86, 0.68], np.float32)}


def flash(cam, scene):
    pos = cam + np.array([10.0, 0, 0], np.float32)
    return {"samples": [(pos, 1.0)], "power": scene["flash_power"], "color": np.array([1.0, 1.0, 1.04], np.float32)}


def frame_radiance(cards, layout, cam, flash_on, scene):
    lights = [lamp(scene)] + ([flash(cam, scene)] if flash_on else [])
    rad = {}
    for c in cards:
        col, row = layout[c.cid]
        rad[c.cid] = shade(c, c.world(col, row), cam, lights, scene)
    # the table (bright cloth) under the cards, for auto-exposure: lamp + room + flash at the centre
    table = 0.75 * scene["env"] + 0.75 / np.pi * sum(
        L["power"] * w * max(0.0, float(pos[2])) / (float((pos ** 2).sum()) ** 1.5) for L in lights for pos, w in L["samples"])
    return rad, float(table)


def expose(rad, table, gain=None, target=0.18):
    """Auto-exposure: scale so the frame mean lands at `target` (cards are ~40% of the frame)."""
    lum = np.mean([(r @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean() for r in rad.values()])
    mean = 0.4 * lum + 0.6 * table
    want = target / max(mean, 1e-6)
    return want if gain is None else gain


def to_photo(r, gain, rng, blur=0.7, noise=1.2):
    x = lin_to_srgb(r * gain)
    img = (x[..., ::-1] * 255).astype(np.float32)
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    img += rng.normal(0, noise, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


DEFAULT = dict(foil_rough=0.28, holo_rough=0.28, flake_rough=0.07, flake_tilt=10.0, flake_share=0.12, pattern_rough=0.14,
               emboss=6.0, metal_refl=0.75, foil_diffuse=0.45, holo_edge=0.0, coat=1.0, coat_rough=0.32, sleeve_rough=0.05)
SCENE = dict(env=0.10, lamp_pos=(350.0, -700.0, 1900.0), lamp_r=120.0, lamp_power=1.5e6, flash_power=2.5e4)
CARDS = [(1, "Reverse Holo", True, False), (2, "Holo", True, True), (3, "Normal", True, False),
         (4, "Reverse Holo", False, False), (5, "Holo", False, True), (6, "Normal", False, True)]
LAYOUT = {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (0, 1), 5: (1, 1), 6: (2, 1)}


def make_cards(p=None, seed=0, scale=1.0):
    p = {**DEFAULT, **(p or {})}
    return [Card(cid, f, s, st, p, seed, scale) for cid, f, s, st in CARDS]
