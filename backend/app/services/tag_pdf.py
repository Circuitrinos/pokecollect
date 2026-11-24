"""
Druckbögen für die Hüllen-Aufkleber (services/sleeve_tags.py) als PDF.

Bewusst ohne PDF-Bibliothek: Ein Bogen besteht nur aus Rechtecken (die QR-
Module), Linien (Kalibrierbogen) und etwas Text in den PDF-Standardschriften
(Helvetica, nicht eingebettet). Das kann ein kleiner Schreiber selbst — eine
große Abhängigkeit (und deren Sicherheitsupdates) braucht es dafür nicht.
Die QR-Codes selbst kommen von segno (reines Python, ohne Abhängigkeiten).

Alle Maße rechnen in Druckerpunkten zu 1/600 Zoll („dots"): Der Brother-Laser
druckt mit 600 dpi, und ein QR-Modul aus ganzen Druckerpunkten wird scharf
statt verschmiert. Etiketten-Raster, Code-Position und Versatz liegen darum
immer auf ganzen dots.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Iterable

import segno

DPI = 600
PT = 72 / DPI                      # 1 dot in PDF-Punkten
MM = DPI / 25.4                    # dots je Millimeter
PAGE_W, PAGE_H = 8.5 * DPI, 11 * DPI   # US Letter


@dataclass(frozen=True)
class Bogen:
    """Etikettenbogen: Raster in dots, Ursprung oben links."""
    key: str
    name: str
    cols: int
    rows: int
    label_w: int
    label_h: int
    left: float      # linker Rand bis zur ersten Spalte
    top: float       # oberer Rand bis zur ersten Zeile
    pitch_x: float   # Abstand Etikett-Anfang zu Etikett-Anfang
    pitch_y: float
    radius: int

    @property
    def per_sheet(self) -> int:
        return self.cols * self.rows

    def origin(self, slot: int) -> tuple[int, int]:
        """Oben-links-Ecke von Platz `slot` (0-basiert, zeilenweise), auf ganze
        dots gerundet — dann liegen auch die QR-Module auf dem Druckerraster."""
        r, c = divmod(slot, self.cols)
        return round(self.left + c * self.pitch_x), round(self.top + r * self.pitch_y)


# OfficeSmartLabels LWS144-1-S: 0,75" × 0,5", 144 je Letter-Bogen, 9 × 16.
# Raster aus der Word-Vorlage des Herstellers (LWS144-1-S.docx, 144 Formen mit
# absoluter Lage, Ausgleichsgerade auf ±0,07 mm): erste Spalte 0,4062",
# Spaltenabstand 0,8625"; erste Zeile 0,5935", Zeilenabstand 0,6204";
# Ecken r = 0,0542". Links/rechts ist der Rand laut Vorlage ungleich
# (0,406"/0,444") — die tatsächliche Lage auf Brians Drucker gleicht der
# Versatz aus dem Kalibrierbogen aus.
LWS144 = Bogen("LWS144", "OfficeSmartLabels LWS144-1-S (0.75 × 0.5 in, 144/sheet)",
               cols=9, rows=16, label_w=450, label_h=300, left=243.7, top=356.1,
               pitch_x=517.5, pitch_y=372.25, radius=33)
BOEGEN = {LWS144.key: LWS144}

# Inhalt eines Etiketts (dots, relativ zur Etikett-Ecke). Der Code bekommt
# rundum ≥ 37 dots (1,6 mm) Weiß: Das trägt einen Druckversatz von ~0,8 mm
# und lässt dem Leser noch 2 Module Ruhezone — rund um den Aufkleber liegt das
# bunte Kartenbild, die Ruhezone muss auf dem Etikett selbst liegen.
QR_BOX, QR_INSET = 225, 37
# Code rechts daneben in zwei Zeilen ("K7" / "M2"): so groß wie möglich —
# Versalhöhe 60 dots ≈ 2,5 mm. Breite Paare ("WM") werden schmaler gezogen.
TEXT_X0, TEXT_X1 = 276, 424
TEXT_SIZE = 84
TEXT_GAP = 0.3                   # Zeilenabstand in em

# Breiten der Helvetica-Bold-Zeichen in 1/1000 em (AFM-Daten)
_W_BOLD = {**{str(d): 556 for d in range(10)}, "#": 556,
           "A": 722, "B": 722, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722,
           "I": 278, "J": 556, "K": 722, "L": 611, "M": 833, "N": 722, "O": 778, "P": 667,
           "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944, "X": 667,
           "Y": 667, "Z": 611}
_W_REG = 556                     # Näherung für Laufschrift (Mittelwert reicht)


def qr_matrix(content: str) -> list[bytes]:
    """QR-Matrix ohne Ruhezone; Fehlerkorrektur M (≈15 % — Kratzer, Knicke)."""
    qr = segno.make(content, error="m", boost_error=False, micro=False)
    return [bytes(row) for row in qr.matrix]


def qr_version(content: str) -> int:
    return segno.make(content, error="m", boost_error=False, micro=False).version


# ── PDF-Bausteine ────────────────────────────────────────────────────────────

def _n(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _pt(x_dots: float, y_dots_from_top: float) -> tuple[str, str]:
    return _n(x_dots * PT), _n((PAGE_H - y_dots_from_top) * PT)


def _esc(text: str) -> bytes:
    raw = text.encode("cp1252", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class _Seite:
    def __init__(self) -> None:
        self.ops: list[bytes] = []

    def add(self, s: str | bytes) -> None:
        self.ops.append(s.encode("ascii") if isinstance(s, str) else s)

    def rects(self, rects: Iterable[tuple[int, int, int, int]], gray: float = 0) -> None:
        """Gefüllte Rechtecke (x, y von oben, w, h in dots) als EIN Pfad —
        nebeneinander liegende Module bekommen so keine Haarfugen."""
        parts = [f"{_n(gray)} g"]
        for x, y, w, h in rects:
            px, py = _pt(x, y + h)
            parts.append(f"{px} {py} {_n(w * PT)} {_n(h * PT)} re")
        parts.append("f")
        self.add("\n".join(parts))

    def line(self, x0, y0, x1, y1, width_pt: float = 0.3, gray: float = 0) -> None:
        a, b = _pt(x0, y0)
        c, d = _pt(x1, y1)
        self.add(f"{_n(gray)} G {_n(width_pt)} w {a} {b} m {c} {d} l S")

    def rounded(self, x, y, w, h, r, width_pt: float = 0.3, gray: float = 0) -> None:
        k = 0.5523 * r
        x1, y1 = x + w, y + h
        p = [f"{_n(gray)} G {_n(width_pt)} w"]
        p.append("{} {} m".format(*_pt(x + r, y)))
        p.append("{} {} l".format(*_pt(x1 - r, y)))
        p.append("{} {} {} {} {} {} c".format(*_pt(x1 - r + k, y), *_pt(x1, y + r - k), *_pt(x1, y + r)))
        p.append("{} {} l".format(*_pt(x1, y1 - r)))
        p.append("{} {} {} {} {} {} c".format(*_pt(x1, y1 - r + k), *_pt(x1 - r + k, y1), *_pt(x1 - r, y1)))
        p.append("{} {} l".format(*_pt(x + r, y1)))
        p.append("{} {} {} {} {} {} c".format(*_pt(x + r - k, y1), *_pt(x, y1 - r + k), *_pt(x, y1 - r)))
        p.append("{} {} l".format(*_pt(x, y + r)))
        p.append("{} {} {} {} {} {} c".format(*_pt(x, y + r - k), *_pt(x + r - k, y), *_pt(x + r, y)))
        p.append("S")
        self.add("\n".join(p))

    def text(self, x, baseline_y, size_dots, s: str, bold: bool = False, gray: float = 0,
             anchor: str = "left", max_w: float = 0) -> None:
        """Text; mit max_w (dots) wird zu breiter Text waagrecht gestaucht (Tz)."""
        size_pt = size_dots * PT
        w = _breite(s, bold) * size_dots
        scale = min(1.0, max_w / w) if max_w and w else 1.0
        w *= scale
        if anchor != "left":
            x = x - (w if anchor == "right" else w / 2)
        px, py = _pt(x, baseline_y)
        font = "F2" if bold else "F1"
        self.add(b"BT " + f"{_n(gray)} g /{font} {_n(size_pt)} Tf {_n(scale * 100)} Tz {px} {py} Td (".encode("ascii")
                 + _esc(s) + b") Tj ET")


def _breite(s: str, bold: bool) -> float:
    """Textbreite in em (Code-Zeichen exakt, sonst Näherung)."""
    if bold and all(ch in _W_BOLD for ch in s):
        return sum(_W_BOLD[ch] for ch in s) / 1000
    return len(s) * _W_REG / 1000


def _pdf(seiten: list[_Seite], title: str) -> bytes:
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    catalog = add(b"")            # Platzhalter, unten gefüllt
    pages = add(b"")
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for s in seiten:
        data = zlib.compress(b"\n".join(s.ops), 9)
        content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] " % pages
            + b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>" % (f1, f2, content)))
    objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R /ViewerPreferences << /PrintScaling /None >> >>" % pages
    objs[pages - 1] = (b"<< /Type /Pages /Count %d /Kids [" % len(kids)
                       + b" ".join(b"%d 0 R" % k for k in kids) + b"] >>")
    info = add(b"<< /Title (" + _esc(title) + b") /Producer (PokeCollect) >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, catalog, info, xref))
    return bytes(out)


# ── Bögen ────────────────────────────────────────────────────────────────────

@dataclass
class Aufkleber:
    slot: int          # Platz auf dem Bogen, 0-basiert
    content: str       # Inhalt des QR-Codes (Link)
    label: str         # lesbarer Code daneben ("K7M2")


TEXTE = {
    "EN": {
        "head": "PokéCollect sleeve tags  ·  sheet {sheet}  ·  {n} stickers",
        "print": "Print at Actual size / 100 %, not “Fit to page”  ·  {bogen}",
        "cal_head": "PokéCollect sleeve tags  ·  printer calibration  ·  {bogen}",
        "cal_1": "Print at Actual size / 100 % on a label sheet. Each box should sit on a label’s cut edge.",
        "cal_2": "At the four corner labels, read where the cut edge crosses the mm scales and enter",
        "cal_3": "those numbers as the printer offset (right +, down +). Corners that disagree: send me the readings.",
        "cal_offset": "Offset printed: right {x} mm, down {y} mm",
    },
    "DE": {
        "head": "PokéCollect Hüllen-Aufkleber  ·  Bogen {sheet}  ·  {n} Aufkleber",
        "print": "In Originalgröße / 100 % drucken, nicht „An Seite anpassen“  ·  {bogen}",
        "cal_head": "PokéCollect Hüllen-Aufkleber  ·  Drucker kalibrieren  ·  {bogen}",
        "cal_1": "In Originalgröße / 100 % auf einen Etikettenbogen drucken. Jeder Rahmen sollte auf der Schnittkante liegen.",
        "cal_2": "An den vier Eck-Etiketten ablesen, wo die Schnittkante die mm-Skalen kreuzt, und diese Werte",
        "cal_3": "als Druckversatz eintragen (rechts +, unten +). Weichen die Ecken voneinander ab: Werte melden.",
        "cal_offset": "Gedruckter Versatz: rechts {x} mm, unten {y} mm",
    },
}


def _versatz(offset_mm: tuple[float, float]) -> tuple[int, int]:
    return round(offset_mm[0] * MM), round(offset_mm[1] * MM)


def _kopf(seite: _Seite, zeile1: str, zeile2: str, dy: int) -> None:
    seite.text(PAGE_W / 2, 150 + dy, 60, zeile1, bold=True, anchor="center")
    seite.text(PAGE_W / 2, 235 + dy, 50, zeile2, gray=0.35, anchor="center")


def _aufkleber(seite: _Seite, bogen: Bogen, a: Aufkleber, dx: int, dy: int) -> None:
    x0, y0 = bogen.origin(a.slot)
    x0, y0 = x0 + dx, y0 + dy
    m = qr_matrix(a.content)
    n = len(m)
    mod = QR_BOX // n
    off = (QR_BOX - n * mod) // 2
    qx, qy = x0 + QR_INSET + off, y0 + QR_INSET + off
    rects = []
    for r, row in enumerate(m):
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                rects.append((qx + start * mod, qy + r * mod, (c - start) * mod, mod))
            else:
                c += 1
    seite.rects(rects)
    # Code in zwei Zeilen ("K7" / "M2"), senkrecht mittig
    half = (len(a.label) + 1) // 2
    zeilen = [a.label[:half], a.label[half:]] if len(a.label) > 3 else [a.label]
    cap = 0.718 * TEXT_SIZE
    hoehe = len(zeilen) * cap + (len(zeilen) - 1) * TEXT_GAP * TEXT_SIZE
    mitte_x = x0 + (TEXT_X0 + TEXT_X1) / 2
    base = y0 + (bogen.label_h - hoehe) / 2 + cap
    for i, z in enumerate(zeilen):
        seite.text(mitte_x, base + i * (cap + TEXT_GAP * TEXT_SIZE), TEXT_SIZE, z, bold=True,
                   anchor="center", max_w=TEXT_X1 - TEXT_X0)


def sheet_pdf(bogen: Bogen, blaetter: list[tuple[int, list[Aufkleber]]],
              offset_mm: tuple[float, float] = (0.0, 0.0), lang: str = "EN") -> bytes:
    """
    Druckbögen. `blaetter`: je Bogen (Bogen-Nr., Aufkleber). Freie Plätze
    (bereits benutzte Etiketten) bleiben leer.
    """
    tx = TEXTE.get(lang, TEXTE["EN"])
    dx, dy = _versatz(offset_mm)
    seiten = []
    for nr, aufkleber in blaetter:
        s = _Seite()
        _kopf(s, tx["head"].format(sheet=nr, n=len(aufkleber)),
              tx["print"].format(bogen=bogen.name), dy)
        for a in aufkleber:
            _aufkleber(s, bogen, a, dx, dy)
        seiten.append(s)
    return _pdf(seiten, "PokéCollect sleeve tags")


def _skala(s: _Seite, x: float, y: float, horizontal: bool) -> None:
    """mm-Skala quer über eine Etikett-Kante (bei x bzw. y). Ablesewert dort,
    wo die Schnittkante die Skala kreuzt = nötiger Versatz (rechts/unten +)."""
    for i in range(-6, 7):            # −3 … +3 mm in 0,5-mm-Schritten
        d = i * 0.5 * MM
        lang = 34 if i % 2 == 0 else 18
        if horizontal:
            s.line(x + d, y - lang / 2, x + d, y + lang / 2, width_pt=0.6 if i == 0 else 0.35)
        else:
            s.line(x - lang / 2, y + d, x + lang / 2, y + d, width_pt=0.6 if i == 0 else 0.35)
    if horizontal:
        s.line(x - 3 * MM, y, x + 3 * MM, y, width_pt=0.2, gray=0.5)
    else:
        s.line(x, y - 3 * MM, x, y + 3 * MM, width_pt=0.2, gray=0.5)
    # 0 ist die gedruckte Kante selbst (dicker Strich) — beschriftet nur ±2
    for v in (-2, 2):
        d = v * MM
        if horizontal:
            s.text(x + d, y - 26, 34, f"{v:+d}", anchor="center")
        else:
            s.text(x + 30, y + d + 12, 34, f"{v:+d}")


def calibration_pdf(bogen: Bogen, offset_mm: tuple[float, float] = (0.0, 0.0), lang: str = "EN") -> bytes:
    """Kalibrierbogen: Rahmen auf jeder Schnittkante, 1-mm-Sicherheitsrahmen
    innen und mm-Skalen an den vier Eck-Etiketten."""
    tx = TEXTE.get(lang, TEXTE["EN"])
    dx, dy = _versatz(offset_mm)
    s = _Seite()
    s.text(PAGE_W / 2, 95 + dy, 56, tx["cal_head"].format(bogen=bogen.name), bold=True, anchor="center")
    for i, key in enumerate(("cal_1", "cal_2", "cal_3")):
        s.text(PAGE_W / 2, 160 + i * 52 + dy, 44, tx[key], gray=0.25, anchor="center")
    s.text(PAGE_W / 2, 160 + 3 * 52 + dy, 44,
           tx["cal_offset"].format(x=_n(offset_mm[0]), y=_n(offset_mm[1])), bold=True, anchor="center")
    inset = round(1 * MM)
    for slot in range(bogen.per_sheet):
        x, y = bogen.origin(slot)
        x, y = x + dx, y + dy
        s.rounded(x, y, bogen.label_w, bogen.label_h, bogen.radius, width_pt=0.4)
        s.rounded(x + inset, y + inset, bogen.label_w - 2 * inset, bogen.label_h - 2 * inset,
                  max(bogen.radius - inset, 4), width_pt=0.25, gray=0.6)
    ecken = {0: (1, 1), bogen.cols - 1: (-1, 1),
             bogen.per_sheet - bogen.cols: (1, -1), bogen.per_sheet - 1: (-1, -1)}
    for slot, (sx, sy) in ecken.items():
        x, y = bogen.origin(slot)
        x, y = x + dx, y + dy
        kante_x = x if sx > 0 else x + bogen.label_w        # linke bzw. rechte Kante
        kante_y = y if sy > 0 else y + bogen.label_h        # obere bzw. untere Kante
        _skala(s, kante_x, y + bogen.label_h / 2, horizontal=True)
        _skala(s, x + bogen.label_w / 2, kante_y, horizontal=False)
    return _pdf([s], "PokéCollect sleeve tags – calibration")
