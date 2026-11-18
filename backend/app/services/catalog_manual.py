"""
Katalogkarten von Hand anlegen — für Karten, die TCGdex noch nicht kennt
(Brian 18.11.: „MEP 113 Enamorus"). Sonst ließen sie sich weder im Katalog
finden noch verknüpfen, und der Scanner kannte sie nicht.

Die Karte bekommt dieselbe ID, die TCGdex ihr geben wird ("<set_id>-<Nummer>",
Nummer im Format der übrigen Karten des Sets, z. B. "mep-113" oder "sv10-005").
Liefert TCGdex sie später, übernimmt der nächtliche Katalog-Sync dieselbe Zeile
(catalog.sync_catalog): eigene Karten bleiben verknüpft, und die Volldaten
(Preise, Text, Varianten …) kommen über die übliche Anreicherung nach.

Bis dahin gilt die Zeile als vollständig (enriched, Legalität geprüft) — sonst
fragten die Nachtläufe jede Nacht vergeblich bei TCGdex nach.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.card import PokemonCard
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import regulation

log = logging.getLogger(__name__)

CATEGORIES = ("Pokemon", "Trainer", "Energy")
_NUMBER = re.compile(r"^[A-Za-z]{0,6}[0-9]{1,4}[A-Za-z]{0,3}$")   # "113", "TG05", "SWSH300", "45a"


class ManualCardError(ValueError):
    """Eingabe passt nicht (→ 422) bzw. Karte gibt es schon (`existing` → 409)."""

    def __init__(self, message: str, existing: Optional[str] = None):
        super().__init__(message)
        self.existing = existing


def printed_number(raw: str) -> str:
    """"113/094" oder " 113 " → "113"; Sammelnummer ohne Nenner."""
    part = (raw or "").split("/")[0].strip()
    if not _NUMBER.fullmatch(part):
        raise ManualCardError("Nummer nicht lesbar")
    return part


def _local_id_for_set(db: Session, set_id: str, number: str) -> str:
    """Nummer so schreiben wie die übrigen Karten des Sets bei TCGdex: führen sie
    Nullen ("005"), dann auch hier — damit die ID später zu TCGdex' ID passt."""
    if not number.isdigit():
        return number
    n = str(int(number))
    widths = db.scalars(
        select(func.length(TcgdexCatalog.local_id))
        .where(TcgdexCatalog.set_id == set_id, TcgdexCatalog.local_id.like("0%"))
        .limit(1)
    ).all()
    if widths:
        return n.zfill(widths[0])
    any_card = db.scalar(select(TcgdexCatalog.card_id).where(TcgdexCatalog.set_id == set_id).limit(1))
    srow = db.scalars(select(PokemonSet).where(PokemonSet.set_id == set_id)).first()
    # Set ganz ohne Karten: Scarlet & Violet und Mega Evolution zählen dreistellig
    if any_card is None and srow is not None and srow.series_id in ("sv", "me"):
        return n.zfill(3)
    return n


def _set_row(db: Session, set_id: str) -> PokemonSet:
    row = db.scalars(select(PokemonSet).where(PokemonSet.set_id == set_id)).first()
    if row is None:
        raise ManualCardError("Set unbekannt")
    return row


def add_card(db: Session, *, set_id: str, number: str, name: str, category: str,
             name_de: Optional[str] = None, rarity: Optional[str] = None, dex_id: Optional[int] = None,
             regulation_mark: Optional[str] = None, illustrator: Optional[str] = None,
             trainer_type: Optional[str] = None, energy_type: Optional[str] = None,
             normal: bool = True, reverse: bool = False, holo: bool = False) -> TcgdexCatalog:
    if category not in CATEGORIES:
        raise ManualCardError("Kartenart unbekannt")
    name = (name or "").strip()
    if not name:
        raise ManualCardError("Name fehlt")
    mark = None
    if regulation_mark:
        mark = regulation.norm_mark(regulation_mark)
        if mark is None:
            raise ManualCardError("Regulation Mark ist ein Buchstabe")
    srow = _set_row(db, set_id)
    local_id = _local_id_for_set(db, set_id, printed_number(number))
    card_id = f"{set_id}-{local_id}"
    clash = db.get(TcgdexCatalog, card_id) or (db.scalars(
        select(TcgdexCatalog).where(TcgdexCatalog.set_id == set_id,
                                    TcgdexCatalog.local_id_num == int(local_id))).first()
        if local_id.isdigit() else None)
    if clash is not None:
        raise ManualCardError("Karte schon im Katalog", existing=clash.card_id)

    row = TcgdexCatalog(
        card_id=card_id, region="west", set_id=set_id, set_code=srow.code, set_name=srow.name,
        local_id=local_id, local_id_num=int(local_id) if local_id.isdigit() else None,
        name=(name_de or "").strip() or name, name_en=name,
        category=category, rarity=(rarity or "").strip() or None, dex_id=dex_id,
        illustrator=(illustrator or "").strip() or None,
        regulation_mark=mark,
        trainer_type=(trainer_type or None) if category == "Trainer" else None,
        energy_type=(energy_type or None) if category == "Energy" else None,
        variants_normal=normal, variants_reverse=reverse, variants_holo=holo, variants_firstedition=False,
        enriched=True, regulation_checked=True, effect_checked=True, added_by_hand=True,
    )
    db.add(row)
    db.commit()
    log.info("Katalog: %s (%s) von Hand angelegt", card_id, name)
    return row


def linked_cards(db: Session, card_id: str) -> int:
    """Eigene Karten aller Konten, die mit dieser Katalogkarte verknüpft sind."""
    return int(db.scalar(
        select(func.count()).select_from(PokemonCard)
        .where(func.upper(PokemonCard.tcgdex_card_id) == card_id.upper())
        .execution_options(all_users=True)) or 0)


def list_cards(db: Session) -> list[TcgdexCatalog]:
    return list(db.scalars(select(TcgdexCatalog).where(TcgdexCatalog.added_by_hand == True)  # noqa: E712
                           .order_by(TcgdexCatalog.set_id, TcgdexCatalog.local_id_num, TcgdexCatalog.card_id)).all())


def remove_card(db: Session, card_id: str) -> None:
    """Nur von Hand angelegte Karten, und nur solange keine eigene Karte daran hängt."""
    row = db.get(TcgdexCatalog, card_id)
    if row is None or not row.added_by_hand:
        raise ManualCardError("Keine von Hand angelegte Karte")
    if linked_cards(db, card_id):
        raise ManualCardError("Eigene Karten sind damit verknüpft", existing=card_id)
    db.delete(row)
    db.commit()
    log.info("Katalog: von Hand angelegte Karte %s entfernt", card_id)


def rarities(db: Session) -> list[str]:
    """Seltenheiten, wie TCGdex sie schreibt — als Auswahlhilfe im Formular."""
    return [r for r in db.scalars(select(TcgdexCatalog.rarity).where(TcgdexCatalog.rarity.isnot(None))
                                  .group_by(TcgdexCatalog.rarity).order_by(func.count().desc()).limit(40)).all() if r]


async def tell_matcher() -> None:
    """Bild-Abgleich: Katalog neu laden und neue Karten mit Bild aufnehmen (statt erst
    beim nächsten Tageslauf). Fehler egal — dann eben beim Tageslauf."""
    if not settings.matcher_url:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(f"{settings.matcher_url.rstrip('/')}/catalog/refresh")
    except httpx.HTTPError as exc:
        log.info("Bild-Abgleich nicht erreicht (Katalog lädt er beim Tageslauf): %s", exc)
