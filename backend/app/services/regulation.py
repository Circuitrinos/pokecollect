"""
Turnier-Legalität aus dem Regulation Mark (Standard-Format).

Quelle ist TCGdex (`regulationMark`, `energyType`, `legal`): der Katalog
speichert die Felder je Karte (services/catalog._apply_full, €-Repass und der
Nachholer unten), die eigenen Karten übernehmen sie (pokemon_cards.regulation_mark,
legal_mark, basic_energy).

Standard rechnet PokéCollect selbst — TCGdex' `legal.standard` wird nicht für
die Rotation gepflegt. Regeln (Play! Pokémon):
  - legal ist jedes Zeichen ab dem eingestellten niedrigsten Zeichen
    (Einstellung standard_min_mark, Default "H" = Saison 2026/27: H, I, J und
    alle künftigen Zeichen);
  - Basis-Energie ist immer legal (trägt kein Zeichen);
  - Reprint-Regel: eine ältere Trainer- oder Spezial-Energie-Karte ist legal,
    wenn eine gleichnamige Karte mit legalem Zeichen existiert. Für Pokémon gilt
    das NICHT. legal_mark ist deshalb das jüngste Zeichen aus eigener Karte und
    gleichnamigen Trainer-/Energie-Drucken (westlicher Katalog).

legal_mark: "H" usw.; "-" = geprüft, kein Zeichen (vor Schwert & Schild bzw.
TCGdex kennt keins); NULL = noch unbekannt. "-" sortiert vor "A", so bleibt
`legal_mark >= 'H'` eine einfache Filterbedingung.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable, Optional

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.models.card import PokemonCard
from app.models.setting import AppSetting
from app.models.tcgdex_catalog import TcgdexCatalog

log = logging.getLogger(__name__)

NO_MARK = "-"
DEFAULT_MIN_MARK = "H"
SETTING_KEY = "standard_min_mark"
_MARK_RE = re.compile(r"^[A-Z]$")

# Kategorien kommen im westlichen Katalog englisch ("Pokemon"/"Trainer"/
# "Energy"); die Liste fängt sicherheitshalber lokalisierte Werte mit ab.
_TRAINER = {"trainer", "dresseur", "allenatore", "entrenador", "treinador", "trainers", "トレーナーズ"}
_ENERGY = {"energy", "energie", "énergie", "energia", "energía", "エネルギー"}
_BASIC = {"basic", "basis", "base", "de base", "básica", "basica", "基本"}


def norm_mark(value) -> Optional[str]:
    """Ein einzelner Buchstabe A–Z (großgeschrieben) oder None."""
    if not isinstance(value, str):
        return None
    v = value.strip().upper()
    return v if _MARK_RE.match(v) else None


def standard_min_mark(db: Session) -> str:
    row = db.get(AppSetting, SETTING_KEY)
    return norm_mark(row.value if row else None) or DEFAULT_MIN_MARK


def _kind(category: Optional[str]) -> Optional[str]:
    c = (category or "").strip().lower()
    if c in _TRAINER:
        return "trainer"
    if c in _ENERGY:
        return "energy"
    if c:
        return "pokemon"
    return None


def _is_basic_energy(category: Optional[str], energy_type: Optional[str]) -> bool:
    return _kind(category) == "energy" and (energy_type or "").strip().lower() in _BASIC


def _reprint_key(row: TcgdexCatalog) -> Optional[tuple[str, str]]:
    """Schlüssel für die Reprint-Regel: nur Trainer und Spezial-Energie."""
    kind = _kind(row.category)
    if kind not in ("trainer", "energy") or _is_basic_energy(row.category, row.energy_type):
        return None
    name = (row.name_en or row.name or "").strip().lower()
    return (kind, name) if name else None


def apply_to_catalog(row: TcgdexCatalog, tc) -> None:
    """TCGdex-Volldaten → Legalitäts-Spalten einer Katalogzeile."""
    row.regulation_mark = norm_mark(getattr(tc, "regulationMark", None))
    row.energy_type = getattr(tc, "energyType", None) or None
    legal = getattr(tc, "legal", None)
    row.legal_standard = legal.standard if legal is not None else None
    row.legal_expanded = legal.expanded if legal is not None else None
    if getattr(tc, "category", None):
        row.category = tc.category
    row.regulation_checked = True


def _reprint_marks(db: Session, keys: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Jüngstes Zeichen je (Art, Name) unter den westlichen Katalog-Drucken."""
    if not keys:
        return {}
    names = {name for _, name in keys}
    name_col = func.lower(func.coalesce(TcgdexCatalog.name_en, TcgdexCatalog.name))
    rows = db.execute(
        select(TcgdexCatalog.category, TcgdexCatalog.energy_type, name_col, TcgdexCatalog.regulation_mark)
        .where(TcgdexCatalog.region == "west")
        .where(TcgdexCatalog.regulation_mark.isnot(None))
        .where(name_col.in_(names))
    ).all()
    out: dict[tuple[str, str], str] = {}
    for category, energy_type, name, mark in rows:
        kind = _kind(category)
        mark = norm_mark(mark)
        if mark is None or kind not in ("trainer", "energy") or _is_basic_energy(category, energy_type):
            continue
        key = (kind, (name or "").strip())
        if key in keys and mark > out.get(key, ""):
            out[key] = mark
    return out


def _compute(row: Optional[TcgdexCatalog], own_mark: Optional[str],
             reprints: dict[tuple[str, str], str]) -> tuple[Optional[str], Optional[str], bool]:
    """→ (regulation_mark, legal_mark, basic_energy) für eine Karte."""
    checked = row is not None and bool(row.regulation_checked)
    basic = checked and _is_basic_energy(row.category, row.energy_type)
    # Katalog gewinnt; kennt er kein Zeichen, bleibt ein von Hand gesetztes.
    mark = (norm_mark(row.regulation_mark) if checked else None) or norm_mark(own_mark)
    if mark is None and not checked:
        return None, None, False                      # unbekannt
    legal = mark
    key = _reprint_key(row) if checked else None
    if key is not None and reprints.get(key):
        legal = max(filter(None, (mark, reprints[key])))
    return mark, legal or NO_MARK, basic


def _catalog_rows(db: Session, ids: Iterable[str]) -> dict[str, TcgdexCatalog]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = db.scalars(select(TcgdexCatalog).where(TcgdexCatalog.card_id.in_(ids))).all()
    out = {r.card_id: r for r in rows}
    # case-tolerant (vgl. catalog_lookup.catalog_row_for)
    missing = {i for i in ids if i not in out}
    if missing:
        upper = {i.upper(): i for i in missing}
        for r in db.scalars(select(TcgdexCatalog)
                            .where(func.upper(TcgdexCatalog.card_id).in_(list(upper)))
                            .order_by(TcgdexCatalog.card_id)).all():
            out.setdefault(upper[r.card_id.upper()], r)
    return out


def apply_to_card(db: Session, card: PokemonCard) -> bool:
    """Legalitäts-Spalten einer einzelnen (ORM-)Karte neu berechnen. → geändert?"""
    row = _catalog_rows(db, [card.tcgdex_card_id]).get(card.tcgdex_card_id) if card.tcgdex_card_id else None
    key = _reprint_key(row) if row is not None and row.regulation_checked else None
    reprints = _reprint_marks(db, {key} if key else set())
    mark, legal, basic = _compute(row, card.regulation_mark, reprints)
    changed = (card.regulation_mark, card.legal_mark, bool(card.basic_energy)) != (mark, legal, basic)
    card.regulation_mark, card.legal_mark, card.basic_energy = mark, legal, basic
    return changed


def refresh_cards(db: Session, card_ids: Optional[Iterable[int]] = None) -> int:
    """
    Legalitäts-Spalten eigener Karten (aller Benutzer) aus dem Katalog neu
    berechnen — nach dem Nachholen der Katalogdaten und nachts (neue Reprints).
    Committet. → Anzahl geänderter Karten.
    """
    q = (select(PokemonCard.id, PokemonCard.tcgdex_card_id, PokemonCard.regulation_mark,
                PokemonCard.legal_mark, PokemonCard.basic_energy)
         .execution_options(all_users=True))
    if card_ids is not None:
        card_ids = list(card_ids)
        if not card_ids:
            return 0
        q = q.where(PokemonCard.id.in_(card_ids))
    cards = db.execute(q).all()
    rows = _catalog_rows(db, {c.tcgdex_card_id for c in cards})
    keys = {k for r in rows.values() if r.regulation_checked and (k := _reprint_key(r))}
    reprints = _reprint_marks(db, keys)

    changed = 0
    for cid, tid, own, old_legal, old_basic in cards:
        mark, legal, basic = _compute(rows.get(tid) if tid else None, own, reprints)
        if (own, old_legal, bool(old_basic)) != (mark, legal, basic):
            db.execute(update(PokemonCard).where(PokemonCard.id == cid)
                       .values(regulation_mark=mark, legal_mark=legal, basic_energy=basic)
                       .execution_options(all_users=True, synchronize_session=False))
            changed += 1
    db.commit()
    if changed:
        log.info("Regulation Marks: %d Karten aktualisiert.", changed)
    return changed


def standard_legal_clause(min_mark: str):
    """SQL-Bedingung „Standard-legal" für pokemon_cards."""
    return (PokemonCard.basic_energy == True) | (PokemonCard.legal_mark >= min_mark)  # noqa: E712


async def backfill_catalog(db: Session, limit: int = 2000, owned_only: bool = False) -> dict:
    """
    Holt die Legalitäts-Felder für bereits angereicherte Katalogzeilen nach
    (vor dieser Version angereichert). Vorrang: Zeilen eigener Karten, dann
    Trainer/Energie (Reprint-Regel), dann der Rest. Danach die eigenen Karten
    neu berechnen. Zwei kurze Transaktionen wie beim €-Repass (#66).
    """
    from app.services import tcgdex
    from app.services.catalog import _CONC, _catalog_lang

    owned = select(PokemonCard.tcgdex_card_id).where(PokemonCard.tcgdex_card_id.isnot(None))
    is_owned = TcgdexCatalog.card_id.in_(owned)
    prio_owned = case((is_owned, 0), else_=1)
    prio_kind = case((TcgdexCatalog.category.in_(["Trainer", "Energy"]), 0), else_=1)
    q = (select(TcgdexCatalog.card_id, TcgdexCatalog.region)
         .where(TcgdexCatalog.enriched == True, TcgdexCatalog.regulation_checked == False)  # noqa: E712
         .order_by(prio_owned, prio_kind, TcgdexCatalog.card_id)
         .limit(limit)
         .execution_options(all_users=True))
    if owned_only:
        q = q.where(is_owned)
    auswahl = db.execute(q).all()
    db.commit()
    if not auswahl:
        return {"checked": 0, "failed": 0, "cards_changed": 0}

    sem = asyncio.Semaphore(_CONC)
    data: dict[str, object] = {}
    failed = 0

    async def one(cid: str, region: Optional[str]):
        nonlocal failed
        async with sem:
            try:
                tc = await tcgdex.get_card(cid, _catalog_lang(region))
            except Exception as exc:  # noqa: BLE001 — eine kaputte Karte darf den Lauf nicht kippen
                log.warning("Regulation-Nachholer: %s fehlgeschlagen: %s", cid, exc)
                tc = None
        if tc is None:
            failed += 1
        else:
            data[cid] = tc

    await asyncio.gather(*(one(cid, region) for cid, region in auswahl))
    checked = 0
    for cid, tc in data.items():
        row = db.get(TcgdexCatalog, cid)
        if row is None:
            continue
        apply_to_catalog(row, tc)
        checked += 1
    db.commit()
    changed = refresh_cards(db)
    log.info("Regulation-Nachholer: %d Katalogzeilen geprüft, %d fehlgeschlagen, %d Karten geändert.",
             checked, failed, changed)
    return {"checked": checked, "failed": failed, "cards_changed": changed}


def status(db: Session) -> dict:
    """Wie weit sind die Katalog- und Kartendaten? (Einstellungsseite)"""
    owned = select(PokemonCard.tcgdex_card_id).where(PokemonCard.tcgdex_card_id.isnot(None))
    pending_owned = db.scalar(
        select(func.count()).select_from(TcgdexCatalog)
        .where(TcgdexCatalog.regulation_checked == False, TcgdexCatalog.card_id.in_(owned))  # noqa: E712
    ) or 0
    pending_all = db.scalar(
        select(func.count()).select_from(TcgdexCatalog)
        .where(TcgdexCatalog.regulation_checked == False)  # noqa: E712
    ) or 0
    return {"min_mark": standard_min_mark(db), "pending_owned": int(pending_owned), "pending_catalog": int(pending_all)}
