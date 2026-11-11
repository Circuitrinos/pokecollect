"""
Decks (services/decks.py): Liste anlegen/importieren, Legalität, mit eigenen
Karten füllen, Karten zuordnen, PTCGL-Export.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.card import PokemonCard
from app.models.deck import Deck, DeckSlot
from app.schemas.card import CardResponse
from app.schemas.deck import (
    DeckAssigned, DeckCandidate, DeckCardsAdd, DeckCreate, DeckDetail, DeckFillResult, DeckImport,
    DeckImportResult, DeckLegality, DeckSlotAdd, DeckSlotResponse, DeckSlotUpdate, DeckSummary, DeckUpdate,
)
from app.services import catalog_images
from app.services import decks as svc
from app.services.catalog_lookup import catalog_row_for

router = APIRouter(prefix="/decks", tags=["decks"])


def _deck(db: Session, deck_id: int) -> Deck:
    deck = db.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck nicht gefunden")
    return deck


def _slot(db: Session, deck: Deck, slot_id: int) -> DeckSlot:
    slot = db.get(DeckSlot, slot_id)
    if slot is None or slot.deck_id != deck.id:
        raise HTTPException(status_code=404, detail="Karte nicht in der Deckliste")
    return slot


def _image(row, base_url: str):
    if row is None:
        return None
    return catalog_images.resolve_catalog_image_url(row.image_pfad, row.image_url, base_url)


def _summary_fields(db: Session, deck: Deck, base_url: str, slots, legal: dict, cov) -> dict:
    rows = svc._rows(db, (s.tcgdex_card_id for s in slots))
    cover = next((s for s in slots if s.section == "pokemon" and s.tcgdex_card_id), None) \
        or next((s for s in slots if s.tcgdex_card_id), None)
    return dict(
        id=deck.id, name=deck.name, format=deck.format or "standard", notizen=deck.notizen,
        erstellt_am=deck.erstellt_am, aktualisiert_am=deck.aktualisiert_am,
        total=legal["total"], status=legal["status"],
        in_deck=sum(len(v) for v in cov.by_slot.values()),
        cover_url=_image(rows.get(cover.tcgdex_card_id), base_url) if cover is not None else None,
    )


def _detail(db: Session, deck: Deck, request: Request) -> DeckDetail:
    base_url = str(request.base_url)
    slots = svc._slots(db, deck.id)
    legal = svc.legality(db, deck, slots)
    cov = svc.coverage(db, deck, slots)
    rows = svc._rows(db, (s.tcgdex_card_id for s in slots))
    free = svc.FreeCards(db)
    out = []
    for s in slots:
        info = legal["slots"][s.id]
        cands = free.for_slot(db, s, loose_pokemon=True)
        out.append(DeckSlotResponse(
            id=s.id, tcgdex_card_id=s.tcgdex_card_id, name=s.name, set_code=s.set_code, number=s.number,
            count=s.count, section=s.section, match=s.match,
            image_url=_image(rows.get(s.tcgdex_card_id) if s.tcgdex_card_id else None, base_url),
            rarity=info["rarity"], stage=info["stage"], trainer_type=info["trainer_type"],
            status=info["status"], legal_mark=info["legal_mark"], ace_spec=info["ace_spec"],
            cards=[DeckAssigned(match=how, card=CardResponse.model_validate(c)) for c, how in cov.by_slot[s.id]],
            available=sum(1 for _c, how in cands if how != "other_print"),
            available_other=sum(1 for _c, how in cands if how == "other_print"),
        ))
    legality = DeckLegality(**{k: v for k, v in legal.items() if k != "slots"})
    return DeckDetail(**_summary_fields(db, deck, base_url, slots, legal, cov), legality=legality, slots=out,
                      extra=[CardResponse.model_validate(c) for c in cov.extra])


def _touch(deck: Deck) -> None:
    from datetime import datetime
    deck.aktualisiert_am = datetime.utcnow()


@router.get("", response_model=list[DeckSummary])
def list_decks(request: Request, db: Session = Depends(get_db)):
    base_url = str(request.base_url)
    out = []
    for deck in db.scalars(select(Deck).order_by(Deck.name, Deck.id)).all():
        slots = svc._slots(db, deck.id)
        legal = svc.legality(db, deck, slots)
        cov = svc.coverage(db, deck, slots)
        out.append(DeckSummary(**_summary_fields(db, deck, base_url, slots, legal, cov)))
    return out


@router.post("", response_model=DeckImportResult, status_code=201)
def create_deck(payload: DeckCreate, request: Request, db: Session = Depends(get_db)):
    deck = Deck(name=payload.name.strip(), format=payload.format, notizen=payload.notizen)
    db.add(deck)
    db.flush()
    result = {"lines": 0, "matched": 0, "unmatched": 0, "not_found": [], "unreadable": []}
    if payload.text and payload.text.strip():
        result = svc.import_lines(db, deck, svc.parse_ptcgl(payload.text))
    if payload.fill:
        svc.fill(db, deck)
    db.commit()
    return DeckImportResult(deck=_detail(db, deck, request), **result)


@router.get("/{deck_id}", response_model=DeckDetail)
def get_deck(deck_id: int, request: Request, db: Session = Depends(get_db)):
    return _detail(db, _deck(db, deck_id), request)


@router.put("/{deck_id}", response_model=DeckDetail)
def update_deck(deck_id: int, payload: DeckUpdate, request: Request, db: Session = Depends(get_db)):
    deck = _deck(db, deck_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        deck.name = data["name"].strip()
    if "format" in data:
        deck.format = data["format"]
    if "notizen" in data:
        deck.notizen = data["notizen"]
    _touch(deck)
    db.commit()
    return _detail(db, deck, request)


@router.delete("/{deck_id}", status_code=204)
def delete_deck(deck_id: int, db: Session = Depends(get_db)):
    deck = _deck(db, deck_id)
    # Karten kommen frei (FK ON DELETE SET NULL); auch ORM-seitig, damit die Session stimmt
    for card in db.scalars(select(PokemonCard).where(PokemonCard.deck_id == deck.id)).all():
        card.deck_id = None
    db.delete(deck)
    db.commit()


@router.post("/{deck_id}/import", response_model=DeckImportResult)
def import_list(deck_id: int, payload: DeckImport, request: Request, db: Session = Depends(get_db)):
    deck = _deck(db, deck_id)
    parsed = svc.parse_ptcgl(payload.text)
    if not parsed.lines:
        raise HTTPException(status_code=400, detail="Keine Kartenzeilen erkannt")
    result = svc.import_lines(db, deck, parsed, replace=payload.replace)
    _touch(deck)
    db.commit()
    return DeckImportResult(deck=_detail(db, deck, request), **result)


@router.get("/{deck_id}/export", response_class=PlainTextResponse)
def export_list(deck_id: int, db: Session = Depends(get_db)):
    return PlainTextResponse(svc.export_ptcgl(db, _deck(db, deck_id)))


@router.post("/{deck_id}/fill", response_model=DeckFillResult)
def fill_deck(deck_id: int, request: Request, db: Session = Depends(get_db)):
    deck = _deck(db, deck_id)
    added = svc.fill(db, deck)
    _touch(deck)
    db.commit()
    return DeckFillResult(deck=_detail(db, deck, request), added=added)


@router.post("/{deck_id}/refresh", response_model=DeckDetail)
async def refresh_data(deck_id: int, request: Request, db: Session = Depends(get_db)):
    """Fehlende Kartendaten (Stufe, Trainer-Art, Zeichen) bei TCGdex nachholen."""
    deck = _deck(db, deck_id)
    await svc.fetch_missing_data(db, deck)
    return _detail(db, deck, request)


@router.post("/{deck_id}/slots", response_model=DeckDetail)
def add_slot(deck_id: int, payload: DeckSlotAdd, request: Request, db: Session = Depends(get_db)):
    deck = _deck(db, deck_id)
    row = catalog_row_for(db, payload.tcgdex_card_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Karte nicht im Katalog")
    svc.add_slot(db, deck, row, payload.count)
    _touch(deck)
    db.commit()
    return _detail(db, deck, request)


@router.put("/{deck_id}/slots/{slot_id}", response_model=DeckDetail)
def update_slot(deck_id: int, slot_id: int, payload: DeckSlotUpdate, request: Request,
                db: Session = Depends(get_db)):
    deck = _deck(db, deck_id)
    slot = _slot(db, deck, slot_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("count") == 0:
        db.delete(slot)
    else:
        if "count" in data:
            slot.count = data["count"]
        if "section" in data:
            slot.section = data["section"]
    _touch(deck)
    db.commit()
    return _detail(db, deck, request)


@router.delete("/{deck_id}/slots/{slot_id}", response_model=DeckDetail)
def delete_slot(deck_id: int, slot_id: int, request: Request, db: Session = Depends(get_db)):
    deck = _deck(db, deck_id)
    db.delete(_slot(db, deck, slot_id))
    _touch(deck)
    db.commit()
    return _detail(db, deck, request)


@router.get("/{deck_id}/slots/{slot_id}/candidates", response_model=list[DeckCandidate])
def slot_candidates(deck_id: int, slot_id: int, db: Session = Depends(get_db)):
    """Eigene Karten in keinem Deck, die auf diesen Platz passen."""
    deck = _deck(db, deck_id)
    slot = _slot(db, deck, slot_id)
    return [DeckCandidate(match=how, card=CardResponse.model_validate(c))
            for c, how in svc.FreeCards(db).for_slot(db, slot, loose_pokemon=True)[:60]]


@router.post("/{deck_id}/cards", response_model=DeckDetail)
def add_cards(deck_id: int, payload: DeckCardsAdd, request: Request, db: Session = Depends(get_db)):
    """Eigene Karten ins Deck legen. Steckt eine schon in einem anderen Deck,
    nur mit move=true (sonst 409). Passt eine auf keinen freien Platz, wächst die Liste."""
    deck = _deck(db, deck_id)
    cards = db.scalars(select(PokemonCard).where(PokemonCard.id.in_(payload.card_ids))).all()
    if len(cards) != len(set(payload.card_ids)):
        raise HTTPException(status_code=404, detail="Karte nicht gefunden")
    if any(not c.besessen for c in cards):
        raise HTTPException(status_code=400, detail="Nur eigene Karten können in ein Deck")
    elsewhere = [c for c in cards if c.deck_id is not None and c.deck_id != deck.id]
    if elsewhere and not payload.move:
        raise HTTPException(status_code=409, detail="Karte steckt schon in einem anderen Deck")
    for c in cards:
        if c.deck_id != deck.id:
            svc.assign(db, deck, c)
    _touch(deck)
    db.commit()
    return _detail(db, deck, request)


@router.delete("/{deck_id}/cards/{card_id}", response_model=DeckDetail)
def remove_card(deck_id: int, card_id: int, request: Request, db: Session = Depends(get_db)):
    deck = _deck(db, deck_id)
    card = db.get(PokemonCard, card_id)
    if card is None or card.deck_id != deck.id:
        raise HTTPException(status_code=404, detail="Karte nicht in diesem Deck")
    card.deck_id = None
    _touch(deck)
    db.commit()
    return _detail(db, deck, request)
