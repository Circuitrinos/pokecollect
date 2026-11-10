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


def test_promo_nummern():
    from app.services.tcgdex import local_id_variants
    assert local_id_variants("swshp", "300") == ["300", "SWSH300"]
    assert local_id_variants("swshp", "5") == ["5", "SWSH005", "SWSH5", "005"]
    assert local_id_variants("svp", "1") == ["1", "001"]
    assert local_id_variants("swsh12", "TG05") == ["TG05"]
    assert pokemon_com.candidate_urls("swshp", "300")[0] == S + "SWSHP/SWSHP_EN_SWSH300.png"


def test_tcgdex_findet_promo_ohne_praefix(monkeypatch):
    from app.services import tcgdex
    from app.services.card_image_service import fetch_tcgdex_card
    from app.services.tcgdex import TcgdexCard
    asked = []

    async def fake(set_id, local_id, lang="en"):
        asked.append(local_id)
        return TcgdexCard.model_validate({"id": f"{set_id}-{local_id}", "localId": local_id}) \
            if local_id == "SWSH300" else None

    monkeypatch.setattr(tcgdex, "get_card_by_set", fake)
    tc = asyncio.run(fetch_tcgdex_card("swshp", "300", "EN"))
    assert tc is not None and tc.id == "swshp-SWSH300" and asked[0] == "300"


def test_umschalter_sucht_katalogbild(client, db, monkeypatch, png_bytes):
    """Wie im Screenshot: eigenes Foto, Set swshp, Nummer 300, noch kein Katalogbild."""
    from app.services import card_image_service, tcgdex
    from app.services.tcgdex import TcgdexCard
    from app.models.pokemon_set import PokemonSet

    async def fake_fetch(set_id, nr, sprache):
        if (set_id, nr) == ("pkcomp", "300"):
            return TcgdexCard.model_validate({"id": "pkcom-SWSH300", "localId": "SWSH300", "set": {"id": "pkcomp"}})
        return None

    url = S + "PKCOMP/PKCOMP_EN_SWSH300.png"

    async def fake_find(set_id, local_id, client=None):
        return url if (set_id, local_id) == ("pkcomp", "SWSH300") else None

    monkeypatch.setattr(card_image_service, "fetch_tcgdex_card", fake_fetch)
    monkeypatch.setattr(pokemon_com, "find_image", fake_find)
    if db.get(PokemonSet, "pkcomp") is None:
        db.add(PokemonSet(code="pkcomp", name="pkcom Promos", set_id="pkcomp"))
        db.commit()
    try:
        r = client.post("/api/v1/cards", json={"kartenname": "pkcom-unown", "besessen": True,
                                               "set_edition": "pkcom Promos (pkcomp)", "karten_nr": "300"})
        cid = r.json()["id"]
        up = client.post(f"/api/v1/cards/{cid}/image", files={"file": ("c.png", png_bytes, "image/png")})
        assert up.status_code == 200 and up.json()["bild_karte_pfad"]
        r = client.post(f"/api/v1/cards/{cid}/catalog-image")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["bild_karte_pfad"] is None and body["bild_scan_pfad"] and body["bild_karte_url"] == url
        assert body["tcgdex_card_id"] == "pkcom-SWSH300"           # dabei richtig verknüpft
        # ohne Fund: 404, Foto bleibt
        r2 = client.post("/api/v1/cards", json={"kartenname": "pkcom-none", "besessen": True, "karten_nr": "1"})
        cid2 = r2.json()["id"]
        client.post(f"/api/v1/cards/{cid2}/image", files={"file": ("c.png", png_bytes, "image/png")})
        r3 = client.post(f"/api/v1/cards/{cid2}/catalog-image")
        assert r3.status_code == 404
        assert client.get(f"/api/v1/cards/{cid2}").json()["bild_karte_pfad"]
    finally:
        db.execute(delete(PokemonSet).where(PokemonSet.code == "pkcomp"))
        db.commit()


def test_richtige_karte_verknuepfen(client, db, png_bytes):
    """Auswähler: Karte mit eigenem Foto auf eine Katalogkarte ohne TCGdex-Bild
    umstellen — Bild aus der offiziellen Datenbank, Foto bleibt erhalten."""
    url = S + "SWSHP/SWSHP_EN_SWSH300.png"
    db.add(TcgdexCatalog(card_id="pkcom-swshp-SWSH300", region="west", set_id="swshp", local_id="SWSH300",
                         name="Unown V", name_en="Unown V", rarity="Rare Holo V", enriched=True))
    db.commit()
    r = client.post("/api/v1/cards", json={"kartenname": "pkcom-falsch", "besessen": True, "sprache": "EN",
                                           "set_edition": "Silver Tempest (SIT)", "karten_nr": "065/195"})
    cid = r.json()["id"]
    client.post(f"/api/v1/cards/{cid}/image", files={"file": ("c.png", png_bytes, "image/png")})
    r = client.post(f"/api/v1/cards/{cid}/link-catalog", json={"card_id": "pkcom-swshp-SWSH300", "image_url": url})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["tcgdex_card_id"] == "pkcom-swshp-SWSH300" and b["karten_nr"] == "SWSH300"
    assert b["kartenname"] == "Unown V" and b["bild_karte_url"] == url
    assert b["bild_karte_pfad"] is None and b["bild_scan_pfad"]          # Foto → Scan-Foto
    db.expire_all()
    assert db.get(TcgdexCatalog, "pkcom-swshp-SWSH300").image_source == "pokemon.com"
    # fremde Bild-Adressen werden nicht übernommen, unbekannte Katalogkarte → 404
    r = client.post(f"/api/v1/cards/{cid}/link-catalog", json={"card_id": "pkcom-swshp-SWSH300",
                                                              "image_url": "https://evil.example/x.png"})
    assert r.json()["bild_karte_url"] == url
    assert client.post(f"/api/v1/cards/{cid}/link-catalog", json={"card_id": "gibtsnicht-1"}).status_code == 404
    # Katalog-Liste schlägt für Karten ohne TCGdex-Bild die offizielle Adresse vor
    db.add(TcgdexCatalog(card_id="pkcom-sv08-162", region="west", set_id="sv08", local_id="162", name="pkcomlist"))
    db.commit()
    item = next(i for i in client.get("/api/v1/catalog", params={"q": "pkcomlist"}).json()["items"]
                if i["card_id"] == "pkcom-sv08-162")
    assert item["image_url"] is None and item["image_fallback"] == S + "SV08/SV08_EN_162.png"
    client.delete(f"/api/v1/cards/{cid}")


def test_link_erkennen():
    p = pokemon_com.parse_link
    assert p("https://www.pokemon.com/us/pokemon-tcg/pokemon-cards/series/swsh12/65") == \
        {"code": "swsh12", "number": "65", "image": None}
    assert p("https://www.pokemon.com/us/pokemon-tcg/pokemon-cards/series/swshp/SWSH300/")["number"] == "SWSH300"
    # geteilter Text vom Handy: Titel + Link
    assert p("Unown V | Pokémon TCG https://www.pokemon.com/us/pokemon-tcg/pokemon-cards/series/sv8pt5/117/")["code"] == "sv8pt5"
    img = S + "SWSHP/SWSHP_EN_SWSH300.png"
    assert p(img) == {"code": "SWSHP", "number": "SWSH300", "image": img}
    assert p("https://evil.example/series/swsh12/65") is None
    assert p("https://www.pokemon.com/us/pokemon-tcg/pokemon-cards?cardName=unown+v") is None
    assert p("kein link") is None and p(None) is None
    assert pokemon_com.image_urls_for("swsh12", "65")[0] == S + "SWSH12/SWSH12_EN_65.png"


def test_set_code_zurueck_zu_tcgdex():
    c = pokemon_com.tcgdex_set_candidates
    assert "sv08.5" in c("SV8PT5") and "sv08" in c("SV08") and "sv08" in c("sv8")
    assert "swsh4.5" in c("SWSH45") and "swsh12.5" in c("SWSH12PT5")
    assert c("swshp")[0] == "swshp" and "sv10.5w" in c("RSV10PT5")


def test_link_von_pokemon_com_uebernehmen(client, db, monkeypatch, png_bytes):
    url = S + "SWSHP/SWSHP_EN_SWSH300.png"

    async def fake_image(link):
        return url if link["code"].lower() == "swshp" else None

    monkeypatch.setattr(pokemon_com, "image_for_link", fake_image)
    db.add(TcgdexCatalog(card_id="pkcom-lnk-SWSH300", region="west", set_id="swshp", local_id="SWSH300",
                         name="Unown V", name_en="Unown V", enriched=True))
    db.commit()
    r = client.post("/api/v1/cards", json={"kartenname": "pkcom-link", "besessen": True, "sprache": "EN"})
    cid = r.json()["id"]
    client.post(f"/api/v1/cards/{cid}/image", files={"file": ("c.png", png_bytes, "image/png")})
    try:
        r = client.post(f"/api/v1/cards/{cid}/link-pokemon-com",
                        json={"url": "Unown V https://www.pokemon.com/us/pokemon-tcg/pokemon-cards/series/swshp/SWSH300/"})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["bild_karte_url"] == url and b["bild_karte_pfad"] is None and b["bild_scan_pfad"]
        # catalog row pkcom-lnk-SWSH300 shares set+number with any real swshp row; either is a correct link
        assert b["karten_nr"] == "SWSH300" and b["kartenname"] == "Unown V"
        bad = client.post(f"/api/v1/cards/{cid}/link-pokemon-com", json={"url": "https://example.com/nothing"})
        assert bad.status_code == 422
        none = client.post(f"/api/v1/cards/{cid}/link-pokemon-com",
                           json={"url": "https://www.pokemon.com/us/pokemon-tcg/pokemon-cards/series/xx9/1"})
        assert none.status_code == 404
    finally:
        client.delete(f"/api/v1/cards/{cid}")
