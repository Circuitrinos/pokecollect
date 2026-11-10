"""Sammel-Aktionen im Auswahl-Modus (POST /cards/meta/bulk)."""
import io

import pytest
from PIL import Image

from app.database import SessionLocal
from app.models.card import PokemonCard
from app.services.card_images import safe_media_path

from test_multiuser import _client_for, _drop_user, _make_user

API = "/api/v1"
CAT_URL = "https://assets.tcgdex.net/en/sv/sv03/125/high.webp"


def _jpeg(color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 56), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _db_card(cid):
    db = SessionLocal()
    try:
        return db.get(PokemonCard, cid, execution_options={"all_users": True})
    finally:
        db.close()


def _set(cid, **fields):
    db = SessionLocal()
    try:
        c = db.get(PokemonCard, cid, execution_options={"all_users": True})
        for k, v in fields.items():
            setattr(c, k, v)
        db.commit()
    finally:
        db.close()


def _card(c, name="Bulkmon", **fields):
    r = c.post(f"{API}/cards", json={"kartenname": name, "besessen": True})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    if fields:
        _set(cid, **fields)
    return cid


def _bulk(c, action, ids=None, value=None, **kw):
    return c.post(f"{API}/cards/meta/bulk", json={"action": action, "ids": ids or [], "value": value, **kw})


@pytest.fixture()
def cards(client):
    made = []
    yield lambda **kw: made.append(_card(client, **kw)) or made[-1]
    for cid in made:
        client.delete(f"{API}/cards/{cid}")


def test_zustand_und_prize_pack(client, cards):
    a, b = cards(), cards()
    r = _bulk(client, "zustand", [a, b], "Near Mint")
    assert r.json() == {"done": 2, "unchanged": 0, "missing": 0}
    assert _db_card(a).zustand == "Near Mint"
    assert _bulk(client, "zustand", [a], "Near Mint").json()["unchanged"] == 1
    assert _bulk(client, "zustand", [a], "Gebraucht").status_code == 422
    assert _bulk(client, "prize_pack", [a, b], True).json()["done"] == 2
    assert _db_card(b).prize_pack is True


def test_sammlung_hinzufuegen_entfernen(client, cards):
    a, b = cards(), cards()
    col = client.post(f"{API}/collections", json={"name": "Bulk-Ziel"}).json()
    try:
        assert _bulk(client, "add_to_collection", [a, b], col["id"]).json()["done"] == 2
        assert _bulk(client, "add_to_collection", [a], col["id"]).json()["unchanged"] == 1
        ids = {x["id"] for x in client.get(f"{API}/collections/{col['id']}/cards").json()}
        assert ids == {a, b}
        assert _bulk(client, "remove_from_collection", [a], col["id"]).json()["done"] == 1
        assert {x["id"] for x in client.get(f"{API}/collections/{col['id']}/cards").json()} == {b}
        assert _bulk(client, "add_to_collection", [a], 999999).status_code == 404
    finally:
        client.delete(f"{API}/collections/{col['id']}")


def test_fremde_karten_und_sammlungen_unerreichbar(client, cards):
    bob = _make_user("bob-bulk")
    try:
        bc = _client_for(bob)
        mine = cards()
        bobs = _card(bc, "Bobmon")
        bob_col = bc.post(f"{API}/collections", json={"name": "Bobs"}).json()
        assert _bulk(client, "delete", [bobs]).json() == {"done": 0, "unchanged": 0, "missing": 1}
        assert _db_card(bobs) is not None
        assert _bulk(client, "add_to_collection", [mine], bob_col["id"]).status_code == 404
    finally:
        _drop_user(bob)


def test_loeschen(client):
    a = _card(client)
    b = _card(client)
    assert _bulk(client, "delete", [a, b]).json()["done"] == 2
    assert _db_card(a) is None and _db_card(b) is None


def _photo(client, cid, with_original=True):
    files = {"file": ("crop.jpg", _jpeg(), "image/jpeg")}
    if with_original:
        files["original"] = ("orig.jpg", _jpeg((10, 10, 10)), "image/jpeg")
    assert client.post(f"{API}/cards/{cid}/image", files=files).status_code == 200


def test_katalogbild_und_zurueck_behaelt_foto(client, cards, monkeypatch):
    from app.services.scan import local_match
    monkeypatch.setattr(local_match, "poke_learn", lambda url: None)
    a = cards(tcgdex_card_id="sv03-125", bild_karte_url=CAT_URL)
    _photo(client, a)
    before = _db_card(a)
    shown = safe_media_path(before.bild_karte_pfad)
    assert shown.is_file()
    r = _bulk(client, "catalog_image", [a])
    assert r.json()["done"] == 1
    after = _db_card(a)
    assert after.bild_karte_pfad is None and after.bild_thumbnail_pfad is None
    assert after.bild_karte_url == CAT_URL
    kept = safe_media_path(after.bild_scan_pfad)
    assert kept.is_file() and not shown.exists()              # Foto verschoben, nicht gelöscht
    assert after.bild_original_pfad                             # Original bleibt (Lernen)
    assert client.get(f"{API}/cards/{a}").json()["bild_karte_pfad"] is None
    # und zurück
    assert _bulk(client, "own_photo", [a]).json()["done"] == 1
    back = _db_card(a)
    assert back.bild_scan_pfad is None and safe_media_path(back.bild_karte_pfad).is_file()
    assert safe_media_path(back.bild_thumbnail_pfad).is_file()


def test_katalogbild_ohne_katalogbild_bleibt_foto(client, cards):
    a = cards()                                                  # keine TCGdex-ID, keine URL
    _photo(client, a, with_original=False)
    assert _bulk(client, "catalog_image", [a]).json() == {"done": 0, "unchanged": 1, "missing": 0}
    assert _db_card(a).bild_karte_pfad


def test_alle_gescannten_umstellen(client, cards, monkeypatch):
    from app.services.scan import local_match
    monkeypatch.setattr(local_match, "poke_learn", lambda url: None)
    scanned = cards(tcgdex_card_id="sv03-125", bild_karte_url=CAT_URL)
    manual = cards(tcgdex_card_id="sv03-126", bild_karte_url=CAT_URL)
    _photo(client, scanned)
    _photo(client, manual, with_original=False)                  # bewusst eigenes Foto, kein Scan
    bob = _make_user("bob-bulk2")
    try:
        bc = _client_for(bob)
        bobs = _card(bc, "Bobmon", tcgdex_card_id="sv03-127", bild_karte_url=CAT_URL)
        _photo(bc, bobs)
        r = _bulk(client, "catalog_image", all_scanned=True)
        assert r.json()["done"] == 1
        assert _db_card(scanned).bild_karte_pfad is None
        assert _db_card(manual).bild_karte_pfad                  # manuelles Foto bleibt
        assert _db_card(bobs).bild_karte_pfad                    # anderer Benutzer unberührt
        assert _bulk(client, "zustand", value="Mint", all_scanned=True).status_code == 400
    finally:
        _drop_user(bob)
