"""Katalog: Sortieren nach Name folgt der UI-Sprache (englischer Name auf Englisch)."""
import pytest
from sqlalchemy import delete

from app.database import SessionLocal
from app.models.tcgdex_catalog import TcgdexCatalog

API = "/api/v1"
P = "testnamesort-"


@pytest.fixture()
def rows(client):
    db = SessionLocal()
    data = [("a", "Icognito V", "Unown V"), ("b", "Zapdos", "Zapdos"), ("c", "Bisasam", "Bulbasaur"),
            ("d", "Nur deutsch", None)]
    for cid, de, en in data:
        db.add(TcgdexCatalog(card_id=P + cid, region="west", set_id=P + "set", name=de, name_en=en,
                             category="Pokemon", image_url=None))
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id.like(P + "%")))
    db.commit()
    db.close()


def _names(client, **params):
    r = client.get(f"{API}/catalog", params={"set_id": P + "set", "sort": "name", "limit": 50, **params})
    assert r.status_code == 200, r.text
    return [i["card_id"].removeprefix(P) for i in r.json()["items"]]


def test_name_sort_follows_language(client, rows):
    assert _names(client) == ["c", "a", "d", "b"]                 # Bisasam, Icognito V, Nur deutsch, Zapdos
    assert _names(client, lang="EN") == ["c", "d", "a", "b"]      # Bulbasaur, Nur deutsch, Unown V, Zapdos
    assert _names(client, lang="EN", reverse=True) == ["b", "a", "d", "c"]
    assert client.get(f"{API}/catalog", params={"lang": "FR"}).status_code == 422


def test_english_name_is_searchable(client, rows):
    r = client.get(f"{API}/catalog", params={"q": "Unown", "limit": 50})
    assert P + "a" in [i["card_id"] for i in r.json()["items"]]
