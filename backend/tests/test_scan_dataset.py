"""
Scan-Fotos als Lern- und Trainingsdaten: Ecken des Zuschnitts speichern, den
matcher nach einem Scan-Foto anstoßen, Admin-Export als ZIP (alle Benutzer,
Originale je Inhalt einmal).
"""
import io
import json
import zipfile

import pytest
from PIL import Image

from app.api.v1 import cards as cards_module
from app.services.scan import local_match

from test_multiuser import _client_for, _drop_user, _make_user

API = "/api/v1"
QUAD = [[0.1, 0.12], [0.9, 0.1], [0.92, 0.88], [0.08, 0.9]]


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 56), color=color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def pokes(monkeypatch):
    calls = []
    monkeypatch.setattr(local_match, "poke_learn", lambda url: calls.append(url))
    return calls


def _card(c, tcgdex="sv03-125", **kw):
    """Karte wie aus einem Scan: mit bestätigter TCGdex-ID (setzt nur /scan/commit)."""
    from app.database import SessionLocal
    from app.models.card import PokemonCard
    r = c.post(f"{API}/cards", json={"kartenname": "Glurak ex", "englischer_name": "Charizard ex",
                                      "besessen": True, "karten_nr": "125",
                                      "prize_pack": kw.get("prize_pack", False)})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    db = SessionLocal()
    try:
        db.get(PokemonCard, cid, execution_options={"all_users": True}).tcgdex_card_id = tcgdex
        db.commit()
    finally:
        db.close()
    return cid


def _upload(c, cid, original: bytes | None, quad=None):
    files = {"file": ("crop.jpg", _jpeg((200, 40, 40)), "image/jpeg")}
    if original is not None:
        files["original"] = ("orig.jpg", original, "image/jpeg")
    data = {"quad": json.dumps(quad)} if quad is not None else {}
    r = c.post(f"{API}/cards/{cid}/image", files=files, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def _quad_of(cid):
    from app.database import SessionLocal
    from app.models.card import PokemonCard
    db = SessionLocal()
    try:
        return db.get(PokemonCard, cid, execution_options={"all_users": True}).bild_quad
    finally:
        db.close()


def test_scanfoto_speichert_ecken_und_stoesst_matcher_an(client, pokes):
    cid = _card(client)
    try:
        _upload(client, cid, _jpeg((10, 10, 10)), QUAD)
        assert json.loads(_quad_of(cid)) == QUAD
        assert len(pokes) == 1
    finally:
        client.delete(f"{API}/cards/{cid}")


def test_foto_ohne_original_ist_kein_scanfoto(client, pokes):
    cid = _card(client)
    try:
        _upload(client, cid, None, QUAD)
        assert _quad_of(cid) is None
        assert pokes == []
    finally:
        client.delete(f"{API}/cards/{cid}")


@pytest.mark.parametrize("bad", ["nonsense", json.dumps([[0, 0], [1, 0]]), json.dumps([[0, 0], [9, 0], [1, 1], [0, 1]])])
def test_unplausible_ecken_werden_verworfen(bad):
    assert cards_module._parse_quad(bad) is None


def test_bild_loeschen_leert_ecken(client, pokes):
    cid = _card(client)
    try:
        _upload(client, cid, _jpeg((10, 10, 10)), QUAD)
        assert client.delete(f"{API}/cards/{cid}/image").status_code == 200
        assert _quad_of(cid) is None
    finally:
        client.delete(f"{API}/cards/{cid}")


def test_export_admin_alle_benutzer_originale_einmal(client, pokes):
    bob = _make_user("bob-ds")
    bc = _client_for(bob)
    shared_original = _jpeg((30, 60, 90))          # Mappenseite: zwei Karten aus EINEM Foto
    a1 = _card(client, "sv03-125")
    a2 = _card(client, "sv03-126", prize_pack=True)
    b1 = _card(bc, "base1-4")
    try:
        _upload(client, a1, shared_original, QUAD)
        _upload(client, a2, shared_original, QUAD)
        _upload(bc, b1, _jpeg((90, 60, 30)))         # ältere App: keine Ecken
        r = client.get(f"{API}/data/scan-dataset")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        labels = [json.loads(line) for line in z.read("labels.jsonl").decode().splitlines()]
        mine = {l["id"]: l for l in labels if l["id"] in (a1, a2, b1)}
        assert set(mine) == {a1, a2, b1}               # auch Bobs Scan
        assert mine[a1]["original"] == mine[a2]["original"] != mine[b1]["original"]
        assert mine[a2]["prize_pack"] is True and mine[a1]["card_id"] == "sv03-125"
        assert mine[a1]["quad"] == QUAD and mine[b1]["quad"] is None
        names = set(z.namelist())
        assert {mine[a1]["crop"], mine[a1]["original"], mine[b1]["original"], "README.txt"} <= names
        assert bc.get(f"{API}/data/scan-dataset").status_code == 403
    finally:
        for cid in (a1, a2):
            client.delete(f"{API}/cards/{cid}")
        _drop_user(bob)
