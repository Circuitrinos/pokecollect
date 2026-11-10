"""Scan in ein gewähltes Binder-Fach (start_position) statt ins erste freie."""
import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models.collection import collection_cards

API = "/api/v1"


def _item(name="Slot-Test", **kw):
    return {"kartenname": name, "sprache": "EN", **kw}


@pytest.fixture()
def binder(client):
    """3x3-Sammlung mit Karten in den Fächern 0, 1 und 5."""
    col = client.post(f"{API}/collections", json={"name": "Slot-Test", "binder_layout": "3x3"}).json()
    made = []
    for slot in (0, 1, 5):
        card = client.post(f"{API}/cards", json={"kartenname": f"Belegt {slot}", "besessen": True}).json()
        assert client.post(f"{API}/collections/{col['id']}/cards", json={"card_id": card["id"]}).status_code == 201
        assert client.put(f"{API}/collections/{col['id']}/cards/{card['id']}/slot", json={"slot": slot}).status_code == 204
        made.append(card["id"])
    yield col["id"], made
    db = SessionLocal()
    try:
        ids = [r for r in db.scalars(select(collection_cards.c.card_id)
                                     .where(collection_cards.c.collection_id == col["id"])).all()]
    finally:
        db.close()
    client.delete(f"{API}/collections/{col['id']}")
    for cid in set(ids) | set(made):
        client.delete(f"{API}/cards/{cid}")


def _commit(client, col, items, **kw):
    r = client.post(f"{API}/scan/commit", json={"target": "collection", "collection_id": col, "items": items, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _slots(col):
    db = SessionLocal()
    try:
        return dict(db.execute(select(collection_cards.c.card_id, collection_cards.c.position)
                               .where(collection_cards.c.collection_id == col)).all())
    finally:
        db.close()


def test_gewaehltes_fach_mit_exemplaren(client, binder):
    col, _ = binder
    body = _commit(client, col, [_item(copies=3)], start_position=3)
    # 3, 4, dann 5 belegt → 6
    assert body["positions"] == [3, 4, 6] and body["next_position"] == 7
    slots = _slots(col)
    assert [slots[c] for c in body["copy_ids"][0]] == [3, 4, 6]


def test_belegtes_fach_weicht_aus(client, binder):
    col, _ = binder
    body = _commit(client, col, [_item(), _item("Zweite")], start_position=1)
    assert body["positions"] == [2, 3] and body["next_position"] == 4


def test_ohne_wahl_erstes_freies_fach(client, binder):
    col, _ = binder
    body = _commit(client, col, [_item(), _item("Zweite"), _item("Dritte")])
    assert body["positions"] == [2, 3, 4] and body["next_position"] == 6


def test_mappenseite_auf_gewaehlte_seite(client, binder):
    col, _ = binder
    body = _commit(client, col, [_item(position=0), _item("Mitte", position=4)], start_position=18)
    assert body["positions"] == [18, 22]
    # ohne Wahl: neue Seite hinter der letzten belegten (Seite 3 hat jetzt Karten → ab 27)
    body = _commit(client, col, [_item(position=0), _item("Ende", position=8)])
    assert body["positions"] == [27, 35]
    # Seite 1 gewählt, Fach 0 belegt → nächstes freies dahinter, nie doppelt
    body = _commit(client, col, [_item(position=0)], start_position=0)
    assert body["positions"] == [2]
    slots = list(_slots(col).values())
    assert len(slots) == len(set(slots))


def test_start_position_grenzen(client, binder):
    col, _ = binder
    r = client.post(f"{API}/scan/commit", json={"target": "collection", "collection_id": col,
                                               "items": [_item()], "start_position": -1})
    assert r.status_code == 422


def test_pokedex_ziel_ignoriert_fach(client):
    r = client.post(f"{API}/scan/commit", json={"items": [_item()], "start_position": 5})
    assert r.status_code == 200 and r.json()["positions"] == [] and r.json()["next_position"] is None
    client.delete(f"{API}/cards/{r.json()['card_ids'][0]}")
