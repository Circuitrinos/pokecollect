"""Expected flash reading per kind over the camera position → web/src/lib/flashTable.ts

Every simulated reading gets a random smooth fall-off of room light (shadow of hand / phone, one lamp; up to
60 % in a dark room, 30 % in a bright one, any direction). Measure per reading as in the app: flashShare in a bright room (flash < FLASH_ROOM_SPLIT ×
room light), flashShareFlat in a dark one. Inputs: work/table_0.pkl, dark_0.pkl, table_1.pkl, dark_1.pkl
(gen_table.py 0 60 / 1 30 for the rooms 1–0.04, gen_table.py 0 40 0.012,0.004 _dark … for the dark ones)."""
import os, pickle, sys
import numpy as np
from paths import work, HERE
import appflash as A, table as T, shadow as SH

SPLIT = 2.0
rng = np.random.default_rng(11)
rows = []
for f in ("table_0.pkl", "dark_0.pkl", "table_1.pkl", "dark_1.pkl"):
    for r in pickle.load(open(work(f), "rb")):
        depth = rng.uniform(0, 0.6)
        if r["room"] < SPLIT:       # bright room: a shadow changes little; deeper ones made real v66 worse (58 → 52 right)
            depth *= 0.5
        d, l = SH.shade(r["dark_px"], r["lit_px"], r["room"] ** -0.73, rng, depth)
        s = A.flash_share(d, l) if r["room"] < SPLIT else SH.plane_share(d, l, 1)
        rows.append(dict(kind=r["kind"], tx=r["tx"], ty=r["ty"], room=r["room"], share=s))
tabs = T.build(rows, SPLIT)
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "web", "src", "lib", "flashTable.ts")
def arr(a):
    return "[" + ",".join(f"{v:.2f}".replace("0.", ".") for v in a.ravel()) + "]"
lines = [
    "// Erzeugt von tools/flashsim/make_table.py — nicht von Hand ändern.",
    "// Erwarteter Blitz-Messwert je Folierung, abhängig davon, wo die Kamera über der Karte steht:",
    "// tx, ty = Versatz der Kamera / Höhe über der Karte (Kartenkoordinaten: x nach rechts, y nach unten).",
    "// Aus der Nachbildung (Renderer an Brians Video v66 angepasst; je Messung Lampe aus/an mit nachgeregelter",
    f"// Belichtung und Schatten bis 60 % (hell: 30 %); {len(rows)} Messungen, 6 Karten, Raum hell bis sehr dunkel, 25–43 cm).",
    "// bright: flashShare (Lampe < FLASH_ROOM_SPLIT × Raumlicht), dark: flashShareFlat. mu: Mittel, sd: Streuung.",
    "// Zeilen: ty von min bis max, Spalten: tx.",
    "import type { FlashKind } from \"./flashCheck\";",
    "",
    f"export const FLASH_GRID = {{ min: {T.GRID[0]}, step: {T.GRID[1] - T.GRID[0]:.2f}, n: {len(T.GRID)} }};",
    "/** Raum hell / dunkel: Verhältnis Karte mit / ohne Lampe bei gleicher Belichtung (darunter: hell). */",
    f"export const FLASH_ROOM_SPLIT = {SPLIT};",
    "type Tab = Record<FlashKind, { mu: number[]; sd: number[] }>;",
    "export const FLASH_TABLE: { bright: Tab; dark: Tab } = {",
]
for name, t in zip(("bright", "dark"), tabs):
    lines.append(f"  {name}: {{")
    for k in T.KINDS:
        mu, sd = t[k]
        lines += [f"    \"{k}\": {{", f"      mu: {arr(mu)},", f"      sd: {arr(sd)},", "    },"]
    lines.append("  },")
lines.append("};")
open(out, "w").write("\n".join(lines) + "\n")
print("wrote", out, len(rows), "readings")
