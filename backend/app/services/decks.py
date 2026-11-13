"""
Decks: Deckliste, Legalität, Auffüllen mit eigenen Karten, Import/Export im
Format von Pokémon TCG Live (PTCGL).

Eine Deckliste besteht aus DeckSlots (count × Katalog-Druck). Eigene Karten
stecken über pokemon_cards.deck_id in höchstens EINEM Deck; welche davon
welchen Platz der Liste füllt, rechnet coverage() jedes Mal neu aus:
  - zuerst genau derselbe Druck (TCGdex-ID),
  - dann eine gleichnamige Karte (Trainer/Energie: der Name zählt; Pokémon:
    ein anderer Druck kann andere Attacken haben → markiert, „bitte prüfen").

Deck-Regeln (Play! Pokémon): genau 60 Karten, höchstens 4 Karten gleichen
Namens (Basis-Energie ausgenommen), mindestens ein Basis-Pokémon, höchstens
eine ACE SPEC, höchstens ein Strahlendes Pokémon, je Format nur legale Drucke
(Standard: Regulation Mark samt Reprint-Regel, services/regulation.py).
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.card import PokemonCard
from app.models.deck import SECTIONS, Deck, DeckSlot
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import regulation

log = logging.getLogger(__name__)

DECK_SIZE = 60
MAX_COPIES = 4
MAX_SLOT_COUNT = 60

# PTCGL-Energiesymbole → Typ
ENERGY_SYMBOLS = {
    "G": "Grass", "R": "Fire", "W": "Water", "L": "Lightning", "P": "Psychic",
    "F": "Fighting", "D": "Darkness", "M": "Metal", "Y": "Fairy", "N": "Dragon", "C": "Colorless",
}
_SYMBOL_OF = {v.lower(): k for k, v in ENERGY_SYMBOLS.items()}
_BASIC_TYPES = {"grass", "fire", "water", "lightning", "psychic", "fighting", "darkness", "metal", "fairy"}

# PTCGL-Kürzel, die weder pokemon_sets noch die Brücke kennt (Promos, Energie)
PTCGL_SET_IDS = {
    "PR-SV": "svp", "SVP": "svp", "PR-SW": "swshp", "PR-SM": "smp", "PR-XY": "xyp",
    "PR-BLW": "bwp", "PR-HS": "hgssp", "PR-DPP": "dpp", "SVE": "sve", "MEP": "mep", "PR-ME": "mep",
}
# Serien, deren Karten in Expanded erlaubt sind (wenn TCGdex selbst nichts sagt)
_EXPANDED_SERIES = ("bw", "xy", "sm", "swsh", "sv", "me")


# ── Namen ───────────────────────────────────────────────────────────────────

def _plain(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'")
    return re.sub(r"\s+", " ", t).strip()


def expand_symbols(name: str) -> str:
    """„Basic {P} Energy" → „Basic Psychic Energy"."""
    return re.sub(r"\{([A-Za-z])\}", lambda m: ENERGY_SYMBOLS.get(m.group(1).upper(), m.group(0)), name or "")


def rule_name(name: str) -> str:
    """Name im Sinne der Regeln: Akzente/Groß-klein zählen nicht, der Zusatz in
    Klammern („Boss's Orders (Ghetsis)") auch nicht."""
    n = _plain(expand_symbols(name))
    return re.sub(r"\s*\([^)]*\)\s*$", "", n)


def basic_energy_type(name: str) -> Optional[str]:
    m = re.fullmatch(r"(?:basic )?(\w+) energy", rule_name(name))
    return m.group(1) if m and m.group(1) in _BASIC_TYPES else None


def ptcgl_name(name: str) -> str:
    """Name wie in PTCGL: Basis-Energie als „Basic {P} Energy"."""
    t = basic_energy_type(name)
    return f"Basic {{{_SYMBOL_OF[t]}}} Energy" if t else (name or "").strip()


def _name_candidates(name: str) -> set[str]:
    """Kleingeschriebene Schreibweisen, unter denen der Katalog den Namen führen kann."""
    base = (name or "").strip()
    out = {base, expand_symbols(base)}
    t = basic_energy_type(base)
    if t:
        out |= {f"{t.title()} Energy", f"Basic {t.title()} Energy"}
    more = set()
    for n in out:
        n2 = n.replace("’", "'")
        more |= {n2, n2.replace("Pokemon", "Pokémon"), n2.replace("Pokémon", "Pokemon")}
    return {n.lower() for n in out | more if n}


def _num(number: Optional[str]) -> Optional[str]:
    if not number:
        return None
    n = str(number).strip().upper()
    return n.lstrip("0") or "0"


# ── Katalog ─────────────────────────────────────────────────────────────────

def _kind(row: Optional[TcgdexCatalog]) -> Optional[str]:
    return regulation._kind(row.category) if row is not None else None


def _is_basic_energy(row: Optional[TcgdexCatalog], name: str) -> bool:
    if row is not None and row.regulation_checked and row.category:
        return regulation._is_basic_energy(row.category, row.energy_type, row.name_en or row.name or name)
    return basic_energy_type(name) is not None


def _rows(db: Session, ids: Iterable[Optional[str]]) -> dict[str, TcgdexCatalog]:
    return regulation._catalog_rows(db, [i for i in ids if i])


def set_ids_for_code(db: Session, code: Optional[str]) -> set[str]:
    if not code:
        return set()
    from app.services.set_sync import PTCGO_TO_SETID

    up = code.strip().upper()
    ids = {PTCGO_TO_SETID[up]} if up in PTCGO_TO_SETID else set()
    if up in PTCGL_SET_IDS:
        ids.add(PTCGL_SET_IDS[up])
    ids |= set(db.scalars(select(PokemonSet.set_id).where(func.upper(PokemonSet.code) == up,
                                                          PokemonSet.set_id.isnot(None))).all())
    ids |= set(db.scalars(select(TcgdexCatalog.set_id).where(func.upper(TcgdexCatalog.set_code) == up)
                          .distinct()).all())
    ids.add(code.strip().lower())
    return {i for i in ids if i}


def _best(rows: list[TcgdexCatalog]) -> TcgdexCatalog:
    """Mehrere passende Drucke: der mit dem jüngsten Zeichen, im neuesten Set — dort
    der normale Druck (niedrigste Nummer), nicht die Full-Art/Secret-Rare dahinter."""
    newest = max(rows, key=lambda r: (r.regulation_mark or "", r.set_id or ""))
    same_set = [r for r in rows if (r.regulation_mark or "", r.set_id or "") == (newest.regulation_mark or "", newest.set_id or "")]
    return min(same_set, key=lambda r: (r.local_id_num if r.local_id_num is not None else 1 << 30, r.card_id))


def match_card(db: Session, name: str, set_code: Optional[str], number: Optional[str]
               ) -> tuple[Optional[TcgdexCatalog], Optional[str]]:
    """Katalog-Druck zu einer Listenzeile. → (Zeile, "set" | "name" | None)."""
    key = rule_name(name)
    names = _name_candidates(name)
    num = _num(number)
    west = TcgdexCatalog.region == "west"
    num_col = func.upper(func.ltrim(TcgdexCatalog.local_id, "0"))
    if num:
        ids = set_ids_for_code(db, set_code)
        if ids:
            rows = db.scalars(select(TcgdexCatalog).where(west, TcgdexCatalog.set_id.in_(ids), num_col == num)).all()
            same = [r for r in rows if rule_name(r.name_en or r.name or "") == key]
            if same or len(rows) == 1:
                return _best(same or rows), "set"
        rows = db.scalars(select(TcgdexCatalog).where(
            west, num_col == num, func.lower(TcgdexCatalog.name_en).in_(names))).all()
        if rows:
            return _best(rows), "set"
    rows = db.scalars(select(TcgdexCatalog).where(west, func.lower(TcgdexCatalog.name_en).in_(names))).all()
    if rows:
        return _best(rows), "name"
    return None, None


def section_of(row: Optional[TcgdexCatalog], name: str, given: Optional[str] = None) -> str:
    if given in SECTIONS:
        return given
    kind = _kind(row)
    if kind in SECTIONS:
        return kind
    return "energy" if rule_name(name).endswith(" energy") else "pokemon"


# ── PTCGL-Text ──────────────────────────────────────────────────────────────

_HEADER = re.compile(r"^(pok[eé]mon|trainer|trainers|energy)\s*(?:[:\-]\s*\d*)?\s*$", re.I)
_SKIP = re.compile(r"^(total\s+cards|\*\*\*|##)", re.I)
_LINE = re.compile(r"^\*?\s*(\d{1,2})x?\s+(.+?)\s+([A-Za-z0-9][A-Za-z0-9-]{0,9})\s+([A-Za-z]{0,6}\d{1,4}[A-Za-z]?)\s*$")
_LINE_NAME = re.compile(r"^\*?\s*(\d{1,2})x?\s+(.+?)\s*$")


@dataclass
class ListLine:
    count: int
    name: str
    set_code: Optional[str] = None
    number: Optional[str] = None
    section: Optional[str] = None


@dataclass
class Parsed:
    lines: list[ListLine] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def parse_ptcgl(text: str) -> Parsed:
    out = Parsed()
    section: Optional[str] = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or _SKIP.match(line):
            continue
        h = _HEADER.match(line)
        if h:
            word = h.group(1).lower()
            section = "pokemon" if word.startswith("pok") else ("trainer" if word.startswith("trainer") else "energy")
            continue
        m = _LINE.match(line)
        if m:
            out.lines.append(ListLine(int(m.group(1)), m.group(2).strip(), m.group(3), _num(m.group(4)), section))
            continue
        m = _LINE_NAME.match(line)
        if m:
            out.lines.append(ListLine(int(m.group(1)), m.group(2).strip(), None, None, section))
            continue
        out.unreadable.append(line[:120])
    return out


def export_ptcgl(db: Session, deck: Deck) -> str:
    slots = _slots(db, deck.id)
    rows = _rows(db, (s.tcgdex_card_id for s in slots))
    labels = {"pokemon": "Pokémon", "trainer": "Trainer", "energy": "Energy"}
    parts = []
    for sec in SECTIONS:
        lines = []
        for s in (x for x in slots if x.section == sec):
            row = rows.get(s.tcgdex_card_id) if s.tcgdex_card_id else None
            code = s.set_code or (row.set_code if row is not None else None) or (row.set_id.upper() if row is not None and row.set_id else None)
            number = s.number or (_num(row.local_id) if row is not None else None)
            tail = f" {code} {number}" if code and number else ""
            lines.append(f"{s.count} {ptcgl_name(s.name)}{tail}")
        if lines:
            parts.append(f"{labels[sec]}: {len(lines)}\n" + "\n".join(lines))
    total = sum(s.count for s in slots)
    parts.append(f"Total Cards: {total}")
    return "\n\n".join(parts) + "\n"


# ── Liste bearbeiten ────────────────────────────────────────────────────────

def _slots(db: Session, deck_id: int) -> list[DeckSlot]:
    order = {s: i for i, s in enumerate(SECTIONS)}
    rows = db.scalars(select(DeckSlot).where(DeckSlot.deck_id == deck_id)).all()
    return sorted(rows, key=lambda s: (order.get(s.section, 9), s.position if s.position is not None else 1 << 30, s.id))


def _next_position(db: Session, deck_id: int) -> int:
    return (db.scalar(select(func.max(DeckSlot.position)).where(DeckSlot.deck_id == deck_id)) or 0) + 1


def import_lines(db: Session, deck: Deck, parsed: Parsed, replace: bool = False) -> dict:
    """Zeilen in die Liste übernehmen (gleicher Druck → Anzahl addieren). Committet nicht."""
    if replace:
        for s in _slots(db, deck.id):
            db.delete(s)
        db.flush()
    existing = {(s.tcgdex_card_id or "").upper() or f"name:{rule_name(s.name)}": s for s in _slots(db, deck.id)}
    pos = _next_position(db, deck.id)
    matched = unmatched = 0
    missing: list[str] = []
    for ln in parsed.lines:
        if ln.count <= 0:
            continue
        row, how = match_card(db, ln.name, ln.set_code, ln.number)
        key = row.card_id.upper() if row is not None else f"name:{rule_name(ln.name)}"
        slot = existing.get(key)
        if slot is not None:
            slot.count = min(MAX_SLOT_COUNT, slot.count + ln.count)
        else:
            slot = DeckSlot(deck_id=deck.id, tcgdex_card_id=row.card_id if row is not None else None,
                            name=ln.name, set_code=ln.set_code.upper() if ln.set_code else None,
                            number=ln.number, count=min(MAX_SLOT_COUNT, ln.count),
                            section=section_of(row, ln.name, ln.section), match=how, position=pos)
            pos += 1
            db.add(slot)
            existing[key] = slot
        if row is None:
            unmatched += 1
            missing.append(f"{ln.count} {ln.name}" + (f" {ln.set_code} {ln.number}" if ln.set_code else ""))
        else:
            matched += 1
    db.flush()
    return {"lines": len(parsed.lines), "matched": matched, "unmatched": unmatched,
            "not_found": missing, "unreadable": parsed.unreadable}


def add_slot(db: Session, deck: Deck, row: TcgdexCatalog, count: int = 1, how: str = "set") -> DeckSlot:
    """Druck aus dem Katalog in die Liste (vorhanden → Anzahl erhöhen). Committet nicht."""
    for s in _slots(db, deck.id):
        if s.tcgdex_card_id and s.tcgdex_card_id.upper() == row.card_id.upper():
            s.count = min(MAX_SLOT_COUNT, s.count + count)
            return s
    name = row.name_en or row.name or row.card_id
    slot = DeckSlot(deck_id=deck.id, tcgdex_card_id=row.card_id, name=name, set_code=(row.set_code or None),
                    number=_num(row.local_id), count=min(MAX_SLOT_COUNT, count),
                    section=section_of(row, name), match=how, position=_next_position(db, deck.id))
    db.add(slot)
    db.flush()
    return slot


def prints_for(db: Session, name: str) -> list[TcgdexCatalog]:
    """Alle westlichen Drucke derselben Karte (Name nach den Regeln: Akzente, ’ und
    ein Zusatz wie „(Ghetsis)" zählen nicht), jüngstes Zeichen zuerst."""
    want = rule_name(name)
    first = want.split()[0].split("'")[0] if want else ""
    if not first:
        return []
    col = func.lower(func.coalesce(TcgdexCatalog.name_en, TcgdexCatalog.name))
    rows = db.scalars(select(TcgdexCatalog).where(TcgdexCatalog.region == "west", col.like(f"{first}%"))).all()
    rows = [r for r in rows if rule_name(r.name_en or r.name or "") == want]
    return sorted(rows, key=lambda r: (r.regulation_mark or "", r.set_id or "", r.local_id_num or 0), reverse=True)


def set_print(db: Session, deck: Deck, slot: DeckSlot, row: TcgdexCatalog, link_cards: bool = True) -> int:
    """
    Zeile auf einen anderen Druck derselben Karte stellen (von Hand gewählt). Steht
    der Druck schon in einer anderen Zeile, wandert die Anzahl dorthin. link_cards:
    eigene Karten dieser Zeile ohne Katalog-Druck bekommen ihn auch (Bild, Preis,
    Legalität). Committet nicht. → Anzahl so verknüpfter Karten.
    """
    linked = 0
    if link_cards:
        for card, _how in coverage(db, deck).by_slot.get(slot.id, []):
            if card.tcgdex_card_id:
                continue
            card.tcgdex_card_id, card.set_id = row.card_id, row.set_id
            card.englischer_name = card.englischer_name or row.name_en
            card.karten_nr = card.karten_nr or row.local_id
            if row.dex_id is not None and card.dex_id is None:
                card.dex_id = row.dex_id
            regulation.apply_to_card(db, card)
            linked += 1
    other = next((s for s in _slots(db, deck.id) if s.id != slot.id and s.tcgdex_card_id
                  and s.tcgdex_card_id.upper() == row.card_id.upper()), None)
    if other is not None:
        other.count = min(MAX_SLOT_COUNT, other.count + slot.count)
        db.delete(slot)
    else:
        slot.tcgdex_card_id, slot.match = row.card_id, "set"
        slot.name = row.name_en or row.name or slot.name
        slot.set_code, slot.number = row.set_code or None, _num(row.local_id)
        slot.section = section_of(row, slot.name)
    db.flush()
    return linked


def _home_for(db: Session, deck: Deck, name: str, number: Optional[str],
              skip: Optional[DeckSlot] = None) -> tuple[Optional[DeckSlot], Optional[TcgdexCatalog], Optional[str]]:
    """
    Wohin gehört eine Karte bzw. Zeile ohne Katalog-Druck (z. B. ein Scan, der
    nur den Namen kannte)? → (vorhandene Zeile | None, Katalog-Druck | None, wie gefunden).
    1. Name + Nummer ergeben einen Druck → dessen Zeile (oder er selbst, neu)
    2. sonst die Zeile mit demselben Namen im Deck (meist genau dieser Druck)
    3. sonst der jüngste Druck dieses Namens („nach Name")
    """
    num = _num(number)
    row, how = match_card(db, name, None, num) if name else (None, None)
    slots = [s for s in _slots(db, deck.id) if s is not skip]
    if row is not None and how == "set" and num:
        same_print = next((s for s in slots if s.tcgdex_card_id and s.tcgdex_card_id.upper() == row.card_id.upper()), None)
        return same_print, row, how
    key = rule_name(name)
    same_name = next((s for s in slots if s.tcgdex_card_id and rule_name(s.name) == key), None)
    if same_name is not None:
        return same_name, None, None
    return None, row, how


def relink(db: Session, deck: Deck) -> int:
    """
    Zeilen ohne Katalog-Druck nachträglich zuordnen (ältere Scans ins Deck legten
    für eine Karte ohne Druck eine eigene Namens-Zeile an — immer unter Pokémon,
    eine Zeile je Karte): in die Zeile desselben Drucks bzw. Namens zählen, sonst
    den gefundenen Druck eintragen. Committet nicht. → Anzahl geänderter Zeilen.
    """
    changed = 0
    # nur über den Namen gefundene Zeile, deren eigene Karten alle denselben Druck
    # haben (z. B. nach dem Verknüpfen der Karten): dieser Druck, samt seinem Bild
    cov = coverage(db, deck)
    for s in [s for s in _slots(db, deck.id) if s.match == "name" and s.tcgdex_card_id]:
        ids = {c.tcgdex_card_id.upper() for c, _how in cov.by_slot.get(s.id, []) if c.tcgdex_card_id}
        if len(ids) != 1 or s.tcgdex_card_id.upper() in ids:
            continue
        row = _rows(db, list(ids)).get(next(iter(ids)))
        if row is not None and rule_name(row.name_en or row.name or "") == rule_name(s.name):
            set_print(db, deck, s, row, link_cards=False)
            changed += 1
    for s in [s for s in _slots(db, deck.id) if not s.tcgdex_card_id]:
        home, row, how = _home_for(db, deck, s.name, s.number, skip=s)
        if home is not None:
            home.count = min(MAX_SLOT_COUNT, home.count + s.count)
            db.delete(s)
        elif row is not None:
            s.tcgdex_card_id, s.match = row.card_id, how
            s.section = section_of(row, s.name)
            if how == "set":
                s.set_code = row.set_code or s.set_code
        else:
            continue
        db.flush()
        changed += 1
    return changed


# ── Eigene Karten ↔ Liste ───────────────────────────────────────────────────

def _card_name(card: PokemonCard, rows: dict[str, TcgdexCatalog]) -> str:
    row = rows.get(card.tcgdex_card_id) if card.tcgdex_card_id else None
    return card.englischer_name or (row.name_en if row is not None else None) or card.kartenname or ""


def _card_set(card: PokemonCard) -> Optional[str]:
    if card.set_id:
        return card.set_id.lower()
    tid = card.tcgdex_card_id or ""
    return tid.rsplit("-", 1)[0].lower() if "-" in tid else None


def _fits(slot: DeckSlot, slot_row: Optional[TcgdexCatalog], card: PokemonCard, card_name: str,
          loose_pokemon: bool) -> Optional[str]:
    """Passt die Karte auf den Platz? → "exact" | "same_name" | "other_print" | None."""
    if slot.tcgdex_card_id and card.tcgdex_card_id and slot.tcgdex_card_id.upper() == card.tcgdex_card_id.upper():
        return "exact"
    if rule_name(card_name) != rule_name(slot.name) or not card_name:
        return None
    if slot.section != "pokemon":
        return "same_name"
    # Pokémon: anderer Druck kann andere Attacken haben. Gleiches Set (andere
    # Seltenheit desselben Drucks) gilt als sicher genug fürs Auffüllen.
    slot_set = slot_row.set_id.lower() if slot_row is not None and slot_row.set_id else None
    if slot_set and _card_set(card) == slot_set:
        return "same_name"
    return "other_print" if loose_pokemon else None


@dataclass
class Coverage:
    by_slot: dict[int, list[tuple[PokemonCard, str]]]
    extra: list[PokemonCard]


def coverage(db: Session, deck: Deck, slots: Optional[list[DeckSlot]] = None) -> Coverage:
    slots = slots if slots is not None else _slots(db, deck.id)
    cards = db.scalars(select(PokemonCard).where(PokemonCard.deck_id == deck.id).order_by(PokemonCard.id)).all()
    rows = _rows(db, [s.tcgdex_card_id for s in slots] + [c.tcgdex_card_id for c in cards])
    names = {c.id: _card_name(c, rows) for c in cards}
    by_slot: dict[int, list[tuple[PokemonCard, str]]] = {s.id: [] for s in slots}
    left = list(cards)
    for loose in (False, True):          # erst exakt + sicher, dann andere Drucke
        for want in (("exact",), ("exact", "same_name", "other_print")):
            for s in slots:
                row = rows.get(s.tcgdex_card_id) if s.tcgdex_card_id else None
                for c in list(left):
                    if len(by_slot[s.id]) >= s.count:
                        break
                    how = _fits(s, row, c, names[c.id], loose)
                    if how in want:
                        by_slot[s.id].append((c, how))
                        left.remove(c)
    return Coverage(by_slot, left)


class FreeCards:
    """Eigene Karten in keinem Deck, einmal geladen und nach Druck/Name sortiert."""

    def __init__(self, db: Session):
        cards = db.scalars(select(PokemonCard).where(PokemonCard.besessen == True,  # noqa: E712
                                                     PokemonCard.deck_id.is_(None))).all()
        rows = _rows(db, [c.tcgdex_card_id for c in cards])
        self.names: dict[int, str] = {}
        self.by_name: dict[str, list[PokemonCard]] = defaultdict(list)
        self.by_id: dict[str, list[PokemonCard]] = defaultdict(list)
        for c in cards:
            n = _card_name(c, rows)
            self.names[c.id] = n
            if n:
                self.by_name[rule_name(n)].append(c)
            if c.tcgdex_card_id:
                self.by_id[c.tcgdex_card_id.upper()].append(c)
        self.taken: set[int] = set()

    def for_slot(self, db: Session, slot: DeckSlot, loose_pokemon: bool = True) -> list[tuple[PokemonCard, str]]:
        """Passende Karten, genauer Druck zuerst, dann die günstigsten."""
        row = _rows(db, [slot.tcgdex_card_id]).get(slot.tcgdex_card_id) if slot.tcgdex_card_id else None
        pool = {c.id: c for c in self.by_name.get(rule_name(slot.name), [])}
        if slot.tcgdex_card_id:
            pool.update({c.id: c for c in self.by_id.get(slot.tcgdex_card_id.upper(), [])})
        out = []
        for c in pool.values():
            if c.id in self.taken:
                continue
            how = _fits(slot, row, c, self.names[c.id], loose_pokemon)
            if how:
                out.append((c, how))
        rank = {"exact": 0, "same_name": 1, "other_print": 2}
        out.sort(key=lambda x: (rank[x[1]], x[0].wert_eur is None, float(x[0].wert_eur or 0), x[0].id))
        return out


def fill(db: Session, deck: Deck) -> int:
    """Fehlende Plätze mit eigenen Karten aus keinem Deck füllen (genauer Druck
    zuerst, dann gleichnamige — Pokémon nur aus demselben Set —, günstigste
    zuerst). Committet nicht. → Anzahl zugeordneter Karten."""
    slots = _slots(db, deck.id)
    cov = coverage(db, deck, slots)
    free = FreeCards(db)
    added = 0
    for s in slots:
        need = s.count - len(cov.by_slot.get(s.id, []))
        if need <= 0:
            continue
        for c, _how in free.for_slot(db, s, loose_pokemon=False)[:need]:
            c.deck_id = deck.id
            free.taken.add(c.id)
            added += 1
    db.flush()
    return added


def assign(db: Session, deck: Deck, card: PokemonCard, extend_list: bool = True) -> None:
    """Karte ins Deck legen. Passt sie auf keinen freien Platz, wächst die Liste
    um diesen Druck (Scan ins Deck, Zuordnen auf der Kartenseite). Committet nicht."""
    card.deck_id = deck.id
    db.flush()
    if not extend_list:
        return
    cov = coverage(db, deck)
    if any(c.id == card.id for c in cov.extra):
        row = _rows(db, [card.tcgdex_card_id]).get(card.tcgdex_card_id) if card.tcgdex_card_id else None
        if row is not None:
            add_slot(db, deck, row, 1)
            return
        # Karte ohne Katalog-Druck (Scan kannte nur Name/Nummer): Druck bzw.
        # gleichnamige Zeile suchen statt einer eigenen Namens-Zeile unter Pokémon
        name = card.englischer_name or card.kartenname or ""
        number = _num(card.karten_nr.split("/")[0]) if card.karten_nr else None
        home, found, how = _home_for(db, deck, name, number)
        if home is not None:
            home.count = min(MAX_SLOT_COUNT, home.count + 1)
            db.flush()
        elif found is not None:
            add_slot(db, deck, found, 1, how=how or "name")
        else:
            db.add(DeckSlot(deck_id=deck.id, tcgdex_card_id=None, name=name, set_code=None, number=number,
                            count=1, section=section_of(None, name), match=None,
                            position=_next_position(db, deck.id)))
            db.flush()


# ── Legalität ───────────────────────────────────────────────────────────────

def _expanded_ok(row: Optional[TcgdexCatalog]) -> Optional[bool]:
    if row is None:
        return None
    if row.legal_expanded:
        return True
    sid = (row.set_id or "").lower()
    if not sid:
        return None
    return any(re.match(rf"^{s}(\d|p$|$)", sid) for s in _EXPANDED_SERIES)


def slot_status(fmt: str, slot: DeckSlot, row: Optional[TcgdexCatalog], min_mark: str,
                reprints: dict) -> tuple[str, Optional[str]]:
    """→ (Status, maßgebliches Zeichen) eines Platzes im Format:
    ok | reprint_text | basic | not_legal | unknown | unmatched.
    reprint_text: Trainer/Spezial-Energie, deren gleichnamiger Nachdruck legal ist,
    nur mit neuem Wortlaut (z. B. Boss's Orders: „… with their Active Pokémon" →
    „… to the Active Spot") — nach der Reprint-Regel spielbar, im Deck darum kein
    Fehler, aber gekennzeichnet (der Text ist nicht verglichen, nur der Name)."""
    if row is None:
        return ("basic" if basic_energy_type(slot.name) else "unmatched"), None
    if _is_basic_energy(row, slot.name):
        return "basic", None
    if fmt == "unlimited":
        return "ok", None
    if fmt == "expanded":
        ok = _expanded_ok(row)
        return ("unknown" if ok is None else ("ok" if ok else "not_legal")), None
    _mark, legal, _basic, name_mark = regulation._compute(row, None, reprints)
    if legal is None:
        return "unknown", None
    if legal != regulation.NO_MARK and legal >= min_mark:
        return "ok", legal
    if name_mark and name_mark >= min_mark:
        return "reprint_text", name_mark
    return "not_legal", legal


def legality(db: Session, deck: Deck, slots: Optional[list[DeckSlot]] = None) -> dict:
    slots = slots if slots is not None else _slots(db, deck.id)
    rows = _rows(db, (s.tcgdex_card_id for s in slots))
    keys = {k for r in rows.values() if r.regulation_checked and (k := regulation._reprint_key(r))}
    reprints = regulation._reprint_marks(db, keys)
    min_mark = regulation.standard_min_mark(db)
    fmt = deck.format or "standard"

    issues: list[dict] = []
    per_slot: dict[int, dict] = {}
    sections = defaultdict(int)
    total = 0
    by_name: dict[str, list[DeckSlot]] = defaultdict(list)
    ace = radiant = 0
    ace_slots: list[int] = []
    radiant_slots: list[int] = []
    basics = unknown_stage = 0
    need_data = False

    for s in slots:
        row = rows.get(s.tcgdex_card_id) if s.tcgdex_card_id else None
        total += s.count
        sections[s.section] += s.count
        status, mark = slot_status(fmt, s, row, min_mark, reprints)
        stage = row.stage if row is not None else None
        rarity = row.rarity if row is not None else None
        is_ace = bool(rarity and "ace spec" in rarity.lower())
        per_slot[s.id] = {"status": status, "legal_mark": mark, "stage": stage, "trainer_type": row.trainer_type if row is not None else None,
                          "rarity": rarity, "ace_spec": is_ace}
        if not _is_basic_energy(row, s.name):
            by_name[rule_name(s.name)].append(s)
        if is_ace:
            ace += s.count
            ace_slots.append(s.id)
        if rule_name(s.name).startswith("radiant "):
            radiant += s.count
            radiant_slots.append(s.id)
        if s.section == "pokemon":
            if stage:
                basics += s.count if stage.strip().lower() == "basic" else 0
            else:
                unknown_stage += s.count
        if row is not None and (not row.regulation_checked or (_kind(row) == "pokemon" and not row.stage)
                                or (_kind(row) == "trainer" and not row.trainer_type)):
            need_data = True

    if total != DECK_SIZE:
        issues.append({"level": "error", "code": "deck_size", "count": total, "slots": []})
    for ss in by_name.values():
        n = sum(x.count for x in ss)
        if n > MAX_COPIES:
            issues.append({"level": "error", "code": "too_many_copies", "name": ss[0].name, "count": n,
                           "slots": [x.id for x in ss]})
    if ace > 1:
        issues.append({"level": "error", "code": "ace_spec", "count": ace, "slots": ace_slots})
    if radiant > 1:
        issues.append({"level": "error", "code": "radiant", "count": radiant, "slots": radiant_slots})
    if sections["pokemon"] == 0 and total:
        issues.append({"level": "error", "code": "no_basic", "count": 0, "slots": []})
    elif sections["pokemon"] and basics == 0:
        issues.append({"level": "warning" if unknown_stage else "error",
                       "code": "basic_unknown" if unknown_stage else "no_basic", "count": 0, "slots": []})
    bad = [s for s in slots if per_slot[s.id]["status"] == "not_legal"]
    if bad:
        issues.append({"level": "error", "code": "not_in_format", "count": sum(s.count for s in bad),
                       "slots": [s.id for s in bad]})
    unknown = [s for s in slots if per_slot[s.id]["status"] == "unknown"]
    if unknown:
        issues.append({"level": "warning", "code": "legality_unknown", "count": sum(s.count for s in unknown),
                       "slots": [s.id for s in unknown]})
    unmatched = [s for s in slots if per_slot[s.id]["status"] == "unmatched"]
    if unmatched:
        issues.append({"level": "warning", "code": "unmatched", "count": sum(s.count for s in unmatched),
                       "slots": [s.id for s in unmatched]})

    errors = any(i["level"] == "error" for i in issues)
    status = "illegal" if errors else ("check" if issues else "legal")
    return {"status": status, "format": fmt, "min_mark": min_mark, "total": total,
            "sections": {sec: sections[sec] for sec in SECTIONS}, "issues": issues,
            "slots": per_slot, "need_data": need_data}


async def fetch_missing_data(db: Session, deck: Deck, limit: int = 80) -> int:
    """Stufe/Trainer-Art/Zeichen der Listen-Drucke bei TCGdex nachholen, wo sie
    fehlen (ältere Katalogzeilen). → Anzahl aktualisierter Zeilen."""
    from app.services import tcgdex
    from app.services.catalog import _apply_full

    slots = _slots(db, deck.id)
    rows = _rows(db, (s.tcgdex_card_id for s in slots))
    todo = [r.card_id for r in rows.values()
            if not r.regulation_checked or (_kind(r) == "pokemon" and not r.stage)
            or (_kind(r) == "trainer" and not r.trainer_type)][:limit]
    if not todo:
        return 0
    sem = asyncio.Semaphore(5)
    got: dict[str, object] = {}

    async def one(cid: str):
        async with sem:
            try:
                tc = await tcgdex.get_card(cid, "en")
            except Exception as exc:  # noqa: BLE001 — eine Karte darf den Rest nicht kippen
                log.info("Deck-Daten: %s nicht abrufbar: %s", cid, exc)
                tc = None
            if tc is not None:
                got[cid] = tc

    await asyncio.gather(*(one(c) for c in todo))
    for cid, tc in got.items():
        row = db.get(TcgdexCatalog, cid)
        if row is None:
            continue
        if row.enriched:
            regulation.apply_to_catalog(row, tc)
        else:
            _apply_full(row, tc)
    db.commit()
    return len(got)
