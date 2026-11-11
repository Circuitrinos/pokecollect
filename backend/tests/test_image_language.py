"""Bildsprache einer Karte: TCGdex-Katalogbild in einer anderen westlichen Sprache."""
from pathlib import Path

from app.database import SessionLocal
from app.models.card import PokemonCard
from app.services import bild_spiegel, image_lang

API = "/api/v1"
DE_URL = "https://assets.tcgdex.net/de/swsh/swshp/SWSH300/high.webp"


def test_with_lang():
    assert image_lang.image_lang(DE_URL) == "DE"
    assert image_lang.with_lang(DE_URL, "EN") == "https://assets.tcgdex.net/en/swsh/swshp/SWSH300/high.webp"
    assert image_lang.with_lang(DE_URL, "fr").startswith("https://assets.tcgdex.net/fr/")
    assert image_lang.with_lang("https://assets.tcgdex.net/ja/SV/SV1S/001/high.webp", "EN") is None
    assert image_lang.with_lang("https://example.com/de/x.png", "EN") is None
    assert image_lang.with_lang(DE_URL, "JP") is None


def test_set_image_language(client, monkeypatch, tmp_path):
    seen = []

    async def fake(url):
        seen.append(url)
        return None if "/it/" in url else Path(tmp_path / "x.webp")

    monkeypatch.setattr(bild_spiegel, "datei_fuer", fake)
    card = client.post(f"{API}/cards", json={"kartenname": "Icognito V", "besessen": True}).json()
    db = SessionLocal()
    db.query(PokemonCard).filter(PokemonCard.id == card["id"]).execution_options(all_users=True) \
        .update({"bild_karte_url": DE_URL})
    db.commit()
    db.close()
    card = client.get(f"{API}/cards/{card['id']}").json()
    try:
        assert card["bild_sprache"] == "DE"
        r = client.post(f"{API}/cards/{card['id']}/image-language", json={"lang": "EN"})
        assert r.status_code == 200, r.text
        assert r.json()["bild_karte_url"] == DE_URL.replace("/de/", "/en/") and r.json()["bild_sprache"] == "EN"
        r = client.post(f"{API}/cards/{card['id']}/image-language", json={"lang": "IT"},
                        headers={"X-UI-Lang": "EN"})
        assert r.status_code == 404 and r.json()["detail"] == "There is no card image in this language"
        assert client.get(f"{API}/cards/{card['id']}").json()["bild_sprache"] == "EN"   # unverändert
        plain = client.post(f"{API}/cards", json={"kartenname": "Ohne Bild", "besessen": True}).json()
        assert plain["bild_sprache"] is None
        assert client.post(f"{API}/cards/{plain['id']}/image-language", json={"lang": "EN"}).status_code == 422
        client.delete(f"{API}/cards/{plain['id']}")
    finally:
        client.delete(f"{API}/cards/{card['id']}")
