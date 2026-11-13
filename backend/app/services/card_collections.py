"""Kartenlisten mit den Sammlungen jeder Karte (eine Abfrage je Seite) — für die
Kartenlisten, „Karte zuweisen" und die Kartenwahl einer Deck-Zeile."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.collection import Collection, collection_cards
from app.schemas.card import CardResponse, CollectionRef, binder_place


def with_collections(db: Session, cards) -> list[CardResponse]:
    out = [CardResponse.model_validate(c) for c in cards]
    ids = [c.id for c in cards]
    by_card: dict[int, list] = {i: [] for i in ids}
    if ids:
        rows = db.execute(select(collection_cards.c.card_id, Collection.id, Collection.name,
                                 collection_cards.c.position, Collection.binder_layout)
                          .join(Collection, Collection.id == collection_cards.c.collection_id)
                          .where(collection_cards.c.card_id.in_(ids))
                          .order_by(Collection.name)).all()
        for card_id, cid, name, position, layout in rows:
            by_card[card_id].append(CollectionRef(id=cid, name=name, place=binder_place(position, layout)))
    for r in out:
        r.sammlungen = by_card.get(r.id, [])
    return out
