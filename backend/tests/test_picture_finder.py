"""Bild finden: Vorschläge, Übernahme einer Bildadresse, eingefügtes Bild, Spiegel für lokale Katalogbilder."""
import asyncio
import json
import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import delete

from app.database import SessionLocal
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import bild_spiegel, picture_finder

API = "/api/v1"
CID = "testpic-mcd11-1"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (60, 84), (10, 120, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture()
def row(client):
    db = SessionLocal()
    db.add(TcgdexCatalog(card_id=CID, region="west", set_id="testpic-mcd11", name="Serpifeu", name_en="Snivy",
                         local_id="1", category="Pokemon", image_url=None))
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id == CID))
    db.commit()
    db.close()


def test_candidates_listed(client, row, monkeypatch):
    async def tc(r):
        return [{"url": "https://assets.tcgdex.net/fr/bw/mcd11/1/high.webp", "source": "tcgdex", "label": "TCGdex · FR"}]

    async def ptcg(r):
        return [{"url": "https://images.pokemontcg.io/mcd11/1_hires.png", "source": "pokemontcg", "label": "McDonald's 2011 · 1"},
                {"url": "https://evil.example.com/x.png", "source": "pokemontcg", "label": "nope"}]

    monkeypatch.setattr(picture_finder, "_tcgdex_langs", tc)
    monkeypatch.setattr(picture_finder, "_pokemontcg", ptcg)
    r = client.get(f"{API}/catalog/{CID}/pictures")
    assert r.status_code == 200, r.text
    body = r.json()
    urls = [c["url"] for c in body["candidates"]]
    assert "https://images.pokemontcg.io/mcd11/1_hires.png" in urls and not any("evil" in u for u in urls)
    assert all(c["spiegel"].startswith("/bild-spiegel/") for c in body["candidates"])
    assert body["query"].startswith("Snivy") and body["current"] is None


def test_use_url_and_propagate(client, row, monkeypatch, tmp_path):
    async def fake(url):
        return Path(tmp_path / "x.png")
    monkeypatch.setattr(bild_spiegel, "datei_fuer", fake)
    card = client.post(f"{API}/cards", json={"kartenname": "Snivy", "besessen": True}).json()
    db = SessionLocal()
    from app.models.card import PokemonCard
    db.query(PokemonCard).filter(PokemonCard.id == card["id"]).execution_options(all_users=True) \
        .update({"tcgdex_card_id": CID})
    db.commit()
    db.close()
    try:
        url = "https://images.pokemontcg.io/mcd11/1_hires.png"
        r = client.post(f"{API}/catalog/{CID}/picture", json={"url": url})
        assert r.status_code == 200, r.text
        assert r.json()["image_url"] == url and r.json()["cards_updated"] == 1
        assert client.get(f"{API}/cards/{card['id']}").json()["bild_karte_url"] == url
        assert client.post(f"{API}/catalog/{CID}/picture", json={"url": "https://example.com/a.png"}).status_code == 422
        assert client.get(f"{API}/catalog", params={"no_image": True, "q": "Snivy"}).json()["total"] == 0
    finally:
        client.delete(f"{API}/cards/{card['id']}")


def test_upload_and_mirror(client, row):
    card = client.post(f"{API}/cards", json={"kartenname": "Snivy", "besessen": True}).json()
    db = SessionLocal()
    from app.models.card import PokemonCard
    db.query(PokemonCard).filter(PokemonCard.id == card["id"]).execution_options(all_users=True) \
        .update({"tcgdex_card_id": CID})
    db.commit()
    db.close()
    try:
        assert client.get(f"{API}/catalog", params={"no_image": True, "q": "Snivy"}).json()["total"] >= 1
        r = client.post(f"{API}/catalog/{CID}/picture/upload", files={"file": ("p.png", _png(), "image/png")})
        assert r.status_code == 200, r.text
        assert r.json()["image_source"] == "manual" and "/images/" in r.json()["image_url"]
        got = client.get(f"{API}/cards/{card['id']}").json()
        assert got["bild_karte_url"] == f"katalog:{CID}" and got["bild_karte_spiegel"].startswith("/bild-spiegel/")
        img = client.get(got["bild_karte_spiegel"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
        bad = client.post(f"{API}/catalog/{CID}/picture/upload", files={"file": ("x.png", b"nope", "image/png")})
        assert bad.status_code == 400
    finally:
        client.delete(f"{API}/cards/{card['id']}")


def test_katalog_url_rules():
    assert picture_finder.katalog_id("katalog:sv03-125") == "sv03-125"
    assert picture_finder.katalog_id("katalog:../etc") is None
    assert bild_spiegel.ist_spiegelbar("katalog:sv03-125")
    assert not bild_spiegel.ist_spiegelbar("katalog:a/b")
    assert asyncio.run(bild_spiegel.datei_fuer("katalog:does-not-exist")) is None


def test_pictures_admin_only(row):
    from test_multiuser import _client_for, _drop_user, _make_user
    bob = _make_user("bob-pic")
    try:
        bc = _client_for(bob)
        assert bc.get(f"{API}/catalog/{CID}/pictures").status_code == 403
        assert bc.post(f"{API}/catalog/{CID}/picture/upload", files={"file": ("p.png", _png(), "image/png")}).status_code == 403
    finally:
        _drop_user(bob)


def test_picture_outline_forwards_to_the_matcher(client, row, monkeypatch):
    """Bild-Finder → Zuschneiden: grobe Ecken und gleichnamige Drucke als Referenz an matcher /outline, nur Admins."""
    import httpx
    from app.services import picture_finder

    db = SessionLocal()
    db.add_all([
        TcgdexCatalog(card_id="testpic-blw-1", region="west", set_id="testpic-blw", set_code="BLW", local_id="1",
                      name="Serpifeu", name_en="Snivy", image_url="https://assets.tcgdex.net/en/bw/bw1/1/high.webp"),
        TcgdexCatalog(card_id="testpic-noimg-2", region="west", set_id="testpic-x", name_en="Snivy"),
        TcgdexCatalog(card_id="testpic-other-3", region="west", set_id="testpic-x", name_en="Oshawott",
                      image_url="https://assets.tcgdex.net/en/bw/bw1/3/high.webp"),
    ])
    db.commit()
    db.close()
    seen = {}

    async def outline(data, quad, refs=None):
        seen.update(data=data, quad=quad, refs=refs)
        return {"quad": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], "method": "reference",
                "reference": "testpic-blw-1"}

    monkeypatch.setattr(picture_finder, "outline", outline)
    try:
        rough = [[0, 0], [1, 0], [1, 1], [0, 1]]
        r = client.post(f"{API}/catalog/{CID}/picture/outline", files={"file": ("p.jpg", b"img", "image/jpeg")},
                        data={"quad": json.dumps(rough)})
        assert r.status_code == 200, r.text
        assert r.json() == {"quad": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], "method": "reference",
                            "reference": "testpic-blw-1", "reference_label": "BLW 1"}
        # nur gleichnamige Drucke mit Bild
        assert seen == {"data": b"img", "quad": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                        "refs": ["testpic-blw-1"]}
        assert client.post(f"{API}/catalog/{CID}/picture/outline", files={"file": ("p.jpg", b"img", "image/jpeg")},
                           data={"quad": "[[5,5],[1,0],[1,1],[0,1]]"}).status_code == 422
        assert client.post(f"{API}/catalog/nope-1/picture/outline",
                           files={"file": ("p.jpg", b"img", "image/jpeg")}).status_code == 404

        async def down(data, quad, refs=None):
            raise httpx.HTTPError("down")
        monkeypatch.setattr(picture_finder, "outline", down)
        assert client.post(f"{API}/catalog/{CID}/picture/outline",
                           files={"file": ("p.jpg", b"img", "image/jpeg")}).status_code == 503
    finally:
        db = SessionLocal()
        db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id.in_(["testpic-blw-1", "testpic-noimg-2", "testpic-other-3"])))
        db.commit()
        db.close()
