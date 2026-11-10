"""Gleiche Karte mehrfach aus einem Scan: copies=N legt N Exemplare an."""
from app.database import SessionLocal
from app.models.card import PokemonCard
from app.models.collection import collection_cards
from sqlalchemy import select

API = "/api/v1"


def _item(**kw):
    base = {"kartenname": "Pikachu", "englischer_name": "Pikachu", "karten_nr": "063", "sprache": "EN",
            "tcgdex_card_id": "sv01-063", "set_id": "sv01"}
    return {**base, **kw}


def test_drei_exemplare_in_sammlung_mit_slots(client):
    col = client.post(f"{API}/collections", json={"name": "Copies-Test", "binder_layout": "3x3"}).json()
    body = {}
    try:
        r = client.post(f"{API}/scan/commit", json={"target": "collection", "collection_id": col["id"],
                                                    "items": [_item(copies=3, position=4), _item(kartenname="Evoli", karten_nr="1")]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == 4
        assert len(body["card_ids"]) == 2                 # je Item das erste Exemplar (Foto-Upload)
        assert len(body["all_card_ids"]) == 4 and body["card_ids"][0] == body["all_card_ids"][0]
        db = SessionLocal()
        try:
            rows = db.execute(select(collection_cards.c.card_id, collection_cards.c.position)
                              .where(collection_cards.c.collection_id == col["id"])).all()
            pos = {cid: p for cid, p in rows}
            first = body["card_ids"][0]
            assert pos[first] == 4                         # Binder-Slot nur fürs erste Exemplar
            assert len(set(pos.values())) == 4             # alle in eigenen Slots
            cards = [db.get(PokemonCard, cid, execution_options={"all_users": True}) for cid in body["all_card_ids"][:3]]
            assert {c.tcgdex_card_id for c in cards} == {"sv01-063"} and all(c.besessen for c in cards)
        finally:
            db.close()
    finally:
        for cid in body.get("all_card_ids", []):
            client.delete(f"{API}/cards/{cid}")
        client.delete(f"{API}/collections/{col['id']}")


def test_wunschliste_ignoriert_anzahl(client):
    r = client.post(f"{API}/scan/commit", json={"target": "wishlist", "items": [_item(copies=5)]})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    client.delete(f"{API}/cards/{r.json()['card_ids'][0]}")


def test_anzahl_grenzen(client):
    assert client.post(f"{API}/scan/commit", json={"items": [_item(copies=0)]}).status_code == 422
    assert client.post(f"{API}/scan/commit", json={"items": [_item(copies=51)]}).status_code == 422


def test_ohne_anzahl_wie_bisher(client):
    r = client.post(f"{API}/scan/commit", json={"items": [_item()]})
    assert r.status_code == 200 and r.json()["created"] == 1 and r.json()["card_ids"] == r.json()["all_card_ids"]
    client.delete(f"{API}/cards/{r.json()['card_ids'][0]}")
