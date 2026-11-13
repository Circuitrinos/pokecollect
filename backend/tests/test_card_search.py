"""Kartensuche wie bei Limitless TCG (services/card_search.py), Sammlungs-/Deck-Filter,
Sortierung nach Sammlung, Sammlungen je Karte in der Liste."""
import pytest
from sqlalchemy import delete

from app.database import SessionLocal
from app.models.card import PokemonCard
from app.models.collection import Collection
from app.models.deck import Deck
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import card_search

API = "/api/v1"
P = "tqs"


def _row(cid, **kw):
    base = dict(card_id=cid, region="west", set_id=f"{P}1", set_code="TQS", local_id=cid.rsplit("-", 1)[1],
                name="x", name_en="x", category="Pokemon", regulation_checked=True, effect_checked=True, enriched=True)
    base.update(kw)
    return TcgdexCatalog(**base)


@pytest.fixture()
def cards(client):
    db = SessionLocal()
    db.add_all([
        PokemonSet(code="TQS", name="Testquelle", name_en="Test Source", set_id=f"{P}1"),
        _row(f"{P}1-001", name_en="Qsmon ex", stage="Basic", legal_expanded=True),
        _row(f"{P}1-002", name_en="Qsmon", stage="Stage1"),
        _row(f"{P}1-050", name_en="Qs Boss", category="Trainer", trainer_type="Supporter"),
        _row(f"{P}1-090", name_en="Qs Energy", category="Energy"),
    ])
    db.commit()
    db.close()
    specs = [
        ("Qsmon ex", f"{P}1-001", dict(karten_nr="001/132", illustrator="Asako Ito", seltenheit="Double Rare",
                                       folierung="Holo", regulation_mark="H", sprache="EN", pokedex_nr=25, wert_eur=12.5)),
        ("Qsmon", f"{P}1-002", dict(karten_nr="002/132", illustrator="Mitsuhiro Arita", seltenheit="Common",
                                    folierung="Reverse Holo", regulation_mark="G", sprache="DE", pokedex_nr=26, wert_eur=0.5,
                                    erste_edition=True)),
        ("Qs Boss", f"{P}1-050", dict(karten_nr="050/132", seltenheit="Special Illustration Rare", folierung="Normal",
                                      regulation_mark="I", sprache="EN", wert_eur=40, prize_pack=True)),
        ("Qs Energy", f"{P}1-090", dict(karten_nr="090/132", folierung="Normal", sprache="JP")),
    ]
    ids = {}
    for name, cid, extra in specs:
        r = client.post(f"{API}/cards", json={"kartenname": name, "englischer_name": name, "besessen": True,
                                              "set_edition": "Test Source (TQS)", **{k: v for k, v in extra.items()
                                                                                    if k not in ("regulation_mark", "illustrator")}})
        assert r.status_code in (200, 201), r.text
        ids[name] = r.json()["id"]
        db = SessionLocal()
        db.query(PokemonCard).filter(PokemonCard.id == ids[name]).execution_options(all_users=True).update(
            {"tcgdex_card_id": cid, "set_id": f"{P}1", "regulation_mark": extra.get("regulation_mark"),
             "illustrator": extra.get("illustrator"),
             "legal_mark": extra.get("regulation_mark") or "-"})
        db.commit()
        db.close()
    binder = client.post(f"{API}/collections", json={"name": "Qs Binder A"}).json()["id"]
    other = client.post(f"{API}/collections", json={"name": "Qs Album B"}).json()["id"]
    client.post(f"{API}/collections/{binder}/cards", json={"card_id": ids["Qsmon ex"]})
    client.post(f"{API}/collections/{binder}/cards", json={"card_id": ids["Qsmon"]})
    client.post(f"{API}/collections/{other}/cards", json={"card_id": ids["Qs Boss"]})
    deck = client.post(f"{API}/decks", json={"name": "Test-Qs deck"}).json()["deck"]["id"]
    db = SessionLocal()
    db.query(PokemonCard).filter(PokemonCard.id == ids["Qs Boss"]).execution_options(all_users=True).update({"deck_id": deck})
    db.commit()
    db.close()
    yield {**ids, "binder": binder, "other": other, "deck": deck}
    db = SessionLocal()
    db.execute(delete(PokemonCard).where(PokemonCard.id.in_([ids[k] for k in ids])).execution_options(all_users=True))
    db.execute(delete(Collection).where(Collection.name.like("Qs %")).execution_options(all_users=True))
    db.execute(delete(Deck).where(Deck.name.like("Test-Qs%")).execution_options(all_users=True))
    db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id.like(P + "%")))
    db.execute(delete(PokemonSet).where(PokemonSet.code == "TQS"))
    db.commit()
    db.close()


def _names(client, search, **params):
    r = client.get(f"{API}/cards", params={"search": search, "limit": 100, **params})
    assert r.status_code == 200, r.text
    return {c["kartenname"] for c in r.json()["items"]}, r.json()


# ── Parser ───────────────────────────────────────────────────────────────────

def test_parse_like_limitless():
    p = card_search.parse('t:pkmn -is:v,gx,ex a:"asako ito" !set:tqs n>=10 is:ex+holo hp<=90 foo:bar o:collection')
    terms = {t.raw: t for t in p.terms}
    assert terms["-is:v,gx,ex"].neg and terms["-is:v,gx,ex"].values == [["v"], ["gx"], ["ex"]]
    assert terms['a:"asako ito"'].values == [["asako ito"]]
    assert terms["!set:tqs"].exact
    assert terms["n>=10"].op == ">="
    assert terms["is:ex+holo"].values == [["ex", "holo"]]
    assert {w["term"]: w["reason"] for w in p.warnings} == {"hp<=90": "unsupported", "foo:bar": "unknown"}
    assert p.sort == "collection"
    q = card_search.parse("“boss's orders” -pikachu r!=c")
    assert [(t.key, t.values, t.neg) for t in q.terms] == [(None, [["boss's orders"]], False), (None, [["pikachu"]], True),
                                                          ("rarity", [["c"]], True)]
    assert card_search.parse("r>c").warnings == [{"term": "r>c", "reason": "value"}]


# ── Liste ────────────────────────────────────────────────────────────────────

def test_search_fields(client, cards):
    assert _names(client, "qs set:tqs n:1")[0] == {"Qsmon ex"}
    assert _names(client, "qs n>40")[0] == {"Qs Boss", "Qs Energy"}
    assert _names(client, "qs a:asako_ito")[0] == {"Qsmon ex"}
    assert _names(client, "qs -a:asako_ito")[0] == {"Qsmon", "Qs Boss", "Qs Energy"}     # ohne Illustrator zählt mit
    assert _names(client, "qs r:sir,c")[0] == {"Qs Boss", "Qsmon"}
    assert _names(client, "qs r:rr")[0] == {"Qsmon ex"}
    assert _names(client, "qs t:supporter")[0] == {"Qs Boss"}
    assert _names(client, "qs t:pkmn -is:ex")[0] == {"Qsmon"}
    assert _names(client, "qs t:energy")[0] == {"Qs Energy"}
    assert _names(client, "qs s:stage1")[0] == {"Qsmon"}
    assert _names(client, "qs is:holo,rh")[0] == {"Qsmon ex", "Qsmon"}
    assert _names(client, "qs is:ex+holo")[0] == {"Qsmon ex"}
    assert _names(client, "qs is:1st")[0] == {"Qsmon"}
    assert _names(client, "qs is:stamp")[0] == {"Qs Boss"}
    assert _names(client, "qs reg>=h")[0] == {"Qsmon ex", "Qs Boss"}
    assert _names(client, "qs lang:de,jp")[0] == {"Qsmon", "Qs Energy"}
    assert _names(client, "qs dex>=25 dex<=25")[0] == {"Qsmon ex"}
    assert _names(client, "qs price>10")[0] == {"Qsmon ex", "Qs Boss"}
    assert _names(client, "qs f:expanded")[0] == {"Qsmon ex"}
    assert _names(client, '"qsmon ex"')[0] == {"Qsmon ex"}
    assert _names(client, "!qsmon")[0] == {"Qsmon"}
    assert _names(client, "TQS 50")[0] == {"Qs Boss"}                                 # Kurzcode wie bisher


def test_search_collections_and_decks(client, cards):
    assert _names(client, "qs in:none")[0] == {"Qs Energy"}
    assert _names(client, "qs -in:any")[0] == {"Qs Energy"}
    assert _names(client, "qs in:binder")[0] == {"Qsmon ex", "Qsmon"}
    assert _names(client, "qs in:deck")[0] == {"Qs Boss"}
    assert _names(client, "qs deck:none")[0] == {"Qsmon ex", "Qsmon", "Qs Energy"}
    assert _names(client, "qs deck:qs_deck")[0] == {"Qs Boss"}
    assert _names(client, "qs", collection="none")[0] == {"Qs Energy"}
    assert _names(client, "qs", collection="any")[0] == {"Qsmon ex", "Qsmon", "Qs Boss"}
    assert _names(client, "qs", collection=str(cards["other"]))[0] == {"Qs Boss"}
    assert _names(client, "qs", deck="in")[0] == {"Qs Boss"}
    assert _names(client, "qs", deck="none")[0] == {"Qsmon ex", "Qsmon", "Qs Energy"}


def test_warnings_sort_and_collections_in_the_list(client, cards):
    _, body = _names(client, "qs hp>90 t:g n>abc zz:1")
    assert {w["term"]: w["reason"] for w in body["search_warnings"]} == {
        "hp>90": "unsupported", "t:g": "types", "n>abc": "value", "zz:1": "unknown"}
    assert len(body["items"]) == 4                     # nicht Verstandenes schränkt nicht ein
    names, body = _names(client, "qs o:collection")
    order = [c["kartenname"] for c in body["items"]]
    assert body["search_sort"] == "collection"
    assert order[0] == "Qs Boss" and order[-1] == "Qs Energy"          # „Qs Album B" vor „Qs Binder A", ohne Sammlung am Ende
    by = {c["kartenname"]: c for c in body["items"]}
    assert by["Qsmon ex"]["sammlungen"] == [{"id": cards["binder"], "name": "Qs Binder A"}]
    assert by["Qs Energy"]["sammlungen"] == []
    assert by["Qs Boss"]["deck_name"] == "Test-Qs deck"
    rev = [c["kartenname"] for c in client.get(f"{API}/cards", params={"search": "qs", "sort": "collection", "reverse": True,
                                                                     "limit": 100}).json()["items"]]
    assert rev[-1] == "Qs Energy" and rev[0] in ("Qsmon ex", "Qsmon")    # umgekehrt; ohne Sammlung bleibt am Ende


def test_deck_line_picker_shows_the_collections(client, cards):
    deck = client.post(f"{API}/decks", json={"name": "Test-Qs picker", "text": "Pokémon: 1\n1 Qsmon ex TQS 1\n"}).json()["deck"]
    slot = next(s for sec in ("pokemon", "trainer", "energy") for s in deck["slots"] if s["section"] == sec)
    got = client.get(f"{API}/decks/{deck['id']}/slots/{slot['id']}/candidates").json()
    by = {c["card"]["kartenname"]: c["card"] for c in got}
    assert by["Qsmon ex"]["sammlungen"] == [{"id": cards["binder"], "name": "Qs Binder A"}]
    client.delete(f"{API}/decks/{deck['id']}")
