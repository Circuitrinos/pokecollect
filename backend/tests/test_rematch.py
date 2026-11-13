"""Neu abgleichen (services/rematch.py): Druck finden, Daten aus dem Katalog ergänzen,
Admin-Werkzeug „Karten mit Lücken"."""
import httpx
import pytest
from sqlalchemy import delete

from app.config import settings
from app.database import SessionLocal
from app.models.card import PokemonCard
from app.models.deck import Deck, DeckSlot
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import decks as deck_svc
from app.services import foil_check, rematch

from tests.test_decks import eve  # noqa: F401 — Fixture eines zweiten Kontos

API = "/api/v1"
P = "trm"


def _row(cid, set_id, local_id, name, **kw):
    base = dict(card_id=cid, region="west", set_id=set_id, set_code=kw.pop("set_code", "TRA"), local_id=local_id,
                local_id_num=int(local_id) if local_id.isdigit() else None, name=name, name_en=name,
                category="Trainer", trainer_type="Item", rarity="Uncommon", regulation_mark="I",
                regulation_checked=True, effect_checked=True, enriched=True, legal_expanded=True,
                illustrator="Test Artist", variants_normal=True, variants_reverse=True, variants_holo=False,
                image_url=f"https://assets.tcgdex.net/en/me/{cid}/high.webp")
    base.update(kw)
    return TcgdexCatalog(**base)


@pytest.fixture()
def catalog(client):
    db = SessionLocal()
    db.add_all([
        PokemonSet(code="TRA", name="Testwald", name_en="Test Forest", set_id=f"{P}1", card_count_official=132),
        PokemonSet(code="TRB", name="Testberg", name_en="Test Mountain", set_id=f"{P}2", card_count_official=182),
        PokemonSet(code="TRP", name="Test Promos", name_en="Test Black Star Promos", set_id=f"{P}p"),
        _row(f"{P}1-117", f"{P}1", "117", "Forest of Testing"),
        _row(f"{P}2-117", f"{P}2", "117", "Forest of Testing", set_code="TRB", rarity="Common"),
        _row(f"{P}1-062", f"{P}1", "062", "Other Card", illustrator="Someone Else"),
        _row(f"{P}1-090", f"{P}1", "090", "Holo Only", variants_normal=False, variants_reverse=False, variants_holo=True),
        _row(f"{P}p-007", f"{P}p", "007", "Promo Thing", set_code="TRP", set_name="Test Black Star Promos", rarity="None"),
    ])
    db.commit()
    yield
    db.execute(delete(PokemonCard).where(PokemonCard.kartenname.like("Rm %") | PokemonCard.tcgdex_card_id.like(P + "%"))
               .execution_options(all_users=True))
    db.execute(delete(Deck).where(Deck.name.like("Test-Rm%")).execution_options(all_users=True))
    db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id.like(P + "%")))
    db.execute(delete(PokemonSet).where(PokemonSet.code.in_(["TRA", "TRB", "TRP"])))
    db.commit()
    db.close()


def _card(**kw) -> int:
    db = SessionLocal()
    c = PokemonCard(**{"kartenname": "Rm card", "englischer_name": "Forest of Testing", "sprache": "EN",
                       "besessen": True, **kw})
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    return cid


def _get(cid) -> PokemonCard:
    with SessionLocal() as db:
        return db.query(PokemonCard).filter(PokemonCard.id == cid).execution_options(all_users=True).one()


# ── Bausteine ────────────────────────────────────────────────────────────────

def test_number_change_ignores_spelling_but_not_the_total():
    assert rematch._number_change("117", "117/132") == "fill"
    assert rematch._number_change(None, "117/132") == "fill"
    assert rematch._number_change("7/146", "007/146") is None
    assert rematch._number_change("117/132", "117") is None          # Set ohne bekannte Kartenzahl
    assert rematch._number_change("117/132", "117/182") == "change"  # anderes Set, gleiche Nummer
    assert rematch._number_change("118/132", "117/132") == "change"
    assert rematch._number_change("TG05", "TG05") is None


def test_foil_suggestion_only_when_it_can_be_told():
    assert rematch.foil_suggestion(None, ["Holo"], None) == ("Holo", "only")
    assert rematch.foil_suggestion(None, ["Normal", "Reverse Holo"], True) == ("Reverse Holo", "photo")
    assert rematch.foil_suggestion(None, ["Normal", "Reverse Holo"], False) == ("Normal", "photo")
    assert rematch.foil_suggestion(None, ["Normal", "Holo", "Reverse Holo"], False) == (None, None)
    assert rematch.foil_suggestion("Normal", ["Normal", "Reverse Holo"], True) == (None, None)


def test_catalog_number_has_the_set_total_promos_do_not(client, catalog):
    from app.services.catalog import catalog_fields

    with SessionLocal() as db:
        assert catalog_fields(db, db.get(TcgdexCatalog, f"{P}1-117"), "EN")["karten_nr"] == "117/132"
        promo = catalog_fields(db, db.get(TcgdexCatalog, f"{P}p-007"), "EN")
        assert promo["karten_nr"] == "007" and promo["seltenheit"] == "Promo"


# ── Kartenseite ──────────────────────────────────────────────────────────────

def test_rematch_fills_the_gaps_of_a_linked_card(client, catalog):
    cid = _card(tcgdex_card_id=f"{P}1-117", set_id=f"{P}1", karten_nr="117",
                set_edition="Test Forest (TRA)", folierung=None,
                bild_karte_url="https://assets.tcgdex.net/en/me/keep/high.webp")
    p = client.get(f"{API}/cards/{cid}/rematch").json()
    assert p["found"]["tcgdex_card_id"] == f"{P}1-117" and p["how"] == "link" and p["sure"] and p["safe"]
    got = {c["field"]: (c["old"], c["new"], c["kind"]) for c in p["changes"]}
    assert got["karten_nr"] == ("117", "117/132", "fill")
    assert got["illustrator"] == (None, "Test Artist", "fill")
    assert got["seltenheit"][1] == "Uncommon"
    assert "set_edition" not in got and "bild" not in got
    assert p["foil"]["options"] == ["Normal", "Reverse Holo"] and p["foil"]["suggested"] is None
    assert any(a["tcgdex_card_id"] == f"{P}2-117" for a in p["alternatives"])   # andere Drucke zur Wahl

    r = client.post(f"{API}/cards/{cid}/rematch", json={"tcgdex_card_id": f"{P}1-117", "folierung": "Reverse Holo"})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["karten_nr"] == "117/132" and card["illustrator"] == "Test Artist" and card["folierung"] == "Reverse Holo"
    assert card["bild_karte_url"] == "https://assets.tcgdex.net/en/me/keep/high.webp"   # derselbe Druck: Bild bleibt
    assert client.get(f"{API}/cards/{cid}/rematch").json()["changes"] == []


def test_rematch_another_print_and_only_some_fields(client, catalog):
    cid = _card(tcgdex_card_id=f"{P}1-117", set_id=f"{P}1", karten_nr="117/132", illustrator="Test Artist",
                seltenheit="Uncommon", set_edition="Test Forest (TRA)")
    p = client.get(f"{API}/cards/{cid}/rematch", params={"print": f"{P}2-117"}).json()
    assert p["how"] == "picked" and not p["safe"]
    got = {c["field"]: (c["new"], c["kind"]) for c in p["changes"]}
    assert got["karten_nr"] == ("117/182", "change") and got["set_edition"] == ("Test Mountain (TRB)", "change")
    r = client.post(f"{API}/cards/{cid}/rematch", json={"tcgdex_card_id": f"{P}2-117", "fields": ["karten_nr"]})
    card = r.json()
    assert card["tcgdex_card_id"] == f"{P}2-117" and card["karten_nr"] == "117/182"
    assert card["set_edition"] == "Test Forest (TRA)"                   # abgewählt
    assert "assets.tcgdex.net/en/me/trm2-117" in card["bild_karte_url"]   # neuer Druck → sein Bild
    assert client.post(f"{API}/cards/{cid}/rematch", json={"tcgdex_card_id": "nope-1"}).status_code == 404
    assert client.post(f"{API}/cards/{cid}/rematch",
                       json={"tcgdex_card_id": f"{P}2-117", "folierung": "Shiny"}).status_code == 422


def test_unlinked_card_without_photo_found_by_set_and_number(client, catalog):
    cid = _card(karten_nr="117/132", set_edition="Test Forest (TRA)")
    p = client.get(f"{API}/cards/{cid}/rematch").json()
    assert p["found"]["tcgdex_card_id"] == f"{P}1-117" and p["how"] == "number" and p["sure"]
    assert p["photo"] == "no_photo"
    only = _card(englischer_name="Holo Only", kartenname="Rm holo")
    q = client.get(f"{API}/cards/{only}/rematch").json()
    assert q["found"]["tcgdex_card_id"] == f"{P}1-090" and q["how"] == "name" and not q["sure"]
    assert q["foil"] == {**q["foil"], "suggested": "Holo", "why": "only"}


@pytest.fixture()
def matcher(monkeypatch, tmp_path):
    """Bild-Abgleich als Attrappe: answers[Dateiname] = (Kandidaten, sicher)."""
    answers: dict = {}
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/foil":
            return httpx.Response(200, json={"model": True, "aligned": True, "hint": True, "score": 3.0})
        body = request.content.decode("latin-1")
        who = next(k for k in answers if k in body)
        cands, sure = answers[who][:2]
        stamp = answers[who][2] if len(answers[who]) > 2 else None
        return httpx.Response(200, json={"cards": [{"auto": sure, "stamp": stamp, "candidates": [
            {"card_id": c, "name": "x", "local_id": c.rsplit("-", 1)[1]} for c in cands]}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings, "matcher_url", "http://matcher.test")
    monkeypatch.setattr(settings, "images_dir", str(tmp_path / "images"))

    async def ref(card_id):
        return b"ref"

    monkeypatch.setattr(foil_check, "reference", ref)
    (tmp_path / "images").mkdir()

    def photo(name, cands, sure, stamp=None):
        (tmp_path / "images" / f"{name}.jpg").write_bytes(b"\xff\xd8 " + name.encode())
        answers[name] = (cands, sure, stamp)
        return f"images/{name}.jpg"
    return photo


def test_photo_decides_when_sure_and_keeps_a_same_name_link(client, catalog, matcher):
    # unsicher, aber der Kandidat ist nur ein anderer Druck derselben Karte → Verknüpfung bleibt
    a = _card(tcgdex_card_id=f"{P}2-117", set_id=f"{P}2", bild_scan_pfad=matcher("rm_a", [f"{P}1-117"], False))
    pa = client.get(f"{API}/cards/{a}/rematch").json()
    assert pa["found"]["tcgdex_card_id"] == f"{P}2-117" and pa["how"] == "link" and pa["photo"] == "used"
    assert any(x["photo"] for x in pa["alternatives"])
    # sicher → das Foto entscheidet
    b = _card(tcgdex_card_id=f"{P}2-117", set_id=f"{P}2", bild_scan_pfad=matcher("rm_b", [f"{P}1-117"], True))
    pb = client.get(f"{API}/cards/{b}/rematch").json()
    assert pb["found"]["tcgdex_card_id"] == f"{P}1-117" and pb["how"] == "photo" and pb["safe"]
    assert pb["foil"]["photo_hint"] is True and pb["foil"]["suggested"] == "Reverse Holo"
    # unsicher und eine ganz andere Karte → Vorschlag, aber nicht ohne Rückfrage
    c = _card(tcgdex_card_id=f"{P}1-117", set_id=f"{P}1", bild_scan_pfad=matcher("rm_c", [f"{P}1-062"], False))
    pc = client.get(f"{API}/cards/{c}/rematch").json()
    assert pc["found"]["tcgdex_card_id"] == f"{P}1-062" and not pc["sure"] and not pc["safe"]
    # die aufgedruckte Nummer wählt unter unsicheren Kandidaten
    d = _card(karten_nr="117/182", bild_scan_pfad=matcher("rm_d", [f"{P}1-117", f"{P}2-117", f"{P}1-062"], False))
    pd = client.get(f"{API}/cards/{d}/rematch").json()
    assert pd["found"]["tcgdex_card_id"] == f"{P}2-117" and pd["how"] == "photo" and pd["sure"]


def test_finder_and_deck_print_fill_the_full_number(client, catalog):
    cid = _card()
    card = client.post(f"{API}/cards/{cid}/link-catalog", json={"card_id": f"{P}1-117"}).json()
    assert card["karten_nr"] == "117/132" and card["illustrator"] == "Test Artist"
    # Deck-Seite: Karte ohne Druck bekommt beim Wählen des Drucks alles Fehlende
    other = _card(karten_nr=None)
    with SessionLocal() as db:
        c = db.query(PokemonCard).filter(PokemonCard.id == other).execution_options(all_users=True).one()
        n = rematch.fill_from_row(db, c, db.get(TcgdexCatalog, f"{P}1-117"))
        db.commit()
    got = _get(other)
    assert n >= 3 and got.tcgdex_card_id == f"{P}1-117" and got.karten_nr == "117/132"
    assert got.illustrator == "Test Artist" and got.seltenheit == "Uncommon" and got.bild_karte_url


# ── Admin ────────────────────────────────────────────────────────────────────

def test_admin_finds_cards_with_gaps_of_all_accounts(client, catalog, eve):  # noqa: F811
    full = _card(tcgdex_card_id=f"{P}1-062", set_id=f"{P}1", karten_nr="062/132", illustrator="Someone Else",
                 seltenheit="Uncommon", set_edition="Test Forest (TRA)", englischer_name="Other Card",
                 kartenname="Other Card", folierung="Normal", bild_karte_url="https://assets.tcgdex.net/x.webp")
    thin = _card(tcgdex_card_id=f"{P}1-117", set_id=f"{P}1", karten_nr="117", folierung=None)
    loose = _card(kartenname="Rm loose", englischer_name=None)
    eves = eve.post(f"{API}/cards", json={"kartenname": "Rm eve", "besessen": True}).json()["id"]

    r = client.get(f"{API}/card-gaps")
    assert r.status_code == 200, r.text
    items = {i["card_id"]: i for i in r.json()["items"]}
    assert full not in items, items.get(full)
    assert {"number", "illustrator", "rarity", "set", "foiling", "image"} <= set(items[thin]["gaps"])
    assert "print" in items[loose]["gaps"] and "english_name" in items[loose]["gaps"]
    others = {i["card_id"]: i for i in client.get(f"{API}/card-gaps", params={"owner": "others"}).json()["items"]}
    assert eves in others and not others[eves]["own"] and others[eves]["username"] == "eve-deck" and thin not in others
    assert r.json()["counts"]["print"] >= 2
    only = client.get(f"{API}/card-gaps", params={"gap": "illustrator"}).json()["items"]
    assert thin in {i["card_id"] for i in only} and loose not in {i["card_id"] for i in only}
    assert eves not in {i["card_id"] for i in client.get(f"{API}/card-gaps", params={"owner": "mine"}).json()["items"]}
    # das Admin-Werkzeug gleicht auch Karten anderer Konten ab
    assert client.get(f"{API}/cards/{eves}/rematch").status_code == 200
    assert eve.get(f"{API}/card-gaps").status_code == 403
    assert eve.get(f"{API}/cards/{thin}/rematch").status_code == 404


def test_deck_print_choice_links_cards_with_full_data(client, catalog):
    deck = client.post(f"{API}/decks", json={"name": "Test-Rm deck", "text": "Trainer: 1\n1 Forest of Testing TRA 117\n"}).json()["deck"]
    cid = _card(kartenname="Rm deck card", deck_id=deck["id"])
    with SessionLocal() as db:
        d = db.get(Deck, deck["id"])
        slot = db.query(DeckSlot).filter(DeckSlot.deck_id == d.id).one()
        deck_svc.set_print(db, d, slot, db.get(TcgdexCatalog, f"{P}1-117"))
        db.commit()
    got = _get(cid)
    assert got.tcgdex_card_id == f"{P}1-117" and got.karten_nr == "117/132" and got.illustrator == "Test Artist"
    with SessionLocal() as db:
        db.execute(delete(DeckSlot).where(DeckSlot.deck_id == deck["id"]))
        db.commit()


def test_card_number_from_two_fields_is_stored_together(client):
    from app.schemas._validators import clean_card_nr

    assert clean_card_nr(" 117 / 132 ") == "117/132"
    assert clean_card_nr("117/") == "117"
    assert clean_card_nr("/132") is None and clean_card_nr("") is None
    assert clean_card_nr("TG05/TG30") == "TG05/TG30"
    card = client.post(f"{API}/cards", json={"kartenname": "Rm split", "karten_nr": "117 /132"}).json()
    assert card["karten_nr"] == "117/132"
    assert client.put(f"{API}/cards/{card['id']}", json={"karten_nr": "/132"}).json()["karten_nr"] is None


def test_copies_share_the_scan_photo_and_placeholders_are_no_gaps(client, catalog, monkeypatch, tmp_path):
    from datetime import datetime, timedelta

    from app.services import card_photos

    monkeypatch.setattr(settings, "images_dir", str(tmp_path / "images"))
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "first.jpg").write_bytes(b"\xff\xd8 photo")
    now = datetime.utcnow()
    base = dict(tcgdex_card_id=f"{P}1-117", set_id=f"{P}1", karten_nr="117/132", illustrator="Test Artist",
                seltenheit="Uncommon", set_edition="Test Forest (TRA)", folierung="Normal",
                bild_karte_url="https://assets.tcgdex.net/x.webp", hinzugefuegt_am=now)
    first = _card(kartenname="Forest of Testing", bild_scan_pfad="images/first.jpg", bild_original_pfad="images/orig.jpg", **base)
    second = _card(kartenname="Forest of Testing", **base)
    later = _card(kartenname="Forest of Testing", **{**base, "hinzugefuegt_am": now + timedelta(days=3)})   # andere Speicherung
    placeholder = _card(kartenname="Rm placeholder", englischer_name="Dreepy", besessen=False, pokedex_nr=885, sprache="DE")

    items = {i["card_id"]: i for i in client.get(f"{API}/card-gaps", params={"limit": 2000}).json()["items"]}
    assert items[second]["gaps"] == ["photo"] and first not in items and later not in items
    assert placeholder not in items                                   # Pokédex-Platzhalter sind keine Lücke
    assert client.post(f"{API}/card-gaps/copy-photos").json()["shared"] >= 1
    got = _get(second)
    assert got.bild_scan_pfad == f"images/card_{second}_scan.jpg" and got.bild_original_pfad is None
    assert (tmp_path / "images" / f"card_{second}_scan.jpg").read_bytes() == b"\xff\xd8 photo"
    assert _get(later).bild_scan_pfad is None

    # beim Speichern: die Scan-Seite gibt das Foto gleich weiter
    third = _card(kartenname="Forest of Testing", **base)
    assert client.post(f"{API}/cards/{first}/share-photo", json={"card_ids": [third, second]}).json() == {"shared": 1}
    assert _get(third).bild_scan_pfad == f"images/card_{third}_scan.jpg"
    with SessionLocal() as s:
        assert card_photos.photoless_copies(s).get(third) is None


def test_new_photo_checks_the_play_stamp_and_its_own_photo_first(client, catalog, matcher):
    """Nach einem neuen Foto: der Stempel-Befund kommt mit, und das angezeigte Foto zählt
    vor einem älteren Scan-Foto."""
    stamp = {"score": 0.81, "ok": True, "sure": True, "quad": [[0.6, 0.7], [0.7, 0.7], [0.7, 0.8], [0.6, 0.8]]}
    cid = _card(tcgdex_card_id=f"{P}1-117", set_id=f"{P}1", karten_nr="117/132", illustrator="Test Artist",
                seltenheit="Uncommon", set_edition="Test Forest (TRA)", kartenname="Forest of Testing", folierung="Normal",
                bild_scan_pfad=matcher("rm_old_scan", [f"{P}1-062"], True),
                bild_karte_pfad=matcher("rm_new_photo", [f"{P}1-117"], True, stamp))
    p = client.get(f"{API}/cards/{cid}/rematch").json()
    assert p["found"]["tcgdex_card_id"] == f"{P}1-117" and p["found"]["current"]      # das neue Foto, nicht das alte
    assert p["stamp"] == {"current": False, "ok": True, "sure": True, "score": 0.81}
    card = client.post(f"{API}/cards/{cid}/rematch", json={"tcgdex_card_id": f"{P}1-117", "fields": [],
                                                          "prize_pack": True}).json()
    assert card["prize_pack"] is True
    plain = _card(kartenname="Rm plain", tcgdex_card_id=f"{P}1-117", set_id=f"{P}1")
    assert client.get(f"{API}/cards/{plain}/rematch").json()["stamp"] is None        # ohne Foto nicht geprüft
