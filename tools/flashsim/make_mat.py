"""Flash test mat: 3×2 card slots between ArUco markers (DICT_4X4_50, ids 0–19), US Letter landscape.
Writes mat.pdf (print at 100 %) and mat.json (marker corners and card centres in mm, origin top-left,
x right, y down) — from the markers in a video the camera pose is known in every frame."""
import json, sys
import numpy as np, cv2
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

W, H = 279.4, 215.9                 # Letter landscape
SLOT = (66.0, 91.0)                 # sleeve size; a bare card (63×88) sits in the middle
COL = 16.0                          # marker column
MARK = 12.0                         # marker side (6×6 modules of 2 mm)
GAP = 8.0                           # between the two rows
mx = (W - 4 * COL - 3 * SLOT[0]) / 2
my = (H - 2 * SLOT[1] - GAP) / 2
LABELS = ["1 · Reverse Holo, sleeve", "2 · Holo, sleeve", "3 · Normal, sleeve", "4 · Reverse Holo", "5 · Holo", "6 · Normal"]
DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)

def slot_xy(i):
    c, r = i % 3, i // 3
    return mx + COL + c * (SLOT[0] + COL), my + r * (SLOT[1] + GAP)

def main(out_pdf, out_json):
    pdf = canvas.Canvas(out_pdf, pagesize=(W * mm, H * mm))
    Y = lambda y: (H - y) * mm                      # top-left origin → PDF
    markers = []
    ys = [my + 12, my + SLOT[1] * 0.5, my + SLOT[1] + GAP / 2, my + SLOT[1] + GAP + SLOT[1] * 0.5, my + 2 * SLOT[1] + GAP - 12]
    mid = 0
    for k in range(4):
        cx = mx + COL / 2 + k * (SLOT[0] + COL)
        for cy in ys:
            img = cv2.aruco.generateImageMarker(DICT, mid, 6)
            x0, y0, m = cx - MARK / 2, cy - MARK / 2, MARK / 6
            pdf.setFillColorRGB(0, 0, 0)
            for j in range(6):
                for i in range(6):
                    if img[j, i] == 0:
                        pdf.rect((x0 + i * m) * mm, Y(y0 + (j + 1) * m), m * mm + 0.02, m * mm + 0.02, stroke=0, fill=1)
            markers.append({"id": mid, "corners": [[x0, y0], [x0 + MARK, y0], [x0 + MARK, y0 + MARK], [x0, y0 + MARK]]})
            pdf.setFont("Helvetica", 5)
            pdf.setFillColorRGB(0.55, 0.55, 0.55)
            pdf.drawCentredString(cx * mm, Y(cy + MARK / 2 + 2.6), str(mid))
            mid += 1
    cards = []
    for i in range(6):
        x, y = slot_xy(i)
        pdf.setStrokeColorRGB(0.6, 0.6, 0.6); pdf.setLineWidth(0.3)
        pdf.rect(x * mm, Y(y + SLOT[1]), SLOT[0] * mm, SLOT[1] * mm, stroke=1, fill=0)
        bx, by = x + (SLOT[0] - 63) / 2, y + (SLOT[1] - 88) / 2
        pdf.setDash(1.5, 1.5)
        pdf.rect(bx * mm, Y(by + 88), 63 * mm, 88 * mm, stroke=1, fill=0)
        pdf.setDash()
        pdf.setFont("Helvetica", 8); pdf.setFillColorRGB(0.45, 0.45, 0.45)
        pdf.drawCentredString((x + SLOT[0] / 2) * mm, Y(y + SLOT[1] / 2), LABELS[i])
        cards.append({"slot": i + 1, "label": LABELS[i], "center": [x + SLOT[0] / 2, y + SLOT[1] / 2]})
    # centre cross (between the middle cards) and scale bar
    cx, cy = W / 2, my + SLOT[1] + GAP / 2
    pdf.setStrokeColorRGB(0, 0, 0); pdf.setLineWidth(0.4)
    pdf.line((cx - 3) * mm, Y(cy), (cx + 3) * mm, Y(cy)); pdf.line(cx * mm, Y(cy - 3), cx * mm, Y(cy + 3))
    bx0, by = W / 2 - 50, H - my / 2
    pdf.setLineWidth(0.5)
    pdf.line(bx0 * mm, Y(by), (bx0 + 100) * mm, Y(by))
    for t in range(11):
        L = 2.5 if t % 5 == 0 else 1.5
        pdf.line((bx0 + 10 * t) * mm, Y(by - L), (bx0 + 10 * t) * mm, Y(by))
    pdf.setFont("Helvetica", 6); pdf.setFillColorRGB(0, 0, 0)
    pdf.drawString((bx0 + 102) * mm, Y(by + 0.8), "100 mm — measure it: the mat is only right at 100 %")
    pdf.setFont("Helvetica", 7)
    pdf.drawCentredString(W / 2 * mm, Y(my / 2 + 1), "PokéCollect flash test mat · print at 100 % / actual size (not “fit to page”) · Letter landscape · lay flat, cards in the slots")
    pdf.showPage(); pdf.save()
    json.dump({"paper_mm": [W, H], "marker_dict": "DICT_4X4_50", "marker_mm": MARK, "markers": markers, "cards": cards,
               "slot_mm": SLOT, "card_mm": [63, 88]}, open(out_json, "w"), indent=1)
    print(out_pdf, out_json, f"margins x {mx:.1f} y {my:.1f} mm, {len(markers)} markers")

if __name__ == "__main__":
    main(*(sys.argv[1:3] if len(sys.argv) > 2 else ("mat.pdf", "mat.json")))
