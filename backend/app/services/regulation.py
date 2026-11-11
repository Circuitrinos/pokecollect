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
    wenn eine gleichnamige Karte mit legalem Zeichen UND gleichem Text existiert
    (Handbuch: Name identisch, Text funktional identisch — „damage" statt
    „damage counter" ist eine andere Karte). Für Pokémon gilt das NICHT.
    legal_mark ist deshalb das jüngste Zeichen aus eigener Karte und
    gleichnamigen Trainer-/Energie-Drucken mit gleichem Text (westlicher
    Katalog, Text normalisiert: Groß/klein, Akzente, Satzzeichen, Leerraum).
    Ein bloß umformulierter, funktional gleicher Text zählt hier NICHT — lieber
    zu streng als eine Karte fälschlich legal melden. name_mark merkt sich ein
    höheres Zeichen eines gleichnamigen Drucks mit anderem/unbekanntem Text
    (Hinweis im UI: von Hand prüfen).

legal_mark: "H" usw.; "-" = geprüft, kein Zeichen (vor Schwert & Schild bzw.
TCGdex kennt keins); NULL = noch unbekannt. "-" sortiert vor "A", so bleibt
`legal_mark >= 'H'` eine einfache Filterbedingung.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
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


def effect_key(text) -> Optional[str]:
    """Fingerabdruck eines Kartentexts: Akzente, Groß/klein, Satzzeichen und
    Leerraum zählen nicht, jedes Wort schon („damage" ≠ „damage counter")."""
    if not isinstance(text, str):
        return None
    t = unicodedata.normalize("NFKD", text)
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16] if t else None


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
    row.effect_key = effect_key(getattr(tc, "effect", None))
    row.stage = getattr(tc, "stage", None) or None
    row.trainer_type = getattr(tc, "trainerType", None) or None
    row.effect_checked = True
    row.regulation_checked = True


def _reprint_marks(db: Session, keys: set[tuple[str, str]]) -> dict[tuple, str]:
    """Jüngstes Zeichen je (Art, Name) und je (Art, Name, Text) unter den
    westlichen Katalog-Drucken mit Zeichen."""
    if not keys:
        return {}
    names = {name for _, name in keys}
    name_col = func.lower(func.coalesce(TcgdexCatalog.name_en, TcgdexCatalog.name))
    rows = db.execute(
        select(TcgdexCatalog.category, TcgdexCatalog.energy_type, name_col,
               TcgdexCatalog.regulation_mark, TcgdexCatalog.effect_key)
        .where(TcgdexCatalog.region == "west")
        .where(TcgdexCatalog.regulation_mark.isnot(None))
        .where(name_col.in_(names))
    ).all()
    out: dict[tuple, str] = {}
    for category, energy_type, name, mark, ekey in rows:
        kind = _kind(category)
        mark = norm_mark(mark)
        if mark is None or kind not in ("trainer", "energy") or _is_basic_energy(category, energy_type):
            continue
        key = (kind, (name or "").strip())
        if key not in keys:
            continue
        for k in ((key, key + (ekey,)) if ekey else (key,)):
            if mark > out.get(k, ""):
                out[k] = mark
    return out


def _compute(row: Optional[TcgdexCatalog], own_mark: Optional[str],
             reprints: dict[tuple, str]) -> tuple[Optional[str], Optional[str], bool, Optional[str]]:
    """→ (regulation_mark, legal_mark, basic_energy, name_mark) für eine Karte."""
    checked = row is not None and bool(row.regulation_checked)
    basic = checked and _is_basic_energy(row.category, row.energy_type)
    # Katalog gewinnt; kennt er kein Zeichen, bleibt ein von Hand gesetztes.
    mark = (norm_mark(row.regulation_mark) if checked else None) or norm_mark(own_mark)
    if mark is None and not checked:
        return None, None, False, None                # unbekannt
    legal = mark
    name_mark = None
    key = _reprint_key(row) if checked else None
    if key is not None:
        # Reprint zählt nur mit gleichem Text (eigener Text muss bekannt sein)
        same_text = reprints.get(key + (row.effect_key,)) if row.effect_key else None
        if same_text:
            legal = max(filter(None, (mark, same_text)))
        any_text = reprints.get(key)
        if any_text and any_text > (legal or ""):
            name_mark = any_text
    return mark, legal or NO_MARK, basic, name_mark


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
    new = _compute(row, card.regulation_mark, reprints)
    changed = (card.regulation_mark, card.legal_mark, bool(card.basic_energy), card.name_mark) != new
    card.regulation_mark, card.legal_mark, card.basic_energy, card.name_mark = new
    return changed


def refresh_cards(db: Session, card_ids: Optional[Iterable[int]] = None) -> int:
    """
    Legalitäts-Spalten eigener Karten (aller Benutzer) aus dem Katalog neu
    berechnen — nach dem Nachholen der Katalogdaten und nachts (neue Reprints).
    Committet. → Anzahl geänderter Karten.
    """
    q = (select(PokemonCard.id, PokemonCard.tcgdex_card_id, PokemonCard.regulation_mark,
                PokemonCard.legal_mark, PokemonCard.basic_energy, PokemonCard.name_mark)
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
    for cid, tid, own, old_legal, old_basic, old_name in cards:
        mark, legal, basic, name_mark = _compute(rows.get(tid) if tid else None, own, reprints)
        if (own, old_legal, bool(old_basic), old_name) != (mark, legal, basic, name_mark):
            db.execute(update(PokemonCard).where(PokemonCard.id == cid)
                       .values(regulation_mark=mark, legal_mark=legal, basic_energy=basic, name_mark=name_mark)
                       .execution_options(all_users=True, synchronize_session=False))
            changed += 1
    db.commit()
    if changed:
        log.info("Regulation Marks: %d Karten aktualisiert.", changed)
    return changed


def standard_legal_clause(min_mark: str):
    """SQL-Bedingung „Standard-legal" für pokemon_cards."""
    return (PokemonCard.basic_energy == True) | (PokemonCard.legal_mark >= min_mark)  # noqa: E712


def _needs_fetch():
    """Zeile braucht (nochmal) Volldaten: Zeichen noch nie geholt, oder
    Trainer/Energie ohne geholten Text (vor der Text-Prüfung geprüft)."""
    return (TcgdexCatalog.regulation_checked == False) | (  # noqa: E712
        (TcgdexCatalog.effect_checked == False)  # noqa: E712
        & TcgdexCatalog.category.in_(["Trainer", "Energy"]))


async def backfill_catalog(db: Session, limit: int = 2000, owned_only: bool = False) -> dict:
    """
    Holt die Legalitäts-Felder für Katalogzeilen nach. Vorrang: Zeilen eigener
    Karten, dann Trainer/Energie (Reprint-Regel), dann der Rest. Noch nicht
    angereicherte Zeilen werden dabei gleich voll angereichert (sonst blieben
    eigene Karten, deren Zeile die nächtliche Anreicherung noch nicht erreicht
    hat, ohne Zeichen). Danach Karten ohne Katalogzeile direkt bei TCGdex und
    die eigenen Karten neu berechnen. Kurze Transaktionen wie beim €-Repass (#66).
    """
    from app.services import tcgdex
    from app.services.catalog import _CONC, _apply_full, _catalog_lang

    owned = select(PokemonCard.tcgdex_card_id).where(PokemonCard.tcgdex_card_id.isnot(None))
    is_owned = TcgdexCatalog.card_id.in_(owned)
    prio_owned = case((is_owned, 0), else_=1)
    prio_kind = case((TcgdexCatalog.category.in_(["Trainer", "Energy"]), 0), else_=1)
    q = (select(TcgdexCatalog.card_id, TcgdexCatalog.region)
         .where(_needs_fetch())
         .order_by(prio_owned, prio_kind, TcgdexCatalog.card_id)
         .limit(limit)
         .execution_options(all_users=True))
    if owned_only:
        q = q.where(is_owned)
    auswahl = db.execute(q).all()
    db.commit()

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
        if row.enriched:
            apply_to_catalog(row, tc)
        else:
            _apply_full(row, tc)          # volle Anreicherung, inkl. Zeichen
        checked += 1
    db.commit()
    direct = await _backfill_without_catalog(db, limit=min(limit, 500))
    changed = refresh_cards(db)
    log.info("Regulation-Nachholer: %d Katalogzeilen geprüft, %d fehlgeschlagen, %d Karten ohne "
             "Katalogzeile direkt geholt, %d Karten geändert.", checked, failed, direct, changed)
    return {"checked": checked, "failed": failed, "direct": direct, "cards_changed": changed}


async def _backfill_without_catalog(db: Session, limit: int = 500) -> int:
    """Eigene Karten mit TCGdex-ID, aber ohne Katalogzeile (Set noch nicht im
    Katalog, Altbestand): Zeichen direkt bei TCGdex holen. → Anzahl IDs mit Zeichen."""
    from app.services import tcgdex

    known = select(func.upper(TcgdexCatalog.card_id))
    ids = db.scalars(
        select(PokemonCard.tcgdex_card_id).distinct()
        .where(PokemonCard.tcgdex_card_id.isnot(None))
        .where(PokemonCard.regulation_mark.is_(None), PokemonCard.legal_mark.is_(None))
        .where(func.upper(PokemonCard.tcgdex_card_id).not_in(known))
        .limit(limit)
        .execution_options(all_users=True)
    ).all()
    db.commit()
    if not ids:
        return 0
    sem = asyncio.Semaphore(5)
    marks: dict[str, str] = {}

    async def one(cid: str):
        async with sem:
            for lang in ("en", "ja"):
                try:
                    tc = await tcgdex.get_card(cid, lang)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Regulation-Nachholer: %s (%s) fehlgeschlagen: %s", cid, lang, exc)
                    tc = None
                if tc is not None:
                    mark = norm_mark(tc.regulationMark)
                    if mark:
                        marks[cid] = mark
                    return

    await asyncio.gather(*(one(cid) for cid in ids))
    for cid, mark in marks.items():
        db.execute(update(PokemonCard).where(PokemonCard.tcgdex_card_id == cid,
                                             PokemonCard.regulation_mark.is_(None))
                   .values(regulation_mark=mark)
                   .execution_options(all_users=True, synchronize_session=False))
    db.commit()
    return len(marks)


def status(db: Session) -> dict:
    """Wie weit sind die Katalog- und Kartendaten? (Einstellungsseite)"""
    owned = select(PokemonCard.tcgdex_card_id).where(PokemonCard.tcgdex_card_id.isnot(None))
    pending_owned = db.scalar(
        select(func.count()).select_from(TcgdexCatalog)
        .where(_needs_fetch(), TcgdexCatalog.card_id.in_(owned))
    ) or 0
    pending_all = db.scalar(
        select(func.count()).select_from(TcgdexCatalog).where(_needs_fetch())
    ) or 0
    return {"min_mark": standard_min_mark(db), "pending_owned": int(pending_owned), "pending_catalog": int(pending_all)}
