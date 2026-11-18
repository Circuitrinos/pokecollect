"""
Katalogkarten von Hand anlegen (Brian 18.11.: „MEP 113 Enamorus" — TCGdex kennt
sie noch nicht). Die Karte bekommt TCGdex' künftige ID; der Scan löst sie aus dem
Katalog auf; nimmt TCGdex sie auf, übernimmt der Katalog-Sync dieselbe Zeile.

Netzfrei: TCGdex und der Bild-Abgleich gemockt.
"""

import asyncio

import pytest

from app.database import SessionLocal
from app.models.card import PokemonCard
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog
from app.schemas.scan import ScanRawRead
from app.services import catalog as catalog_svc
from app.services import catalog_manual, tcgdex
from app.services.scan.resolver import resolve_one

API = "/api/v1"
SET_ID, CODE = "tmep", "TMEP"


@pytest.fixture()
def db(client, monkeypatch):
    calls = []

    async def told():
        calls.append(1)
    monkeypatch.setattr(catalog_manual, "tell_matcher", told)
    session = SessionLocal()
    session.merge(PokemonSet(code=CODE, name="Test-Promos", name_en="Test Black Star Promos",
                             set_id=SET_ID, series_id="me"))
    session.add(TcgdexCatalog(card_id=f"{SET_ID}-001", region="west", set_id=SET_ID, local_id="001",
                              local_id_num=1, name="Erste", name_en="First"))
    session.commit()
    session.matcher_calls = calls
    yield session
    session.rollback()
    session.query(PokemonCard).filter(PokemonCard.kartenname.ilike("TestManual%")).delete(synchronize_session=False)
    session.query(TcgdexCatalog).filter(TcgdexCatalog.set_id == SET_ID).delete(synchronize_session=False)
    session.query(PokemonSet).filter(PokemonSet.code == CODE).delete(synchronize_session=False)
    session.commit()
    session.close()


def _card(**kw):
    return {"set_id": SET_ID, "number": "113", "name": "Enamorus", "category": "Pokemon", "dex_id": 905,
            "rarity": "None", "regulation_mark": "i", "normal": False, "reverse": False, "holo": True, **kw}


def test_add_gets_the_id_tcgdex_will_use(client, db):
    r = client.post(f"{API}/catalog/manual", json=_card(number="5"))
    assert r.status_code == 201, r.text
    assert r.json() == {"card_id": "tmep-005"}          # Set zählt dreistellig ("001")
    r = client.post(f"{API}/catalog/manual", json=_card())
    assert r.json() == {"card_id": "tmep-113"}
    db.expire_all()
    row = db.get(TcgdexCatalog, "tmep-113")
    assert (row.name_en, row.set_code, row.local_id, row.local_id_num, row.regulation_mark) == \
        ("Enamorus", CODE, "113", 113, "I")
    assert (row.variants_normal, row.variants_holo) == (False, True)
    # bis TCGdex sie hat: vollständig — die Nachtläufe fragen nicht vergeblich nach
    assert row.added_by_hand and row.enriched and row.regulation_checked
    assert len(db.matcher_calls) == 2                    # der Scanner lädt den Katalog neu


def test_add_refuses_duplicates_and_bad_input(client, db):
    assert client.post(f"{API}/catalog/manual", json=_card()).status_code == 201
    r = client.post(f"{API}/catalog/manual", json=_card(number="113/094"))
    assert r.status_code == 409 and r.json()["detail"]["card_id"] == "tmep-113"
    r = client.post(f"{API}/catalog/manual", json=_card(number="1"))      # gibt es schon als "001"
    assert r.status_code == 409 and r.json()["detail"]["card_id"] == "tmep-001"
    assert client.post(f"{API}/catalog/manual", json=_card(set_id="nope")).status_code == 422
    assert client.post(f"{API}/catalog/manual", json=_card(number="1 1 3")).status_code == 422
    assert client.post(f"{API}/catalog/manual", json=_card(regulation_mark="7")).status_code == 422
    assert client.post(f"{API}/catalog/manual", json=_card(category="Pokémon")).status_code == 422


def test_admin_only(db):
    from test_multiuser import _client_for, _drop_user, _make_user
    bob = _make_user("bob-manual")
    try:
        bc = _client_for(bob)
        assert bc.get(f"{API}/catalog/manual").status_code == 403
        assert bc.post(f"{API}/catalog/manual", json=_card()).status_code == 403
        assert bc.delete(f"{API}/catalog/manual/tmep-001").status_code == 403
    finally:
        _drop_user(bob)


def test_list_and_remove(client, db):
    client.post(f"{API}/catalog/manual", json=_card())
    items = client.get(f"{API}/catalog/manual").json()["items"]
    mine = [i for i in items if i["set_id"] == SET_ID]
    assert [(i["card_id"], i["name"], i["set_name"], i["linked"]) for i in mine] == \
        [("tmep-113", "Enamorus", "Test Black Star Promos", 0)]

    db.add(PokemonCard(kartenname="TestManual Enamorus", besessen=True, tcgdex_card_id="tmep-113"))
    db.commit()
    r = client.delete(f"{API}/catalog/manual/tmep-113")
    assert r.status_code == 409                          # eigene Karte hängt daran
    db.query(PokemonCard).filter(PokemonCard.kartenname == "TestManual Enamorus").delete()
    db.commit()
    assert client.delete(f"{API}/catalog/manual/tmep-113").status_code == 204
    assert client.delete(f"{API}/catalog/manual/tmep-001").status_code == 404   # nicht von Hand


def test_scan_resolves_a_hand_added_card(client, db, monkeypatch):
    client.post(f"{API}/catalog/manual", json=_card(normal=True, reverse=True, holo=False))

    async def unknown(cid, lang):            # TCGdex: 404, kein Ausfall
        return None
    monkeypatch.setattr(tcgdex, "get_card", unknown)
    read = ScanRawRead(name="Enamorus", tcgdex_card_id="tmep-113", language="EN", confidence=0.9)
    s = SessionLocal()
    try:
        cand = asyncio.run(resolve_one(s, read))
    finally:
        s.close()
    assert cand.match is not None and cand.match.tcgdex_card_id == "tmep-113"
    assert cand.match.name == "Enamorus" and cand.match.dex_id == 905
    assert cand.foil_options == ["Normal", "Reverse Holo"]


def test_sync_takes_over_once_tcgdex_has_the_card(client, db, monkeypatch):
    client.post(f"{API}/catalog/manual", json=_card())
    row = db.get(TcgdexCatalog, "tmep-113")
    row.image_pfad, row.image_source = "images/catalog_manual/tmep-113.jpg", "manual"
    db.add(PokemonCard(kartenname="TestManual Owned", besessen=True, tcgdex_card_id="tmep-113",
                       bild_karte_url="katalog:tmep-113"))
    db.commit()
    image = "https://assets.tcgdex.net/en/me/mep/113"

    async def sets(lang="en"):
        return [tcgdex.TcgdexSetBrief(id=SET_ID, name="Test Black Star Promos")]

    async def set_detail(sid, lang="en"):
        return {"id": SET_ID, "serie": {"id": "me"},
                "cards": [{"id": "tmep-113", "localId": "113", "name": "Enamorus", "image": image}]}

    async def nothing(*a, **k):
        return (0, 0)
    monkeypatch.setattr(tcgdex, "get_sets", sets)
    monkeypatch.setattr(tcgdex, "get_set", set_detail)
    monkeypatch.setattr(catalog_svc, "_index_region_cards", nothing)
    monkeypatch.setattr(catalog_svc, "fill_region_from_tcgplayer", nothing)

    s = SessionLocal()
    try:
        asyncio.run(catalog_svc.sync_catalog(s))
    finally:
        s.close()
    db.expire_all()
    row = db.get(TcgdexCatalog, "tmep-113")
    assert not row.added_by_hand
    assert not row.enriched and not row.regulation_checked     # Volldaten kommt nachts nach
    assert row.image_url == image + "/high.webp" and row.image_pfad is None
    owned = db.query(PokemonCard).filter(PokemonCard.kartenname == "TestManual Owned").one()
    assert owned.bild_karte_url == image + "/high.webp"       # statt des Hand-Bilds
    assert db.get(TcgdexCatalog, "tmep-001") is not None       # Karten, die TCGdex nicht liefert, bleiben
