"""Flash test mat: 3×2 card slots between ArUco markers (DICT_4X4_50, ids 0–19), US Letter landscape.
Writes mat.pdf (print at 100 %) and mat.json (marker corners and card centres in mm, origin top-left,
x right, y down) — from the markers in a video the camera pose is known in every frame."""
import json, sys
import numpy as np, cv2
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

W, H = 279.4, 215.9                 # Letter landscape
SLOT = (71.0, 99.0)                 # sleeve, as measured on Brian's (17.11.: 70–72 × 98, card at the bottom)
CARD_IN = (4.0, 8.0)                # where the card sits in the slot (left, top); 63×88
COL = 14.0                          # marker column between / beside the slots
MARK = 9.5                          # marker side (6×6 modules); black at least 7 mm from the paper edge
GAP = 5.0                           # between the two rows (the 100 mm bar runs along it)
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
    row2 = my + SLOT[1] + GAP
    outer = [my + 7.5, my + SLOT[1] * 0.5, my + SLOT[1] + GAP / 2, row2 + SLOT[1] * 0.5, row2 + SLOT[1] - 7.5]
    inner = [my + 7.5, my + SLOT[1] * 0.5, row2 + SLOT[1] * 0.5, row2 + SLOT[1] - 7.5]   # the bar runs between the rows
    mid = 0
    for k in range(4):
        cx = mx + COL / 2 + k * (SLOT[0] + COL)
        for cy in (outer if k in (0, 3) else inner):
            img = cv2.aruco.generateImageMarker(DICT, mid, 6)
            x0, y0, m = cx - MARK / 2, cy - MARK / 2, MARK / 6
            pdf.setFillColorRGB(0, 0, 0)
            for j in range(6):
                for i in range(6):
                    if img[j, i] == 0:
                        pdf.rect((x0 + i * m) * mm, Y(y0 + (j + 1) * m), m * mm + 0.02, m * mm + 0.02, stroke=0, fill=1)
            markers.append({"id": mid, "corners": [[x0, y0], [x0 + MARK, y0], [x0 + MARK, y0 + MARK], [x0, y0 + MARK]]})
            mid += 1
    cards = []
    for i in range(6):
        x, y = slot_xy(i)
        pdf.setStrokeColorRGB(0.6, 0.6, 0.6); pdf.setLineWidth(0.3)
        pdf.rect(x * mm, Y(y + SLOT[1]), SLOT[0] * mm, SLOT[1] * mm, stroke=1, fill=0)
        bx, by = x + CARD_IN[0], y + CARD_IN[1]
        pdf.setDash(1.5, 1.5)
        pdf.rect(bx * mm, Y(by + 88), 63 * mm, 88 * mm, stroke=1, fill=0)
        pdf.setDash()
        pdf.setFont("Helvetica", 8); pdf.setFillColorRGB(0.45, 0.45, 0.45)
        pdf.drawCentredString((bx + 31.5) * mm, Y(by + 44), LABELS[i])
        cards.append({"slot": i + 1, "label": LABELS[i], "center": [bx + 31.5, by + 44]})
    # print check, under the cards: 100 mm bar along the gap between the rows
    gy = my + SLOT[1] + GAP / 2
    bx0 = mx + COL + 8
    pdf.setStrokeColorRGB(0, 0, 0); pdf.setLineWidth(0.5)
    pdf.line(bx0 * mm, Y(gy), (bx0 + 100) * mm, Y(gy))
    for t in range(11):
        L = 1.8 if t % 5 == 0 else 1.0
        pdf.line((bx0 + 10 * t) * mm, Y(gy - L), (bx0 + 10 * t) * mm, Y(gy + L))
    pdf.setFont("Helvetica", 5.5); pdf.setFillColorRGB(0, 0, 0)
    pdf.drawString((bx0 + 101.5) * mm, Y(gy + 0.9), "← 100 mm: measure it before laying the cards on")
    x2, y2 = slot_xy(1)
    pdf.setFont("Helvetica", 6.5); pdf.setFillColorRGB(0.35, 0.35, 0.35)
    for n, t in enumerate(["PokéCollect flash test mat", "Letter, landscape, print at 100 % / actual size", "(not “fit to page”) — check the bar between the rows"]):
        pdf.drawCentredString((x2 + SLOT[0] / 2) * mm, Y(y2 + CARD_IN[1] + 8 + n * 3.2), t)
    pdf.showPage(); pdf.save()
    json.dump({"paper_mm": [W, H], "marker_dict": "DICT_4X4_50", "marker_mm": MARK, "markers": markers, "cards": cards,
               "slot_mm": SLOT, "card_mm": [63, 88], "version": 2}, open(out_json, "w"), indent=1)
    print(out_pdf, out_json, f"margins x {mx:.1f} y {my:.1f} mm, {len(markers)} markers")

if __name__ == "__main__":
    main(*(sys.argv[1:3] if len(sys.argv) > 2 else ("mat.pdf", "mat.json")))
