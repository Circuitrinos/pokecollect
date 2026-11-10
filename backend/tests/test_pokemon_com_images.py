"""Bild-Rückfall auf die offizielle Kartendatenbank (pokemon.com)."""
import asyncio

import httpx
import pytest
from sqlalchemy import delete

from app.database import SessionLocal
from app.models.card import PokemonCard
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import image_fallback, pokemon_com
from app.services.provenance import bild_quelle
from app.services.tcgdex import is_allowed_image_url

S = "https://assets.pokemon.com/static-assets/content-assets/cms2/img/cards/web/"


@pytest.fixture(autouse=True)
def _frischer_cache():
    pokemon_com._known.clear()
    yield
    pokemon_com._known.clear()


def test_kandidaten_fuer_die_beispiele():
    assert pokemon_com.candidate_urls("swsh12", "065")[0] == S + "SWSH12/SWSH12_EN_65.png"
    assert pokemon_com.candidate_urls("swshp", "SWSH300")[0] == S + "SWSHP/SWSHP_EN_SWSH300.png"
    assert pokemon_com._codes("sv08") == ["SV08", "SV8"]
    assert pokemon_com._codes("sv08.5")[0] == "SV8PT5"
    assert pokemon_com._codes("swsh4.5")[0] == "SWSH45"
    assert pokemon_com._codes("swsh12.5")[0] == "SWSH12PT5"
    assert pokemon_com._codes("swsh11tg") == ["SWSH11"]            # Trainer Gallery im Haupt-Set
    assert pokemon_com.candidate_urls("swsh11tg", "TG05")[0] == S + "SWSH11/SWSH11_EN_TG05.png"
    assert pokemon_com.candidate_urls(None, "1") == [] and pokemon_com.candidate_urls("sv01", "") == []
    assert all(is_allowed_image_url(u) for u in pokemon_com.candidate_urls("sv08.5", "117"))


def _cdn(treffer: set[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) in treffer:
            return httpx.Response(200, headers={"content-type": "image/png"})
        return httpx.Response(404, headers={"content-type": "text/html"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_sucht_den_passenden_code_und_merkt_ihn_sich():
    ziel = S + "SV8PT5/SV8PT5_EN_117.png"
    gefragt = []

    def handler(request):
        gefragt.append(str(request.url))
        ok = str(request.url) in {ziel, S + "SV8PT5/SV8PT5_EN_128.png"}
        return httpx.Response(200 if ok else 404, headers={"content-type": "image/png" if ok else "text/html"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            a = await pokemon_com.find_image("sv08.5", "117", c)
            n = len(gefragt)
            b = await pokemon_com.find_image("sv08.5", "128", c)
            return a, b, n
    a, b, n = asyncio.run(run())
    assert a == ziel and b == S + "SV8PT5/SV8PT5_EN_128.png"
    assert len(gefragt) == n + 1                                   # zweite Karte: gleich der gemerkte Code


def test_kein_treffer_keine_url():
    async def run():
        async with _cdn(set()) as c:
            return await pokemon_com.find_image("base1", "4", c)
    assert asyncio.run(run()) is None


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.execute(delete(PokemonCard).where(PokemonCard.kartenname.like("pkcom-%")).execution_options(all_users=True))
    s.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id.like("pkcom-%")))
    s.commit()
    s.close()


def test_karte_ohne_tcgdex_bild_bekommt_pokemon_com(client, db, monkeypatch):
    url = S + "SWSH12/SWSH12_EN_65.png"

    async def fake_find(set_id, local_id, client=None):
        return url if (set_id, local_id) == ("swsh12", "065") else None

    monkeypatch.setattr(pokemon_com, "find_image", fake_find)
    tp = "https://tcgplayer-cdn.tcgplayer.com/product/1_in_1000x1000.jpg"
    db.add(TcgdexCatalog(card_id="pkcom-1", region="west", set_id="swsh12", local_id="065", name="Unown V",
                         image_url=tp, image_source="tcgplayer"))
    card = PokemonCard(kartenname="pkcom-unown", tcgdex_card_id="pkcom-1", karten_nr="065/195", besessen=True)
    db.add(card)
    db.commit()
    got = asyncio.run(image_fallback.fallback_image(db, card))
    db.commit()
    assert got == url and card.bild_karte_url == url
    row = db.get(TcgdexCatalog, "pkcom-1")
    assert (row.image_url, row.image_source) == (url, "pokemon.com")   # ersetzt das TCGplayer-Foto
    assert bild_quelle(card) == "pokemon"
    # schon ein Bild da → nichts anfassen
    assert asyncio.run(image_fallback.fallback_image(db, card)) is None


def test_ohne_treffer_bleibt_tcgplayer(client, db, monkeypatch):
    async def fake_find(set_id, local_id, client=None):
        return None

    monkeypatch.setattr(pokemon_com, "find_image", fake_find)
    tp = "https://tcgplayer-cdn.tcgplayer.com/product/2_in_1000x1000.jpg"
    db.add(TcgdexCatalog(card_id="pkcom-2", region="west", set_id="x", local_id="1", image_url=tp,
                         image_source="tcgplayer"))
    card = PokemonCard(kartenname="pkcom-x", tcgdex_card_id="pkcom-2", besessen=True)
    db.add(card)
    db.commit()
    assert asyncio.run(image_fallback.fallback_image(db, card)) == tp


def test_naechtlicher_katalog_schritt(client, db, monkeypatch):
    url = S + "SWSHP/SWSHP_EN_SWSH300.png"
    seen = []

    async def fake_many(items, conc=6):
        seen.extend(items)
        return {cid: url for cid, sid, lid in items if cid in ("pkcom-3", "pkcom-6")}

    monkeypatch.setattr(pokemon_com, "find_many", fake_many)
    db.add(TcgdexCatalog(card_id="pkcom-3", region="west", set_id="swshp", local_id="SWSH300", name="Promo"))
    tp = "https://tcgplayer-cdn.tcgplayer.com/product/3_in_1000x1000.jpg"
    db.add(TcgdexCatalog(card_id="pkcom-4", region="west", set_id="swshp", local_id="SWSH301", name="Promo 2"))
    db.add(TcgdexCatalog(card_id="pkcom-6", region="west", set_id="swshp", local_id="SWSH302", name="Promo 3",
                         image_url=tp, image_source="tcgplayer"))
    tp_card = PokemonCard(kartenname="pkcom-tp", tcgdex_card_id="pkcom-6", besessen=True, bild_karte_url=tp)
    db.add(tp_card)
    db.add(TcgdexCatalog(card_id="pkcom-5", region="west", set_id="sv01", local_id="1", name="Hat Bild",
                         image_url="https://assets.tcgdex.net/en/sv/sv01/1/high.webp"))
    card = PokemonCard(kartenname="pkcom-promo", tcgdex_card_id="pkcom-3", besessen=True)
    db.add(card)
    db.commit()
    res = asyncio.run(image_fallback.fill_catalog_from_pokemon_com(db, limit=5000))
    assert res["found"] >= 1
    ids = [i[0] for i in seen]
    assert "pkcom-3" in ids and "pkcom-4" in ids and "pkcom-5" not in ids
    assert ids.index("pkcom-3") < ids.index("pkcom-4")                 # eigene Karten zuerst
    db.expire_all()
    assert db.get(TcgdexCatalog, "pkcom-3").image_source == "pokemon.com"
    assert db.get(TcgdexCatalog, "pkcom-4").pokemon_com_checked is not None
    assert db.get(PokemonCard, card.id, execution_options={"all_users": True}).bild_karte_url == url
    # Karte mit dem TCGplayer-Foto des Katalogs wechselt auf das offizielle Bild
    assert db.get(PokemonCard, tp_card.id, execution_options={"all_users": True}).bild_karte_url == url
    # zweiter Lauf: frisch geprüfte Zeilen werden nicht gleich wieder gefragt
    seen.clear()
    asyncio.run(image_fallback.fill_catalog_from_pokemon_com(db, limit=5000))
    assert "pkcom-4" not in [i[0] for i in seen]
