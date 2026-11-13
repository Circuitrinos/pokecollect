"""
Neu abgleichen („Re-match"): den Druck einer Karte noch einmal bestimmen und ihre
Daten aus dem Katalog vervollständigen — Nummer mit Nenner („117/132"),
Illustrator, Seltenheit, Set, Namen, Pokédex-Nr., Bild, Legalität.

Der Druck kommt, wenn es ein eigenes Foto gibt, aus dem Bild-Abgleich (matcher,
Modus „card"); sonst aus der Verknüpfung der Karte, sonst aus Set + Nummer, zuletzt
aus dem Namen. Der Bild-Abgleich überstimmt eine Verknüpfung nur, wenn er sicher
ist; ein Druck gleichen Namens unter den Kandidaten bestätigt die Verknüpfung.

Dieselben Felder nutzen „Richtige Karte finden", die Druck-Wahl der Deck-Seite
(fill_from_row) und das Admin-Werkzeug „Karten mit Lücken" (gaps).
"""
from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass, field
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.card import PokemonCard
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import regulation
from app.services.catalog import catalog_fields, enrich
from app.services.catalog_lookup import catalog_row_for

log = logging.getLogger(__name__)
_TIMEOUT = httpx.Timeout(60.0, connect=3.0)

# Felder aus dem Katalog, in Anzeige-Reihenfolge
FIELDS = ("tcgdex_card_id", "kartenname", "englischer_name", "set_edition", "karten_nr", "seltenheit",
          "illustrator", "pokedex_nr", "set_id", "dex_id")
# nur ergänzen, nie überschreiben (eigene Pflege, oder der Katalog weiß es nicht besser)
_ONLY_FILL = {"pokedex_nr", "dex_id"}
# Lücken fürs Admin-Werkzeug: Feld → Lücke
_GAP_OF = {"karten_nr": "number", "illustrator": "illustrator", "seltenheit": "rarity",
           "set_edition": "set", "englischer_name": "english_name", "bild": "image"}
GAPS = ("print", "number", "illustrator", "rarity", "set", "english_name", "foiling", "image", "differs")
FOILS = ("Normal", "Holo", "Reverse Holo")


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _parts(n: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """„007/132" → ("7", "132"); „TG05" → ("TG5"…): Nummer und Nenner ohne führende Nullen."""
    if not n or not str(n).strip():
        return None, None
    left, _, right = str(n).partition("/")
    norm = lambda x: (x.strip().upper().lstrip("0") or "0") if x.strip() else None  # noqa: E731
    return norm(left), norm(right)


def _num(n: Optional[str]) -> Optional[str]:
    return _parts(n)[0]


def _number_change(old: Optional[str], new: Optional[str]) -> Optional[str]:
    """→ "fill" (fehlt / ohne Nenner), "change" (andere Nummer oder anderer Nenner) oder None
    (passt; „7/146" gegen „007/146" ist nur die Schreibweise)."""
    if _empty(new):
        return None
    if _empty(old):
        return "fill"
    (ol, orr), (nl, nr) = _parts(old), _parts(new)
    if ol != nl:
        return "change"
    if nr and not orr:
        return "fill"
    return "change" if nr and orr and nr != orr else None


def _same(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() == b.strip().casefold()
    return a == b


@dataclass
class Change:
    field: str
    old: object
    new: object
    kind: str          # "fill" (fehlte) | "change" (stand anders da)


def _has_image(card: PokemonCard) -> bool:
    return bool(card.bild_karte_url or card.bild_karte_pfad)


def changes(card: PokemonCard, fields: dict, row: TcgdexCatalog) -> list[Change]:
    """Was der Katalog-Druck an der Karte ergänzen oder richtigstellen würde."""
    out: list[Change] = []
    relink = (card.tcgdex_card_id or "").upper() != row.card_id.upper()
    for f in FIELDS:
        new, old = fields.get(f), getattr(card, f)
        if _empty(new):
            continue
        if f == "kartenname" and not _empty(old) and (card.sprache or "DE").upper() not in ("DE", "EN"):
            continue                    # der Katalog kennt nur deutsche und englische Namen
        if f == "karten_nr":
            kind = _number_change(old, new)
        elif _empty(old):
            kind = "fill"
        elif f in _ONLY_FILL or _same(old, new):
            kind = None
        else:
            kind = "change"
        if kind:
            out.append(Change(f, old, new, kind))
    if not relink and not _has_image(card) and fields.get("bild_karte_url"):
        out.append(Change("bild", None, fields["bild_karte_url"], "fill"))
    return out


# ── Foliierung ─────────────────────────────────────────────────────────────

def foil_options(row: Optional[TcgdexCatalog]) -> list[str]:
    if row is None:
        return list(FOILS)
    opts = [f for f, on in (("Normal", row.variants_normal), ("Holo", row.variants_holo),
                            ("Reverse Holo", row.variants_reverse)) if on]
    return opts or list(FOILS)


def foil_suggestion(current: Optional[str], options: list[str], hint: Optional[bool]) -> tuple[Optional[str], Optional[str]]:
    """Vorschlag für eine fehlende Foliierung → (Wert, Grund "only" | "photo") oder (None, None).
    Der Foto-Hinweis unterscheidet nur Reverse Holo / nicht — Holo gegen Normal nicht."""
    if not _empty(current):
        return None, None
    if len(options) == 1:
        return options[0], "only"
    if hint is True and "Reverse Holo" in options:
        return "Reverse Holo", "photo"
    if hint is False and set(options) == {"Normal", "Reverse Holo"}:
        return "Normal", "photo"
    return None, None


# ── Druck finden ───────────────────────────────────────────────────────────

@dataclass
class Found:
    row: Optional[TcgdexCatalog]
    how: Optional[str]                  # "photo" | "link" | "number" | "name"
    sure: bool
    photo: str                          # "used" | "no_photo" | "off" | "down" | "nothing"
    candidates: list[TcgdexCatalog] = field(default_factory=list)   # vom Bild-Abgleich, beste zuerst
    crop: Optional[bytes] = None


def _photo_path(card: PokemonCard):
    from app.services.card_images import safe_media_path

    p = safe_media_path(card.bild_scan_pfad or card.bild_karte_pfad)
    return p if p is not None and p.is_file() else None


async def _photo_match(path, client: httpx.AsyncClient) -> Optional[dict]:
    r = await client.post(f"{settings.matcher_url.rstrip('/')}/match",
                          files={"file": (path.name, path.read_bytes(),
                                          mimetypes.guess_type(path.name)[0] or "image/jpeg")},
                          data={"mode": "card", "top_k": "5"})
    if r.status_code != 200:
        return None
    return (r.json().get("cards") or [{}])[0]


def _by_card_data(db: Session, card: PokemonCard) -> tuple[Optional[TcgdexCatalog], Optional[str]]:
    """Ohne Verknüpfung und Foto: über Set + Nummer, sonst über den Namen."""
    from app.services import decks

    name = card.englischer_name or card.kartenname or ""
    code = None
    if card.set_edition and "(" in card.set_edition and card.set_edition.rstrip().endswith(")"):
        code = card.set_edition.rstrip()[:-1].rsplit("(", 1)[1].strip()
    number = card.karten_nr.split("/")[0] if card.karten_nr else None
    if card.set_id and number:
        num = decks._num(number)
        rows = [r for r in db.scalars(select(TcgdexCatalog).where(TcgdexCatalog.set_id == card.set_id)).all()
                if decks._num(r.local_id) == num]
        if len(rows) == 1:
            return rows[0], "number"
    if not name:
        return None, None
    row, how = decks.match_card(db, name, code or card.set_edition, number)
    return row, ("number" if how == "set" else how)


async def find(db: Session, card: PokemonCard, use_photo: bool = True) -> Found:
    from app.services import decks

    linked = catalog_row_for(db, card.tcgdex_card_id)
    path = _photo_path(card) if use_photo else None
    photo = "no_photo" if path is None else ("off" if not settings.matcher_url else "used")
    cands: list[TcgdexCatalog] = []
    auto = False
    crop = None
    if photo == "used":
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                found = await _photo_match(path, client)
        except Exception as exc:  # noqa: BLE001 — ohne Bild-Abgleich geht es über die Daten weiter
            log.info("Re-match %s: Bild-Abgleich nicht erreichbar (%s)", card.id, exc)
            found = None
        if found is None:
            photo = "down"
        else:
            crop = path.read_bytes()
            auto = bool(found.get("auto"))
            for c in found.get("candidates") or []:
                row = catalog_row_for(db, c.get("card_id"))
                if row is not None and all(r.card_id != row.card_id for r in cands):
                    cands.append(row)
            if not cands:
                photo = "nothing"
    if cands:
        top = cands[0]
        if auto:
            return Found(top, "photo", True, photo, cands, crop)
        key = lambda r: decks.rule_name(r.name_en or r.name or "")  # noqa: E731
        if linked is not None and (any(r.card_id == linked.card_id for r in cands) or key(top) == key(linked)):
            # derselbe Druck unter den Kandidaten, oder nur ein anderer Druck derselben Karte
            # (gleiches Bild in mehreren Sets) — die Verknüpfung bleibt
            return Found(linked, "link", True, photo, cands, crop)
        from app.services.catalog import card_number

        name = card.englischer_name or card.kartenname or ""
        sets: dict = {}
        # die aufgedruckte Nummer (mit Nenner: auch das Set) wählt unter den Kandidaten
        by_number = [r for r in cands if not _empty(card.karten_nr)
                     and _number_change(card.karten_nr, card_number(db, r, sets)) in (None, "fill")
                     and (not name or key(r) == decks.rule_name(name))]
        if len(by_number) == 1:
            return Found(by_number[0], "photo", True, photo, cands, crop)
        return Found(top, "photo", False, photo, cands, crop)
    if linked is not None:
        return Found(linked, "link", True, photo, [], crop)
    row, how = _by_card_data(db, card)
    if row is not None:
        name = card.englischer_name or card.kartenname or ""
        sure = how == "number" and decks.rule_name(row.name_en or row.name or "") == decks.rule_name(name)
        return Found(row, how, sure, photo, [], crop)
    return Found(None, None, False, photo, [], crop)


async def photo_foil_hint(card_id: str, crop: Optional[bytes]) -> Optional[bool]:
    """Reverse holo laut Foto (matcher /foil mit dem gelernten Modell) → True/False, None = weiß nicht."""
    if crop is None or not settings.matcher_url:
        return None
    from app.services import foil_check

    try:
        r = await foil_check.check(card_id, crop)
    except Exception as exc:  # noqa: BLE001
        log.info("Re-match: Folien-Prüfung %s nicht möglich (%s)", card_id, exc)
        return None
    if not r.get("model") or r.get("reference") is False or not r.get("aligned"):
        return None
    return bool(r.get("hint"))


# ── Übernehmen ─────────────────────────────────────────────────────────────

async def apply(db: Session, card: PokemonCard, row: TcgdexCatalog, only: Optional[set[str]] = None,
                folierung: Optional[str] = None, image_url: Optional[str] = None,
                show_image: bool = False) -> list[Change]:
    """Karte auf den Druck stellen und aus dem Katalog füllen. only: nur diese Felder (None = alle
    vorgeschlagenen). Neuer Druck (oder show_image, „Richtige Karte finden") → dessen Bild;
    derselbe Druck → das Bild bleibt, nur ein fehlendes wird ergänzt. Committet nicht.
    → die übernommenen Änderungen."""
    from urllib.parse import urlparse

    from app.services import card_bulk, image_fallback, pokemon_com
    from app.services.tcgdex import is_allowed_image_url

    await enrich(db, row)
    fields = catalog_fields(db, row, card.sprache)
    relink = (card.tcgdex_card_id or "").upper() != row.card_id.upper()
    done = [c for c in changes(card, fields, row)
            if only is None or c.field in only or c.field in ("tcgdex_card_id", "set_id")]
    for c in done:
        if c.field != "bild":
            setattr(card, c.field, c.new)
    if relink or show_image:
        card.tcgdex_card_id, card.set_id = row.card_id, row.set_id or card.set_id
        url = image_url if image_url and is_allowed_image_url(image_url) else fields.get("bild_karte_url")
        if not url:
            card.bild_karte_url = None
            url = await image_fallback.find_catalog_image(db, card)
        if url:
            card.bild_karte_url = url
            if card.bild_karte_pfad:            # eigenes Foto wird zum Scan-Foto (Umschalter „Mein Foto")
                card_bulk.use_catalog_image(db, card)
            if (not row.image_url or row.image_source == "tcgplayer") and urlparse(url).hostname == pokemon_com.HOST:
                row.image_url, row.image_source, row.image_pfad = url, image_fallback.SOURCE, None
        card.regulation_mark = None                  # neuer Druck → Zeichen aus dem Katalog
    elif any(c.field == "bild" for c in done):
        card.bild_karte_url = fields["bild_karte_url"]
    if folierung in FOILS:
        card.folierung = folierung
    regulation.apply_to_card(db, card)
    return done


def fill_from_row(db: Session, card: PokemonCard, row: TcgdexCatalog, sets: Optional[dict] = None) -> int:
    """Ohne Nachladen und ohne Bildsuche (für die Deck-Seite): Druck setzen und fehlende Felder
    ergänzen — nichts Vorhandenes überschreiben. → Anzahl gefüllter Felder."""
    fields = catalog_fields(db, row, card.sprache, sets)
    card.tcgdex_card_id, card.set_id = row.card_id, row.set_id or card.set_id
    n = 0
    for c in changes(card, fields, row):
        if c.kind == "fill" and c.field not in ("tcgdex_card_id", "set_id"):
            if c.field == "bild":
                card.bild_karte_url = c.new
            else:
                setattr(card, c.field, c.new)
            n += 1
    if not _has_image(card) and fields.get("bild_karte_url"):
        card.bild_karte_url = fields["bild_karte_url"]
    regulation.apply_to_card(db, card)
    return n


# ── Admin: Karten mit Lücken ──────────────────────────────────────────────

def card_gaps(db: Session, card: PokemonCard, row: Optional[TcgdexCatalog], sets: dict) -> list[str]:
    """Was der Karte fehlt (oder nicht zum verknüpften Druck passt) — ohne Nachladen."""
    out: list[str] = []
    if row is None:
        out.append("print")
        if _empty(card.karten_nr):
            out.append("number")
        if _empty(card.set_edition):
            out.append("set")
        if _empty(card.englischer_name):
            out.append("english_name")
    else:
        fields = catalog_fields(db, row, card.sprache, sets)
        for c in changes(card, fields, row):
            gap = "differs" if c.kind == "change" else _GAP_OF.get(c.field)
            if gap and gap not in out:
                out.append(gap)
    if card.besessen and _empty(card.folierung):
        out.append("foiling")
    if not _has_image(card) and "image" not in out:
        out.append("image")
    return [g for g in GAPS if g in out]


def scan_gaps(db: Session) -> list[tuple[PokemonCard, list[str]]]:
    """Alle Karten aller Konten mit mindestens einer Lücke (Admin-Werkzeug)."""
    cards = db.scalars(select(PokemonCard).order_by(PokemonCard.id.desc())
                       .execution_options(all_users=True)).all()
    ids = {c.tcgdex_card_id for c in cards if c.tcgdex_card_id}
    rows = {r.card_id.upper(): r for r in db.scalars(select(TcgdexCatalog).where(TcgdexCatalog.card_id.in_(ids))).all()} if ids else {}
    if ids:
        missing = {i for i in ids if i.upper() not in rows}
        for i in missing:                       # Groß/klein-Abweichungen (selten)
            r = catalog_row_for(db, i)
            if r is not None:
                rows[i.upper()] = r
    sets = {s.set_id: s for s in db.scalars(select(PokemonSet).where(PokemonSet.set_id.isnot(None))).all()}
    out = []
    for c in cards:
        gaps = card_gaps(db, c, rows.get((c.tcgdex_card_id or "").upper()), sets)
        if gaps:
            out.append((c, gaps))
    return out
