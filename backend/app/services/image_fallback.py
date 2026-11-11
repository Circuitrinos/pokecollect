"""
Rückfall für Kartenbilder, wenn TCGdex keins hat:

  TCGdex  →  offizielle Kartendatenbank (pokemon.com)  →  TCGplayer-Produktfoto

pokemon.com zeigt saubere Scans der Karte; TCGplayer-Bilder sind oft Fotos
(Spiegelung, Perspektive) und stören obendrein den Bild-Abgleich beim Scannen.
Deshalb ersetzt ein Treffer auf pokemon.com auch ein TCGplayer-Bild im
Katalog. Ein späteres TCGdex-Bild gewinnt wieder (catalog.sync_catalog).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import case, or_, select, update
from sqlalchemy.orm import Session

from app.models.card import PokemonCard
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import pokemon_com
from app.services.catalog_lookup import catalog_row_for
from app.services.tcgdex import is_allowed_image_url

log = logging.getLogger(__name__)

SOURCE = "pokemon.com"
RECHECK_DAYS = 30


def local_from_nr(karten_nr: Optional[str]) -> Optional[str]:
    """"065/195" → "065"; "SWSH300" bleibt."""
    if not karten_nr:
        return None
    return karten_nr.split("/")[0].strip() or None


async def fallback_image(db: Session, card: PokemonCard, tc=None) -> Optional[str]:
    """
    Setzt card.bild_karte_url, wenn die Karte noch gar kein Bild hat (kein
    eigenes Foto, keine eigene URL, kein TCGdex-Bild). → gesetzte URL oder None.
    Ein pokemon.com-Treffer wird auch in die Katalogzeile übernommen.
    """
    if card.bild_karte_url or card.bild_karte_pfad or card.bild_pokedex_url:
        return None
    row = catalog_row_for(db, card.tcgdex_card_id) if card.tcgdex_card_id else None
    row_url = row.image_url if row is not None and is_allowed_image_url(row.image_url) else None
    if row_url and (row.image_source or "") != "tcgplayer":
        card.bild_karte_url = row_url                  # TCGdex- oder pokemon.com-Bild aus dem Katalog
        return row_url

    from app.services.card_image_service import resolve_set_id
    set_id = ((tc.set.id if tc is not None and tc.set else None) or card.set_id
              or (row.set_id if row is not None else None) or resolve_set_id(db, card.set_edition))
    local = ((tc.localId if tc is not None else None) or (row.local_id if row is not None else None)
             or local_from_nr(card.karten_nr))
    url = await pokemon_com.find_image(set_id, local)
    if url:
        card.bild_karte_url = url
        if row is not None and (not row.image_url or row.image_source == "tcgplayer"):
            row.image_url, row.image_source, row.image_pfad = url, SOURCE, None
        if row is not None:
            row.pokemon_com_checked = datetime.utcnow()
        return url
    if row_url:                                        # zuletzt: TCGplayer-Foto
        card.bild_karte_url = row_url
        return row_url
    return None


async def find_catalog_image(db: Session, card: PokemonCard) -> Optional[str]:
    """
    Katalogbild für eine Karte suchen, auch wenn ihr eigenes Foto angezeigt
    wird (Umschalter „Katalogbild"): gespeicherte URL → Katalog → TCGdex
    (verknüpft die Karte dabei) → pokemon.com → TCGplayer-Foto.
    """
    from app.services import tcgdex
    from app.services.card_image_service import apply_card_to_model, fetch_tcgdex_card, resolve_set_id

    from app.services.picture_finder import katalog_id, katalog_url

    if is_allowed_image_url(card.bild_karte_url) or katalog_id(card.bild_karte_url):
        return card.bild_karte_url
    row = catalog_row_for(db, card.tcgdex_card_id) if card.tcgdex_card_id else None
    if row is not None and is_allowed_image_url(row.image_url) and row.image_source != "tcgplayer":
        return row.image_url
    if row is not None and row.image_pfad and row.image_source == "manual":
        return katalog_url(row.card_id)      # lokal gespeichertes Katalogbild („Bild finden")
    set_id = card.set_id or resolve_set_id(db, card.set_edition) or (row.set_id if row is not None else None)
    tc = await fetch_tcgdex_card(set_id, card.karten_nr, card.sprache) if set_id else None
    if tc is not None:
        apply_card_to_model(card, tc, overwrite_image=False)     # richtige Karte verknüpfen
        url = tcgdex.image_url(tc.image)
        if url:
            return url
        row = catalog_row_for(db, card.tcgdex_card_id) if card.tcgdex_card_id else row
        if row is not None and is_allowed_image_url(row.image_url) and row.image_source != "tcgplayer":
            return row.image_url
    local = (tc.localId if tc is not None else None) or local_from_nr(card.karten_nr)
    url = await pokemon_com.find_image(set_id or (tc.set.id if tc is not None and tc.set else None), local)
    if url:
        if row is not None and (not row.image_url or row.image_source == "tcgplayer"):
            row.image_url, row.image_source, row.image_pfad = url, SOURCE, None
        return url
    if row is not None and is_allowed_image_url(row.image_url):
        return row.image_url
    return None


async def fill_catalog_from_pokemon_com(db: Session, limit: int = 500) -> dict:
    """
    Nächtlich: westliche Katalogzeilen ohne Bild (oder nur mit TCGplayer-Foto)
    bei pokemon.com nachsehen — Zeilen eigener Karten zuerst, jede Zeile
    höchstens alle RECHECK_DAYS Tage. Kurze Transaktionen (#66).
    """
    cutoff = datetime.utcnow() - timedelta(days=RECHECK_DAYS)
    owned = select(PokemonCard.tcgdex_card_id).where(PokemonCard.tcgdex_card_id.isnot(None))
    needs = or_(TcgdexCatalog.image_url.is_(None), TcgdexCatalog.image_source == "tcgplayer")
    rows = db.execute(
        select(TcgdexCatalog.card_id, TcgdexCatalog.set_id, TcgdexCatalog.local_id, TcgdexCatalog.image_url)
        .where(TcgdexCatalog.region == "west", needs,
               TcgdexCatalog.set_id.isnot(None), TcgdexCatalog.local_id.isnot(None),
               or_(TcgdexCatalog.pokemon_com_checked.is_(None), TcgdexCatalog.pokemon_com_checked < cutoff))
        .order_by(case((TcgdexCatalog.card_id.in_(owned), 0), else_=1), TcgdexCatalog.card_id)
        .limit(limit)
        .execution_options(all_users=True)
    ).all()
    db.commit()
    if not rows:
        return {"checked": 0, "found": 0}
    found = await pokemon_com.find_many([(cid, sid, lid) for cid, sid, lid, _old in rows])
    old_url = {cid: old for cid, _sid, _lid, old in rows}
    now = datetime.utcnow()
    for cid, _sid, _lid, _old in rows:
        vals: dict = {"pokemon_com_checked": now}
        if cid in found:
            vals.update(image_url=found[cid], image_source=SOURCE, image_pfad=None)
        db.execute(update(TcgdexCatalog).where(TcgdexCatalog.card_id == cid, needs).values(**vals)
                   .execution_options(synchronize_session=False))
    # eigene Karten ohne Bild (oder mit dem bisherigen TCGplayer-Foto des
    # Katalogs) bekommen das neue Bild gleich mit; eigene Fotos/URLs bleiben
    for cid, url in found.items():
        same = PokemonCard.bild_karte_url.is_(None)
        if old_url.get(cid):
            same = same | (PokemonCard.bild_karte_url == old_url[cid])
        db.execute(update(PokemonCard)
                   .where(PokemonCard.tcgdex_card_id == cid, same,
                          PokemonCard.bild_karte_pfad.is_(None), PokemonCard.bild_pokedex_url.is_(None))
                   .values(bild_karte_url=url)
                   .execution_options(all_users=True, synchronize_session=False))
    db.commit()
    log.info("pokemon.com-Bilder: %d Katalogzeilen geprüft, %d Bilder gefunden.", len(rows), len(found))
    return {"checked": len(rows), "found": len(found)}
