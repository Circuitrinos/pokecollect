"""
Hüllen-Aufkleber (Brian 23.11.: OfficeSmartLabels LWS144-1-S + Hüllen): jede
eigene Karte bekommt auf ihrer Hülle einen kleinen QR-Aufkleber.

Ablauf
  1. Bögen drucken — blanko, fortlaufend nummeriert (je Bogen 144 Aufkleber).
  2. Aufkleber auf eine Hülle kleben und mit der Handykamera scannen: Der QR-
     Code ist ein Link (…/T/<Code>). Ist er noch frei, wählt man die Karte
     (die zuletzt erfassten stehen oben); sonst öffnet sich gleich die Karte.

Code = 4 Zeichen Crockford-Base32 (Ziffern + Großbuchstaben ohne I, L, O, U —
nichts zum Verwechseln), z. B. "K7M2": reicht für 32⁴ ≈ 1 Mio. Aufkleber und
wird nie länger (Brian 23.11.: „with thousands of cards, the number will
eventually get too long"). Der Code ist die fortlaufende Nummer, umkehrbar
verwürfelt: Nachbar-Aufkleber sehen ganz verschieden aus (kein Griff zum
falschen), und ein vertippter Code trifft fast immer einen nie gedruckten
(„nicht gefunden") statt still einen anderen eigenen Aufkleber.

Der Link wird für den QR-Code GROSS geschrieben ("HTTPS://TCG.EXAMPLE.COM/T/
K7M2"): Nur so passt er in den QR-Alphanumerik-Modus und damit in einen
Code der Version 2 (25 × 25 Module) — kleingeschrieben bräuchte er Version 3
(29 × 29), und jedes Modul würde auf dem 12,7 mm hohen Etikett kleiner.
Schema und Host sind ohnehin unabhängig von Groß/Klein; den Pfad /T/ legt die
Web-App großgeschrieben an.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.card import PokemonCard
from app.models.sleeve_tag import SleeveTag, TagSheet
from app.models.user import UserSetting
from app.services import tag_pdf
from app.tenancy import current_user_id

SETTINGS_KEY = "sleeve_tags"

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"      # Crockford-Base32
CODE_LEN = 4
SPACE = len(ALPHABET) ** CODE_LEN                   # 2**20
MAX_SERIAL = SPACE - 1
# Umkehrbares Verwürfeln auf 20 Bit: Feistel-Netz mit 4 Runden auf zwei
# 10-Bit-Hälften — umkehrbar für jede Rundenfunktion, und anders als eine
# bloße Multiplikation hängen auch die letzten Zeichen von allen Bits ab.
_KEYS = (0x2C9, 0x1B7, 0x3A5, 0x0D3)
_LOOKALIKE = str.maketrans({"O": "0", "I": "1", "L": "1"})


def _f(r: int, k: int) -> int:
    x = (r * 0x2D5 + k) & 0x3FF
    x ^= x >> 4
    x = (x * 0x1E7 + (k >> 2)) & 0x3FF
    return x ^ (x >> 5)


def _mix(v: int) -> int:
    left, right = v >> 10, v & 0x3FF
    for k in _KEYS:
        left, right = right, left ^ _f(right, k)
    return (left << 10) | right


def _unmix(v: int) -> int:
    left, right = v >> 10, v & 0x3FF
    for k in reversed(_KEYS):
        left, right = right ^ _f(left, k), left
    return (left << 10) | right


def code_for(serial: int) -> str:
    """Laufende Nummer → 4-Zeichen-Code ("K7M2")."""
    x, out = _mix(serial), []
    for _ in range(CODE_LEN):
        x, r = divmod(x, len(ALPHABET))
        out.append(ALPHABET[r])
    return "".join(reversed(out))


class CodeError(ValueError):
    """Eingabe ist kein gültiger Aufkleber-Code."""

    def __init__(self, reason: str = "format"):
        super().__init__(reason)
        self.reason = reason


_URL_CODE = re.compile(r"/t/([0-9a-z]{4})/?$", re.IGNORECASE)


def parse(raw: str) -> int:
    """
    Code oder ganzer Link → laufende Nummer. Nachsichtig beim Abtippen:
    klein geschrieben, mit Leerzeichen/Bindestrich ("k7-m2"), O statt 0,
    I/L statt 1.
    """
    s = (raw or "").strip()
    m = _URL_CODE.search(s)
    if m:
        s = m.group(1)
    s = s.lstrip("#").replace(" ", "").replace("-", "").upper().translate(_LOOKALIKE)
    if len(s) != CODE_LEN or any(ch not in ALPHABET for ch in s):
        raise CodeError("format")
    x = 0
    for ch in s:
        x = x * len(ALPHABET) + ALPHABET.index(ch)
    serial = _unmix(x)
    if serial == 0:
        raise CodeError("format")
    return serial


def qr_content(base_url: str, serial: int) -> str:
    """Link für den QR-Code (siehe Moduldoku: groß geschrieben, wenn möglich)."""
    u = urlsplit(base_url.strip())
    path = u.path.rstrip("/")
    code = code_for(serial)
    if not path and not u.query and not u.fragment:
        return f"{u.scheme}://{u.netloc}".upper() + f"/T/{code}"
    return f"{u.scheme}://{u.netloc}{path}/T/{code}"


def valid_base_url(value: str) -> bool:
    u = urlsplit((value or "").strip())
    return u.scheme in ("http", "https") and bool(u.netloc) and not u.query and not u.fragment


# ── Einstellungen (je Benutzer) ─────────────────────────────────────────────

@dataclass
class DruckEinstellung:
    base_url: str = ""
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    bogen: str = tag_pdf.LWS144.key


def get_settings(db: Session) -> DruckEinstellung:
    uid = current_user_id(db)
    row = db.get(UserSetting, (uid, SETTINGS_KEY)) if uid is not None else None
    out = DruckEinstellung()
    if row and row.value:
        try:
            data = json.loads(row.value)
        except ValueError:
            data = {}
        out.base_url = str(data.get("base_url") or "")
        out.offset_x_mm = float(data.get("offset_x_mm") or 0)
        out.offset_y_mm = float(data.get("offset_y_mm") or 0)
        if data.get("bogen") in tag_pdf.BOEGEN:
            out.bogen = data["bogen"]
    return out


def save_settings(db: Session, s: DruckEinstellung) -> None:
    uid = current_user_id(db)
    value = json.dumps({"base_url": s.base_url, "offset_x_mm": s.offset_x_mm,
                        "offset_y_mm": s.offset_y_mm, "bogen": s.bogen})
    row = db.get(UserSetting, (uid, SETTINGS_KEY))
    if row is None:
        db.add(UserSetting(user_id=uid, key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    db.commit()


# ── Bögen ────────────────────────────────────────────────────────────────────

def new_sheets(db: Session, count: int, start_slot: int = 0, bogen_key: str = tag_pdf.LWS144.key) -> list[TagSheet]:
    """
    `count` Bögen anlegen. Der erste darf erst ab `start_slot` beginnen (Rest
    eines angebrochenen Bogens); die übrigen sind voll.
    """
    bogen = tag_pdf.BOEGEN[bogen_key]
    # Nummern sind global eindeutig: gleichzeitige Drucke zweier Benutzer
    # dürfen sich nicht dieselben Nummern greifen.
    db.execute(text("LOCK TABLE sleeve_tags IN EXCLUSIVE MODE"))
    nxt = (db.scalar(select(func.max(SleeveTag.serial)).execution_options(all_users=True)) or 0) + 1
    if nxt + count * bogen.per_sheet > MAX_SERIAL:
        raise ValueError("serials exhausted")
    nr = (db.scalar(select(func.max(TagSheet.nr))) or 0) + 1     # je Benutzer (tenancy)
    sheets = []
    for i in range(count):
        start = start_slot if i == 0 else 0
        sheet = TagSheet(nr=nr + i, bogen=bogen.key, start_slot=start)
        db.add(sheet)
        db.flush()
        for slot in range(start, bogen.per_sheet):
            db.add(SleeveTag(serial=nxt, sheet_id=sheet.id, slot=slot))
            nxt += 1
        sheets.append(sheet)
    db.commit()
    return sheets


def sheet_tags(db: Session, sheet_id: int) -> list[SleeveTag]:
    return list(db.scalars(select(SleeveTag).where(SleeveTag.sheet_id == sheet_id).order_by(SleeveTag.slot)))


def sheet_summaries(db: Session) -> list[dict]:
    rows = db.execute(
        select(TagSheet, func.min(SleeveTag.serial), func.max(SleeveTag.serial),
               func.count(SleeveTag.serial), func.count(SleeveTag.card_id))
        .join(SleeveTag, SleeveTag.sheet_id == TagSheet.id)
        .group_by(TagSheet.id).order_by(TagSheet.nr.desc())
    ).all()
    return [{"id": s.id, "nr": s.nr, "first": lo, "last": hi, "count": n, "linked": linked,
             "start_slot": s.start_slot, "created_at": s.erstellt_am} for s, lo, hi, n, linked in rows]


def sheets_pdf(db: Session, sheets: list[TagSheet], base_url: str, lang: str = "EN") -> bytes:
    s = get_settings(db)
    bogen = tag_pdf.BOEGEN.get(sheets[0].bogen, tag_pdf.LWS144) if sheets else tag_pdf.LWS144
    blaetter = []
    for sheet in sheets:
        tags = sheet_tags(db, sheet.id)
        aufkleber = [tag_pdf.Aufkleber(t.slot, qr_content(base_url, t.serial), code_for(t.serial)) for t in tags]
        if tags:
            blaetter.append((sheet.nr, aufkleber))
    return tag_pdf.sheet_pdf(bogen, blaetter, (s.offset_x_mm, s.offset_y_mm), lang)


def calibration_pdf(db: Session, lang: str = "EN") -> bytes:
    s = get_settings(db)
    return tag_pdf.calibration_pdf(tag_pdf.BOEGEN[s.bogen], (s.offset_x_mm, s.offset_y_mm), lang)


# ── Verknüpfen ───────────────────────────────────────────────────────────────

class TagConflict(Exception):
    """Karte hat schon einen (anderen) Aufkleber bzw. Aufkleber klebt schon woanders."""

    def __init__(self, reason: str, other: Optional[int]):
        super().__init__(reason)
        self.reason, self.other = reason, other


def codes_for_cards(db: Session, card_ids) -> dict[int, str]:
    """Karte → Aufkleber-Code, für Kartenlisten (eine Abfrage)."""
    ids = [i for i in card_ids if i is not None]
    if not ids:
        return {}
    rows = db.execute(select(SleeveTag.card_id, SleeveTag.serial).where(SleeveTag.card_id.in_(ids))).all()
    return {card_id: code_for(serial) for card_id, serial in rows}


def tag_for_card(db: Session, card_id: int) -> Optional[SleeveTag]:
    return db.scalar(select(SleeveTag).where(SleeveTag.card_id == card_id))


def link(db: Session, tag: SleeveTag, card: PokemonCard, replace: bool = False) -> SleeveTag:
    """
    Aufkleber ↔ Karte (1:1). Ohne `replace` meldet ein Konflikt, statt still
    umzuhängen: die Karte hat schon einen Aufkleber (reason "card_tagged") oder
    der Aufkleber hängt an einer anderen Karte ("tag_linked"). Mit `replace`
    wird der alte Aufkleber der Karte wieder frei.
    """
    if tag.card_id == card.id:
        return tag
    old = tag_for_card(db, card.id)
    if not replace:
        if old is not None:
            raise TagConflict("card_tagged", old.serial)
        if tag.card_id is not None:
            raise TagConflict("tag_linked", tag.card_id)
    if old is not None:
        old.card_id, old.verknuepft_am = None, None
        db.flush()                         # card_id ist UNIQUE — erst freigeben
    tag.card_id, tag.verknuepft_am = card.id, datetime.utcnow()
    db.commit()
    return tag


def release_card(db: Session, card_id: int) -> None:
    """Aufkleber einer Karte freigeben, die nicht mehr besessen ist (ohne Commit).
    Gelöschte Karten gibt die Datenbank selbst frei (ON DELETE SET NULL)."""
    for tag in db.scalars(select(SleeveTag).where(SleeveTag.card_id == card_id)):
        tag.card_id, tag.verknuepft_am = None, None


def unlink(db: Session, tag: SleeveTag) -> None:
    tag.card_id, tag.verknuepft_am = None, None
    db.commit()


def candidates(db: Session, search: Optional[str], limit: int) -> list[PokemonCard]:
    """Eigene Karten ohne Aufkleber, die zuletzt erfassten zuerst."""
    tagged = select(SleeveTag.card_id).where(SleeveTag.card_id.isnot(None))
    q = select(PokemonCard).where(PokemonCard.besessen.is_(True), PokemonCard.id.not_in(tagged))
    if search and search.strip():
        from app.services import card_search
        conds, _ = card_search.conditions(db, search.strip())
        if conds:
            q = q.where(*conds)
    q = q.order_by(PokemonCard.hinzugefuegt_am.desc().nulls_last(), PokemonCard.id.desc()).limit(limit)
    return list(db.scalars(q))
