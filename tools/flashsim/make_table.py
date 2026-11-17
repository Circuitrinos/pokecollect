"""Expected flash reading per kind over the camera position → web/src/lib/flashTable.ts"""
import os
from paths import data, work, HERE
import pickle, sys, numpy as np, table as T
rows = pickle.load(open(work("table_0.pkl"), "rb")) + pickle.load(open(work("table_1.pkl"), "rb"))
SPLIT = 2.0
tabs = T.build(rows, SPLIT)
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "..", "web", "src", "lib", "flashTable.ts")
def arr(a):
    return "[" + ",".join(f"{v:.2f}".replace("0.", ".").replace("-.", "-.") for v in a.ravel()) + "]"
lines = [
    "// Erzeugt von tools/flashsim/make_table.py — nicht von Hand ändern.",
    "// Erwarteter Blitz-Messwert (flashShare) je Folierung, abhängig davon, wo die Kamera über der Karte steht:",
    "// tx, ty = Versatz der Kamera / Höhe über der Karte (Kartenkoordinaten: x nach rechts, y nach unten).",
    "// Aus der Nachbildung (Renderer an Brians Video v66 angepasst; je Messung Lampe aus/an mit nachgeregelter",
    f"// Belichtung; {len(rows)} Messungen, 6 Karten, Raumlicht hell bis dunkel, 25–43 cm). mu: Mittel, sd: Streuung.",
    "// Zeilen: ty von min bis max, Spalten: tx.",
    "import type { FlashKind } from \"./flashCheck\";",
    "",
    f"export const FLASH_GRID = {{ min: {T.GRID[0]}, step: {T.GRID[1] - T.GRID[0]:.2f}, n: {len(T.GRID)} }};",
    f"/** Raum hell / dunkel: Verhältnis Karte mit / ohne Lampe bei gleicher Belichtung (darunter: hell). */",
    f"export const FLASH_ROOM_SPLIT = {SPLIT};",
    "type Tab = Record<FlashKind, { mu: number[]; sd: number[] }>;",
    "export const FLASH_TABLE: { bright: Tab; dark: Tab } = {",
]
for name, t in zip(("bright", "dark"), tabs):
    lines.append(f"  {name}: {{")
    for k in T.KINDS:
        mu, sd = t[k]
        lines.append(f"    \"{k}\": {{")
        lines.append(f"      mu: {arr(mu)},")
        lines.append(f"      sd: {arr(sd)},")
        lines.append("    },")
    lines.append("  },")
lines.append("};")
open(out, "w").write("\n".join(lines) + "\n")
print("wrote", out, sum(len(l) for l in lines), "chars")
