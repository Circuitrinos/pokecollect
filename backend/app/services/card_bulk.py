"""
Sammel-Aktionen auf mehreren Karten (Auswahl-Modus in den Kartenrastern).

Jede Aktion arbeitet Karte für Karte und meldet, ob sie etwas geändert hat;
Karten anderer Benutzer sieht die (benutzergebundene) Sitzung gar nicht.

Bild-Aktionen:
  catalog_image  Anzeige → Katalogbild. Das eigene Foto wird NICHT gelöscht,
                 sondern wird zum Scan-Foto (bild_scan_pfad), aus dem der
                 Bild-Abgleich weiter lernt.
  own_photo      umgekehrt: ein vorhandenes Scan-Foto wird wieder Kartenbild.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.card import PokemonCard
from app.models.collection import collection_cards
from app.schemas.card import ZUSTAND_VALUES
from app.services.card_images import _save_upright, safe_media_path
from app.services.catalog_lookup import catalog_row_for
from app.services.collection_slots import next_free_position
from app.services.tcgdex import is_allowed_image_url

log = logging.getLogger(__name__)


def _rel(p: Path) -> str:
    return str(p.relative_to(Path(settings.images_dir).parent))


def use_catalog_image(db: Session, card: PokemonCard) -> bool:
    if not card.bild_karte_pfad:
        return False                                   # zeigt schon das Katalogbild
    from app.services.picture_finder import katalog_id, katalog_url

    url = card.bild_karte_url
    if not url and card.tcgdex_card_id:
        row = catalog_row_for(db, card.tcgdex_card_id)
        if row is not None:
            # lokal gespeichertes Katalogbild („Bild finden") gilt auch
            url = row.image_url or (katalog_url(row.card_id) if row.image_pfad and row.image_source == "manual" else None)
    if not url or not (is_allowed_image_url(url) or katalog_id(url)):
        return False                                   # kein Katalogbild → Foto bleibt
    src = safe_media_path(card.bild_karte_pfad)
    if src is not None and src.is_file():
        dst = Path(settings.images_dir) / f"card_{card.id}_scan{src.suffix.lower()}"
        old = safe_media_path(card.bild_scan_pfad)
        if old is not None and old.is_file() and old != dst:
            old.unlink()
        os.replace(src, dst)
        card.bild_scan_pfad = _rel(dst)
    thumb = safe_media_path(card.bild_thumbnail_pfad)
    if thumb is not None and thumb.is_file():
        thumb.unlink()
    card.bild_karte_pfad = None
    card.bild_thumbnail_pfad = None
    card.bild_karte_url = url
    return True


def use_own_photo(db: Session, card: PokemonCard) -> bool:
    if card.bild_karte_pfad:
        return False                                   # zeigt schon ein eigenes Foto
    src = safe_media_path(card.bild_scan_pfad)
    if src is None or not src.is_file():
        return False
    images_dir = Path(settings.images_dir)
    suffix = src.suffix.lower()
    dst, thumb = images_dir / f"card_{card.id}{suffix}", images_dir / f"card_{card.id}_thumb{suffix}"
    with src.open("rb") as f:
        _save_upright(SimpleNamespace(file=f), dst, thumb=thumb)
    src.unlink()
    card.bild_karte_pfad, card.bild_thumbnail_pfad, card.bild_scan_pfad = _rel(dst), _rel(thumb), None
    return True


def set_prize_pack(db: Session, card: PokemonCard, value) -> bool:
    v = bool(value)
    if card.prize_pack == v:
        return False
    card.prize_pack = v
    return True


def set_condition(db: Session, card: PokemonCard, value) -> bool:
    if value not in ZUSTAND_VALUES or card.zustand == value:
        return False
    card.zustand = value
    return True


def add_to_collection(db: Session, card: PokemonCard, collection_id: int) -> bool:
    exists = db.execute(select(collection_cards.c.card_id).where(
        collection_cards.c.collection_id == collection_id, collection_cards.c.card_id == card.id)).first()
    if exists:
        return False
    db.execute(collection_cards.insert().values(
        collection_id=collection_id, card_id=card.id, position=next_free_position(db, collection_id)))
    return True


def remove_from_collection(db: Session, card: PokemonCard, collection_id: int) -> bool:
    res = db.execute(collection_cards.delete().where(
        collection_cards.c.collection_id == collection_id, collection_cards.c.card_id == card.id))
    return bool(res.rowcount)


def owned_scanned_card_ids(db: Session) -> list[int]:
    """Alle eigenen Karten, die ein Scan-Foto als Kartenbild zeigen (für „alle umstellen")."""
    return list(db.scalars(select(PokemonCard.id).where(
        PokemonCard.besessen.is_(True), PokemonCard.bild_karte_pfad.isnot(None),
        PokemonCard.bild_original_pfad.isnot(None), PokemonCard.tcgdex_card_id.isnot(None))))
