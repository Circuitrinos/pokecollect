"""Decks: PTCGL-Import/Export, Legalität, Auffüllen, ein Deck je Karte, Scan ins Deck, Mehrbenutzer."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth_user import issue_token
from app.database import SessionLocal
from app.main import app
from app.models.card import PokemonCard
from app.models.deck import Deck
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog
from app.models.user import User
from app.services import decks as svc

API = "/api/v1"
P = "tdeck"          # Set-IDs der Test-Katalogzeilen: tdeck1, tdeck2, …

EXAMPLE = """Pokémon: 3
4 Testmon ex TDA 62
2 Testmon ex TDB 11
3 Radiant Testmon TDA 5

Trainer: 4
4 Boss's Orders TDA 114
1 Boss's Orders TDB 90
1 Prime Spec TDA 120
1 Other Spec TDB 121

Energy: 2
10 Basic {P} Energy TDE 5
34 Unknown Thing ZZZ 999

Total Cards: 60
"""


def _row(cid, set_id, local_id, name, category, *, mark="I", stage=None, trainer_type=None,
         rarity="Common", energy_type=None, set_code=None):
    return TcgdexCatalog(card_id=cid, region="west", set_id=set_id, set_code=set_code, local_id=local_id,
                         name=name, name_en=name, category=category, regulation_mark=mark, stage=stage,
                         trainer_type=trainer_type, rarity=rarity, energy_type=energy_type,
                         regulation_checked=True, effect_checked=True, enriched=True, legal_expanded=True,
                         image_url=f"https://assets.tcgdex.net/en/sv/{cid}/high.webp")


@pytest.fixture()
def catalog(client):
    db = SessionLocal()
    db.add_all([
        PokemonSet(code="TDA", name="Testset A", set_id=f"{P}1"),
        PokemonSet(code="TDB", name="Testset B", set_id=f"{P}2"),
        PokemonSet(code="TDE", name="Testenergie", set_id=f"{P}e"),
        _row(f"{P}1-062", f"{P}1", "062", "Testmon ex", "Pokemon", stage="Basic"),
        _row(f"{P}1-150", f"{P}1", "150", "Testmon ex", "Pokemon", stage="Basic", rarity="Special Illustration Rare"),
        _row(f"{P}2-011", f"{P}2", "011", "Testmon ex", "Pokemon", stage="Basic", mark="G"),
        _row(f"{P}1-005", f"{P}1", "005", "Radiant Testmon", "Pokemon", stage="Basic"),
        _row(f"{P}1-114", f"{P}1", "114", "Boss's Orders", "Trainer", trainer_type="Supporter"),
        _row(f"{P}2-090", f"{P}2", "090", "Boss's Orders (Ghetsis)", "Trainer", trainer_type="Supporter"),
        _row(f"{P}1-120", f"{P}1", "120", "Prime Spec", "Trainer", trainer_type="Item", rarity="ACE SPEC Rare"),
        _row(f"{P}2-121", f"{P}2", "121", "Other Spec", "Trainer", trainer_type="Item", rarity="ACE SPEC Rare"),
        _row(f"{P}e-005", f"{P}e", "005", "Psychic Energy", "Energy", mark=None, energy_type="Basic"),
        # wie TCGdex' SVE-Energien: „Normal" bzw. gar kein energyType, kein Zeichen
        _row(f"{P}e-009", f"{P}e", "009", "Grass Energy", "Energy", mark=None, energy_type="Normal"),
        _row(f"{P}e-011", f"{P}e", "011", "Water Energy", "Energy", mark=None, energy_type=None),
        # „Normal" steht bei TCGdex auch an Spezial-Energien
        _row(f"{P}e-191", f"{P}e", "191", "Luminous Energy", "Energy", mark="G", energy_type="Normal"),
        _row(f"{P}2-030", f"{P}2", "030", "Stage Testmon", "Pokemon", stage="Stage1"),
    ])
    db.commit()
    yield
    db.execute(delete(PokemonCard).where(PokemonCard.tcgdex_card_id.like(P + "%")).execution_options(all_users=True))
    db.execute(delete(PokemonCard).where(PokemonCard.kartenname.like("Deckscan%")).execution_options(all_users=True))
    db.execute(delete(Deck).where(Deck.name.like("Test-%")).execution_options(all_users=True))
    db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id.like(P + "%")))
    db.execute(delete(PokemonSet).where(PokemonSet.code.in_(["TDA", "TDB", "TDE"])))
    db.commit()
    db.close()


def _owned(client, cid, name, value=None, **extra):
    body = {"kartenname": name, "englischer_name": name, "besessen": True, **extra}
    if value is not None:
        body["wert_eur"] = value
    card = client.post(f"{API}/cards", json=body).json()
    db = SessionLocal()
    db.query(PokemonCard).filter(PokemonCard.id == card["id"]).execution_options(all_users=True) \
        .update({"tcgdex_card_id": cid, "set_id": cid.rsplit("-", 1)[0]})
    db.commit()
    db.close()
    return card["id"]


def _issues(deck):
    return {i["code"]: i for i in deck["legality"]["issues"]}


# ── Text ─────────────────────────────────────────────────────────────────────

def test_parse_ptcgl_sections_and_lines():
    p = svc.parse_ptcgl(EXAMPLE + "\nsome junk line\n* 2 Old Format Card ABC 7\n")
    assert len(p.lines) == 10
    assert p.lines[0].count == 4 and p.lines[0].name == "Testmon ex" and p.lines[0].set_code == "TDA"
    assert p.lines[0].number == "62" and p.lines[0].section == "pokemon"
    assert p.lines[7].name == "Basic {P} Energy" and p.lines[7].section == "energy"
    assert p.unreadable == ["some junk line"]
    assert p.lines[-1].name == "Old Format Card" and p.lines[-1].count == 2


def test_names_follow_the_rules():
    assert svc.rule_name("Boss's Orders (Ghetsis)") == svc.rule_name("Boss’s Orders") == "boss's orders"
    assert svc.basic_energy_type("Basic {P} Energy") == "psychic"
    assert svc.basic_energy_type("Psychic Energy") == "psychic"
    assert svc.basic_energy_type("Telepathic {P} Energy") is None
    assert svc.ptcgl_name("Basic Psychic Energy") == "Basic {P} Energy"


def test_expanded_from_series_when_tcgdex_is_silent():
    row = lambda sid, flag=None: TcgdexCatalog(card_id=f"{sid}-1", set_id=sid, legal_expanded=flag)  # noqa: E731
    assert svc._expanded_ok(row("sv03.5")) and svc._expanded_ok(row("swshp")) and svc._expanded_ok(row("bw1"))
    assert svc._expanded_ok(row("me01")) and svc._expanded_ok(row("smp"))
    assert not svc._expanded_ok(row("dp1")) and not svc._expanded_ok(row("base1")) and not svc._expanded_ok(row("swshx"))
    assert svc._expanded_ok(row("dp1", True))


# ── Import, Legalität, Export ────────────────────────────────────────────────

def test_import_matches_and_reports_problems(client, catalog):
    r = client.post(f"{API}/decks", json={"name": "Test-Import", "text": EXAMPLE})
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["lines"] == 9 and res["matched"] == 8 and res["unmatched"] == 1
    assert res["not_found"] == ["34 Unknown Thing ZZZ 999"]
    deck = res["deck"]
    slots = {s["name"]: s for s in deck["slots"]}
    by_id = {s["tcgdex_card_id"]: s for s in deck["slots"]}
    assert by_id[f"{P}1-062"]["count"] == 4 and by_id[f"{P}2-011"]["count"] == 2
    assert by_id[f"{P}1-062"]["match"] == "set" and by_id[f"{P}1-062"]["image_url"].endswith("/high.webp")
    assert slots["Basic {P} Energy"]["tcgdex_card_id"] == f"{P}e-005"
    assert slots["Basic {P} Energy"]["status"] == "basic"
    assert deck["total"] == 60 and deck["legality"]["sections"] == {"pokemon": 9, "trainer": 7, "energy": 44}
    issues = _issues(deck)
    # 4 + 2 „Testmon ex", 4 + 1 „Boss's Orders" (Klammerzusatz zählt nicht)
    assert issues["too_many_copies"]["level"] == "error"
    names = {i["name"] for i in deck["legality"]["issues"] if i["code"] == "too_many_copies"}
    assert names == {"Testmon ex", "Boss's Orders", "Unknown Thing"}
    assert issues["ace_spec"]["count"] == 2
    assert issues["radiant"]["count"] == 3
    # Testmon ex aus Set B trägt G → nicht Standard (Mindestzeichen H)
    assert issues["not_in_format"]["count"] == 2
    assert by_id[f"{P}1-062"]["status"] == "ok" and by_id[f"{P}2-011"]["status"] == "not_legal"
    assert by_id[f"{P}2-011"]["legal_mark"] == "G" and deck["legality"]["status"] == "illegal"
    assert issues["unmatched"]["level"] == "warning"
    assert "deck_size" not in issues and "no_basic" not in issues


def test_export_roundtrip_and_formats(client, catalog):
    deck = client.post(f"{API}/decks", json={"name": "Test-Export", "text": EXAMPLE}).json()["deck"]
    text = client.get(f"{API}/decks/{deck['id']}/export").text
    assert text.startswith("Pokémon: 3\n4 Testmon ex TDA 62\n2 Testmon ex TDB 11\n3 Radiant Testmon TDA 5\n\nTrainer: 4\n")
    assert "\n\nEnergy: 2\n10 Basic {P} Energy TDE 5\n34 Unknown Thing ZZZ 999\n\nTotal Cards: 60\n" in text
    again = svc.parse_ptcgl(text)
    assert [(x.count, x.name, x.set_code, x.number) for x in again.lines] == \
        [(x.count, x.name, x.set_code, x.number) for x in svc.parse_ptcgl(EXAMPLE).lines]
    # Expanded: Set B (Zeichen G) ist dort erlaubt; unlimited prüft keine Drucke
    d = client.put(f"{API}/decks/{deck['id']}", json={"format": "expanded"}).json()
    assert "not_in_format" not in _issues(d)
    d = client.put(f"{API}/decks/{deck['id']}", json={"format": "unlimited"}).json()
    assert "not_in_format" not in _issues(d) and "too_many_copies" in _issues(d)


def test_legal_deck_and_basic_rule(client, catalog):
    text = "Pokémon: 1\n4 Testmon ex TDA 62\n\nTrainer: 1\n4 Boss's Orders TDA 114\n\nEnergy: 1\n52 Basic {P} Energy TDE 5\n"
    deck = client.post(f"{API}/decks", json={"name": "Test-Legal", "text": text}).json()["deck"]
    assert deck["legality"]["status"] == "legal" and deck["legality"]["issues"] == []
    # nur ein Stufe-1-Pokémon → kein Basis-Pokémon
    text2 = "Pokémon: 1\n4 Stage Testmon TDB 30\n\nEnergy: 1\n56 Basic {P} Energy TDE 5\n"
    deck2 = client.post(f"{API}/decks", json={"name": "Test-NoBasic", "text": text2, "format": "unlimited"}).json()["deck"]
    assert _issues(deck2)["no_basic"]["level"] == "error"
    # Stufe unbekannt → nur ein Hinweis
    db = SessionLocal()
    db.get(TcgdexCatalog, f"{P}2-030").stage = None
    db.commit()
    db.close()
    deck2 = client.get(f"{API}/decks/{deck2['id']}").json()
    assert _issues(deck2)["basic_unknown"]["level"] == "warning" and deck2["legality"]["need_data"] is True


def test_basic_energy_by_name_when_tcgdex_says_normal_or_nothing(client, catalog):
    """SVE-Basis-Energien (energyType „Normal" oder leer, ohne Zeichen) sind überall legal und ohne
    Kopienlimit; eine Spezial-Energie mit „Normal" bleibt Spezial-Energie."""
    text = ("Pokémon: 1\n4 Testmon ex TDA 62\n\nTrainer: 1\n4 Boss's Orders TDA 114\n\n"
            "Energy: 2\n30 Grass Energy TDE 9\n22 Water Energy TDE 11\n")
    deck = client.post(f"{API}/decks", json={"name": "Test-SVE", "text": text}).json()["deck"]
    assert deck["legality"]["status"] == "legal", deck["legality"]
    energy = [s for s in deck["slots"] if s["section"] == "energy"]
    assert {s["status"] for s in energy} == {"basic"}
    text2 = text.replace("22 Water Energy TDE 11", "17 Water Energy TDE 11\n5 Luminous Energy TDE 191")
    deck2 = client.post(f"{API}/decks", json={"name": "Test-Luminous", "text": text2}).json()["deck"]
    issues = _issues(deck2)
    assert "too_many_copies" in issues and "not_in_format" in issues
    lum = next(s for s in deck2["slots"] if s["name"] == "Luminous Energy")
    assert lum["status"] == "not_legal"


def test_slots_add_change_remove(client, catalog):
    deck = client.post(f"{API}/decks", json={"name": "Test-Slots"}).json()["deck"]
    d = client.post(f"{API}/decks/{deck['id']}/slots", json={"tcgdex_card_id": f"{P}1-114", "count": 2}).json()
    d = client.post(f"{API}/decks/{deck['id']}/slots", json={"tcgdex_card_id": f"{P}1-114"}).json()
    assert len(d["slots"]) == 1 and d["slots"][0]["count"] == 3 and d["slots"][0]["section"] == "trainer"
    assert d["slots"][0]["set_code"] is None and d["slots"][0]["number"] == "114"
    sid = d["slots"][0]["id"]
    d = client.put(f"{API}/decks/{deck['id']}/slots/{sid}", json={"count": 1}).json()
    assert d["slots"][0]["count"] == 1
    d = client.put(f"{API}/decks/{deck['id']}/slots/{sid}", json={"count": 0}).json()
    assert d["slots"] == []
    assert client.post(f"{API}/decks/{deck['id']}/slots", json={"tcgdex_card_id": "nope-1"}).status_code == 404
    r = client.post(f"{API}/decks/{deck['id']}/import", json={"text": "hello\nworld"})
    assert r.status_code == 400


# ── Eigene Karten ────────────────────────────────────────────────────────────

def test_fill_prefers_exact_then_cheapest_and_keeps_pokemon_prints_apart(client, catalog):
    exact = _owned(client, f"{P}1-062", "Testmon ex", "9.00")
    sir = _owned(client, f"{P}1-150", "Testmon ex", "80.00")          # gleiches Set, andere Seltenheit
    other_set = _owned(client, f"{P}2-011", "Testmon ex", "1.00")     # anderes Set → nicht automatisch
    boss_cheap = _owned(client, f"{P}2-090", "Boss's Orders (Ghetsis)", "0.10")
    boss_exact = _owned(client, f"{P}1-114", "Boss's Orders", "3.00")
    text = "Pokémon: 1\n3 Testmon ex TDA 62\n\nTrainer: 1\n2 Boss's Orders TDA 114\n"
    res = client.post(f"{API}/decks", json={"name": "Test-Fill", "text": text, "fill": True}).json()
    deck = res["deck"]
    slots = {s["name"]: s for s in deck["slots"]}
    mon = slots["Testmon ex"]
    assert [a["card"]["id"] for a in mon["cards"]] == [exact, sir]
    assert [a["match"] for a in mon["cards"]] == ["exact", "same_name"]
    assert mon["available"] == 0 and mon["available_other"] == 1
    boss = slots["Boss's Orders"]
    assert [a["card"]["id"] for a in boss["cards"]] == [boss_exact, boss_cheap]
    assert deck["in_deck"] == 4
    # Karte zeigt ihr Deck
    card = client.get(f"{API}/cards/{exact}").json()
    assert card["deck_id"] == deck["id"] and card["deck_name"] == "Test-Fill"
    # Kandidaten: der andere Druck, als solcher markiert
    cands = client.get(f"{API}/decks/{deck['id']}/slots/{mon['id']}/candidates").json()
    assert [(c["card"]["id"], c["match"]) for c in cands] == [(other_set, "other_print")]
    # von Hand dazulegen → zählt für den Platz, Liste bleibt
    d = client.post(f"{API}/decks/{deck['id']}/cards", json={"card_ids": [other_set]}).json()
    mon = {s["name"]: s for s in d["slots"]}["Testmon ex"]
    assert len(mon["cards"]) == 3 and mon["cards"][2]["match"] == "other_print" and mon["count"] == 3


def test_one_deck_per_card_and_list_grows(client, catalog):
    a = client.post(f"{API}/decks", json={"name": "Test-A"}).json()["deck"]
    b = client.post(f"{API}/decks", json={"name": "Test-B"}).json()["deck"]
    card = _owned(client, f"{P}1-062", "Testmon ex")
    d = client.post(f"{API}/decks/{a['id']}/cards", json={"card_ids": [card]}).json()
    assert d["slots"][0]["tcgdex_card_id"] == f"{P}1-062" and d["slots"][0]["count"] == 1   # Liste gewachsen
    assert client.post(f"{API}/decks/{b['id']}/cards", json={"card_ids": [card]}).status_code == 409
    d = client.post(f"{API}/decks/{b['id']}/cards", json={"card_ids": [card], "move": True}).json()
    assert d["in_deck"] == 1
    assert client.get(f"{API}/decks/{a['id']}").json()["in_deck"] == 0
    # nicht besessen → nicht ins Deck; Besitz aufgeben nimmt sie heraus
    wish = client.post(f"{API}/cards", json={"kartenname": "Deckscan Wunsch", "besessen": False}).json()
    assert client.post(f"{API}/decks/{b['id']}/cards", json={"card_ids": [wish["id"]]}).status_code == 400
    assert client.put(f"{API}/cards/{card}", json={"besessen": False}).status_code == 200
    assert client.get(f"{API}/cards/{card}").json()["deck_id"] is None
    client.put(f"{API}/cards/{card}", json={"besessen": True})
    client.post(f"{API}/decks/{b['id']}/cards", json={"card_ids": [card]})
    # herausnehmen, Deck löschen gibt Karten frei
    assert client.delete(f"{API}/decks/{b['id']}/cards/{card}").json()["in_deck"] == 0
    client.post(f"{API}/decks/{b['id']}/cards", json={"card_ids": [card]})
    assert client.delete(f"{API}/decks/{b['id']}").status_code == 204
    assert client.get(f"{API}/cards/{card}").json()["deck_id"] is None
    assert client.get(f"{API}/decks/{b['id']}").status_code == 404


def test_scan_into_deck(client, catalog):
    deck = client.post(f"{API}/decks", json={"name": "Test-Scan", "text": "Pokémon: 1\n1 Testmon ex TDA 62\n"}).json()["deck"]
    item = {"kartenname": "Deckscan Testmon", "englischer_name": "Testmon ex", "tcgdex_card_id": f"{P}1-062",
            "set_id": f"{P}1", "copies": 3}
    r = client.post(f"{API}/scan/commit", json={"target": "deck", "deck_id": deck["id"], "items": [item]})
    assert r.status_code == 200, r.text
    d = client.get(f"{API}/decks/{deck['id']}").json()
    assert d["slots"][0]["count"] == 3 and len(d["slots"][0]["cards"]) == 3 and d["in_deck"] == 3
    assert client.post(f"{API}/scan/commit", json={"target": "deck", "items": [item]}).status_code == 400
    assert client.post(f"{API}/scan/commit", json={"target": "deck", "deck_id": 999999, "items": [item]}).status_code == 404


# ── Mehrbenutzer ─────────────────────────────────────────────────────────────

@pytest.fixture()
def eve(client):
    db = SessionLocal()
    old = db.scalar(select(User).where(User.username == "eve-deck"))
    if old is not None:
        db.delete(old)
        db.commit()
    u = User(username="eve-deck", is_admin=False, is_active=True)
    db.add(u)
    db.commit()
    token, _ = issue_token(u)
    uid = u.id
    db.close()
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    yield c
    db = SessionLocal()
    db.delete(db.get(User, uid))
    db.commit()
    db.close()


def test_decks_are_private(client, catalog, eve):
    mine = client.post(f"{API}/decks", json={"name": "Test-Mine", "text": "Pokémon: 1\n1 Testmon ex TDA 62\n"}).json()["deck"]
    card = _owned(client, f"{P}1-062", "Testmon ex")
    assert all(d["id"] != mine["id"] for d in eve.get(f"{API}/decks").json())
    assert eve.get(f"{API}/decks/{mine['id']}").status_code == 404
    assert eve.put(f"{API}/decks/{mine['id']}", json={"name": "x"}).status_code == 404
    assert eve.delete(f"{API}/decks/{mine['id']}").status_code == 404
    assert eve.get(f"{API}/decks/{mine['id']}/export").status_code == 404
    slot = mine["slots"][0]["id"]
    assert eve.put(f"{API}/decks/{mine['id']}/slots/{slot}", json={"count": 4}).status_code == 404
    theirs = eve.post(f"{API}/decks", json={"name": "Test-Theirs"}).json()["deck"]
    # fremde Karte nicht in ihr Deck, ihre Liste nicht über meinen Platz
    assert eve.post(f"{API}/decks/{theirs['id']}/cards", json={"card_ids": [card]}).status_code == 404
    assert eve.put(f"{API}/decks/{theirs['id']}/slots/{slot}", json={"count": 4}).status_code == 404
    # Auffüllen nimmt nur eigene Karten
    eve.post(f"{API}/decks/{theirs['id']}/import", json={"text": "1 Testmon ex TDA 62"})
    assert eve.post(f"{API}/decks/{theirs['id']}/fill").json()["added"] == 0


# ── Diagnose-Werkzeug ────────────────────────────────────────────────────────

def test_deck_diag_explains_reprints_and_unmatched_lines(client, catalog, capsys):
    """app/tools/deck_diag: zeigt, warum ein Trainer „nicht legal" ist (Drucke, Zeichen,
    Text-Fingerabdruck) und was eine Zeile ohne Katalog-Druck beim neuen Abgleich fände."""
    from app.tools import deck_diag

    db = SessionLocal()
    db.add_all([
        _row(f"{P}9-132", f"{P}9", "132", "Boss's Orders", "Trainer", mark="F", trainer_type="Supporter"),
    ])
    db.commit()
    for cid, key in ((f"{P}9-132", "switch in 1 of your opponents benched pokemon"),
                     (f"{P}1-114", "switch in 1 of your opponents benched pokemon to the active spot")):
        db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == cid).update({"effect_key": key})
    db.commit()
    deck = client.post(f"{API}/decks", json={"name": "Test-Diag", "text": "Trainer: 1\n1 Boss's Orders ZZ9 132\n"}).json()["deck"]
    d = db.get(Deck, deck["id"])
    from app.models.deck import DeckSlot
    slot = db.scalars(select(DeckSlot).where(DeckSlot.deck_id == d.id)).one()
    slot.tcgdex_card_id, slot.set_code = f"{P}9-132", None
    db.commit()
    card = PokemonCard(kartenname="Deckscan Testmon ex", englischer_name="Testmon ex", karten_nr="062/193",
                       besessen=True, tcgdex_card_id=None, deck_id=d.id)
    db.add(card)
    # so legten ältere Versionen einen Scan ohne Katalog-Treffer ab: Zeile nur mit Namen
    db.add(DeckSlot(deck_id=d.id, tcgdex_card_id=None, name="Testmon ex", number="62", count=1,
                    section="pokemon", match=None, position=2))
    db.commit()

    deck_diag.main([str(d.id)])
    out = capsys.readouterr().out
    assert "reprint_text" in out and "Boss's Orders" in out
    assert f"{P}1-114" in out and "differs" in out               # der I-Druck hat einen anderen Text
    assert f"{P}2-090" in out and "OTHER" in out                  # „(Ghetsis)": für die Reprint-Regel ein anderer Name
    assert "newest mark, any text: I; with the same text: F" in out
    assert f"{P}1-062 (set, pokemon)" in out                     # Zeile ohne Druck: fände ihn jetzt
    assert "Testmon ex" in out and "—" in out
    db.execute(delete(PokemonCard).where(PokemonCard.id == card.id).execution_options(all_users=True))
    db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id == f"{P}9-132"))
    db.commit()
    db.close()


def test_reprint_with_newer_wording_is_legal_but_marked(client, catalog):
    """Boss's Orders aus Brilliant Stars (F, alter Wortlaut) ist über den I-Nachdruck
    legal, obwohl dessen Text neu formuliert ist: im Deck kein Fehler, aber gekennzeichnet."""
    db = SessionLocal()
    db.add(_row(f"{P}9-132", f"{P}9", "132", "Boss's Orders", "Trainer", mark="F", trainer_type="Supporter"))
    db.commit()
    db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == f"{P}9-132").update({"effect_key": "old wording"})
    db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == f"{P}1-114").update({"effect_key": "new wording"})
    db.commit()
    try:
        deck = client.post(f"{API}/decks", json={"name": "Test-Reprint", "text": "Trainer: 1\n1 Boss's Orders ZZ9 132\n"}).json()["deck"]
        from app.models.deck import DeckSlot
        slot = db.scalars(select(DeckSlot).where(DeckSlot.deck_id == deck["id"])).one()
        slot.tcgdex_card_id = f"{P}9-132"
        db.commit()
        d = client.get(f"{API}/decks/{deck['id']}").json()
        s = d["slots"][0]
        assert s["status"] == "reprint_text" and s["legal_mark"] == "I"
        assert "not_in_format" not in _issues(d)
        # ganz ohne legalen Nachdruck bleibt es ein Fehler
        db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == f"{P}1-114").update({"regulation_mark": "F"})
        db.commit()
        d = client.get(f"{API}/decks/{deck['id']}").json()
        assert d["slots"][0]["status"] == "not_legal" and "not_in_format" in _issues(d)
    finally:
        db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == f"{P}1-114").update(
            {"regulation_mark": "I", "effect_key": None})
        db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id == f"{P}9-132"))
        db.commit()
        db.close()


def test_cards_without_a_catalog_print_land_on_the_right_line(client, catalog):
    """Scan ins Deck, Karte nur mit Name (+ Nummer): früher eine eigene Namens-Zeile je
    Karte unter Pokémon („nicht im Katalog"). Jetzt: Zeile desselben Drucks bzw. Namens,
    sonst der gefundene Druck — und alte solche Zeilen werden beim Öffnen nachgetragen."""
    from app.models.deck import DeckSlot
    deck = client.post(f"{API}/decks", json={"name": "Test-Relink", "text":
                       "Pokémon: 1\n2 Testmon ex TDA 62\n\nTrainer: 1\n1 Boss's Orders TDA 114\n"}).json()["deck"]
    db = SessionLocal()
    d = db.get(Deck, deck["id"])
    # alte Daten: Namens-Zeilen (alle unter Pokémon), wie frühere Scans sie anlegten
    for pos, (name, number) in enumerate([("Boss's Orders", None), ("Boss's Orders", "114"),
                                          ("Prime Spec", None), ("Totally Unknown", None)], start=10):
        db.add(DeckSlot(deck_id=d.id, tcgdex_card_id=None, name=name, number=number, count=1,
                        section="pokemon", match=None, position=pos))
    db.commit()
    got = client.get(f"{API}/decks/{deck['id']}").json()
    by = {(s["name"], s["section"]): s for s in got["slots"]}
    assert by[("Boss's Orders", "trainer")]["count"] == 3               # beide in die vorhandene Zeile
    assert by[("Prime Spec", "trainer")]["tcgdex_card_id"] == f"{P}1-120"
    assert by[("Prime Spec", "trainer")]["match"] == "name"
    assert by[("Totally Unknown", "pokemon")]["status"] == "unmatched"   # nichts gefunden: bleibt
    assert not any(s["section"] == "pokemon" and s["name"] == "Boss's Orders" for s in got["slots"])

    # neue Scans: Karte ohne Druck → Zeile desselben Namens bzw. Drucks, nicht eine eigene
    added = []
    for name, nr in (("Boss's Orders", None), ("Testmon ex", "062/193"), ("Other Spec", None)):
        c = PokemonCard(kartenname=f"Deckscan {name}", englischer_name=name, karten_nr=nr, besessen=True)
        db.add(c)
        db.flush()
        svc.assign(db, d, c)
        added.append(c.id)
    db.commit()
    slots = svc._slots(db, d.id)
    names = [(s.name, s.section, s.count, s.tcgdex_card_id) for s in slots]
    assert ("Boss's Orders", "trainer", 3, f"{P}1-114") in names        # freie Plätze: Liste wächst nicht
    assert ("Testmon ex", "pokemon", 2, f"{P}1-062") in names
    assert any(n == "Other Spec" and sec == "trainer" and tid == f"{P}2-121" for n, sec, _c, tid in names)
    assert [n for n, _s, _c, tid in names if tid is None] == ["Totally Unknown"]
    cov = svc.coverage(db, d)
    assert not cov.extra and sorted(c.id for v in cov.by_slot.values() for c, _ in v) == sorted(added)
    # voll (3 Plätze, dann 4 Karten): die vierte Karte ohne Druck zählt die gleichnamige Zeile hoch
    for _ in range(3):
        c = PokemonCard(kartenname="Deckscan Boss", englischer_name="Boss's Orders", besessen=True)
        db.add(c)
        db.flush()
        svc.assign(db, d, c)
    db.commit()
    boss = [s for s in svc._slots(db, d.id) if s.name == "Boss's Orders"]
    assert len(boss) == 1 and boss[0].count == 4 and boss[0].section == "trainer"
    db.close()


def test_deck_diag_rematch_links_sure_cards_only_with_fix(client, catalog, capsys, monkeypatch, tmp_path):
    """--rematch schickt das Scan-Foto einer Karte ohne Druck durch den heutigen Scan;
    --fix verknüpft nur, wenn der Bild-Abgleich sicher ist und der Name passt."""
    import httpx

    from app.config import settings
    from app.models.deck import DeckSlot
    from app.services import tcgdex
    from app.tools import deck_diag

    real = httpx.AsyncClient
    answers = {"Deckscan Boss": ("tdeck1-114", "Boss's Orders", True), "Deckscan Odd": ("tdeck1-062", "Testmon ex", True),
               "Deckscan Unsure": ("tdeck1-120", "Prime Spec", False),
               "Deckscan Number": ("tdeck2-090", "Boss's Orders", False)}   # unsicher, aber die Nummer entscheidet

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ready", "catalog": 3, "uptime_s": 5})
        body = request.content.decode("latin-1")
        who = next(k for k in answers if k.replace(" ", "_") in body)
        cid, name, sure = answers[who]
        cands = [{"card_id": cid, "name": name, "local_id": cid.rsplit("-", 1)[1], "art_inliers": 40}]
        if who == "Deckscan Number":     # bester Treffer ein anderer Druck, die Nummer 114 zeigt auf den richtigen
            cands.append({"card_id": "tdeck1-114", "name": "Boss's Orders", "local_id": "114", "art_inliers": 38})
        return httpx.Response(200, json={"cards": [{"auto": sure, "confidence": 0.9 if sure else 0.4, "candidates": cands}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def fake_get_card(card_id, lang="en"):
        with SessionLocal() as s:
            row = s.get(TcgdexCatalog, card_id)
        return tcgdex.TcgdexCard(id=card_id, localId=row.local_id, name=row.name_en, category=row.category,
                                 set={"id": row.set_id, "name": "Testset"})

    monkeypatch.setattr(tcgdex, "get_card", fake_get_card)
    monkeypatch.setattr(tcgdex, "search_cards", lambda *a, **k: _async([]))
    monkeypatch.setattr(settings, "images_dir", str(tmp_path / "images"))
    (tmp_path / "images").mkdir()

    deck = client.post(f"{API}/decks", json={"name": "Test-Rematch", "text": "Trainer: 1\n4 Boss's Orders TDA 114\n"}).json()["deck"]
    db = SessionLocal()
    ids = {}
    for name, en, nr in (("Deckscan Boss", "Boss's Orders", None), ("Deckscan Odd", "Boss's Orders", None),
                         ("Deckscan Unsure", "Prime Spec", None), ("Deckscan Number", "Boss's Orders", "114/193")):
        fn = f"{name.replace(' ', '_')}.jpg"
        (tmp_path / "images" / fn).write_bytes(b"\xff\xd8 " + name.replace(" ", "_").encode())
        c = PokemonCard(kartenname=name, englischer_name=en, karten_nr=nr, besessen=True, deck_id=deck["id"],
                        bild_scan_pfad=f"images/{fn}")
        db.add(c)
        db.flush()
        ids[name] = c.id
    db.commit()

    deck_diag.main([str(deck["id"]), "--rematch"])
    out = capsys.readouterr().out
    assert "image match http" in out and "ready" in out
    assert "tdeck1-114 “Boss's Orders” sure" in out and "resolved now → tdeck1-114" in out
    assert "[name differs]" in out                      # „Odd": Foto zeigt etwas anderes
    db.expire_all()
    assert db.get(PokemonCard, ids["Deckscan Boss"]).tcgdex_card_id is None      # ohne --fix: nichts geschrieben

    deck_diag.main([str(deck["id"]), "--rematch", "--fix"])
    out = capsys.readouterr().out
    assert "linked 2 card(s)" in out and "picked by its number" in out
    db.expire_all()
    assert db.get(PokemonCard, ids["Deckscan Boss"]).tcgdex_card_id == "tdeck1-114"
    assert db.get(PokemonCard, ids["Deckscan Number"]).tcgdex_card_id == "tdeck1-114"   # nicht der beste Treffer
    assert db.get(PokemonCard, ids["Deckscan Odd"]).tcgdex_card_id is None       # Name passt nicht
    assert db.get(PokemonCard, ids["Deckscan Unsure"]).tcgdex_card_id is None    # nicht sicher
    deck_diag.main([str(deck["id"]), "--rematch", "--fix", "--any-print"])
    out = capsys.readouterr().out
    db.expire_all()
    assert db.get(PokemonCard, ids["Deckscan Unsure"]).tcgdex_card_id == "tdeck1-120"   # mit --any-print
    assert db.get(PokemonCard, ids["Deckscan Odd"]).tcgdex_card_id is None
    db.execute(delete(PokemonCard).where(PokemonCard.id.in_(list(ids.values()))).execution_options(all_users=True))
    db.execute(delete(DeckSlot).where(DeckSlot.deck_id == deck["id"]))
    db.commit()
    db.close()


async def _async(v):
    return v


def test_choose_the_print_by_hand(client, catalog):
    """Zeile nur über den Namen gefunden (falsches Bild, z. B. die Full Art): Drucke
    derselben Karte auflisten, einen wählen — die Karte ohne Druck bekommt ihn mit."""
    from app.models.deck import DeckSlot
    db = SessionLocal()
    db.add_all([_row(f"{P}3-155", f"{P}3", "155", "Lana's Test", "Trainer", mark="H", trainer_type="Supporter"),
                _row(f"{P}3-219", f"{P}3", "219", "Lana's Test", "Trainer", mark="H", trainer_type="Supporter",
                     rarity="Special Illustration Rare")])
    db.commit()
    for cid, n in ((f"{P}3-155", 155), (f"{P}3-219", 219)):
        db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == cid).update({"local_id_num": n})
    db.commit()
    try:
        # nur über den Namen: der normale Druck, nicht die Special Illustration Rare dahinter
        row, how = svc.match_card(db, "Lana's Test", None, None)
        assert (row.card_id, how) == (f"{P}3-155", "name")

        deck = client.post(f"{API}/decks", json={"name": "Test-Print", "text": "Trainer: 1\n1 Boss's Orders TDA 114\n"}).json()["deck"]
        d = db.get(Deck, deck["id"])
        db.add(DeckSlot(deck_id=d.id, tcgdex_card_id=f"{P}3-219", name="Lana's Test", count=1, section="trainer",
                        match="name", position=5))
        card = PokemonCard(kartenname="Deckscan Lana", englischer_name="Lana's Test", besessen=True, deck_id=d.id)
        db.add(card)
        db.commit()
        got = client.get(f"{API}/decks/{d.id}").json()
        slot = next(s for s in got["slots"] if s["name"] == "Lana's Test")
        prints = client.get(f"{API}/decks/{d.id}/slots/{slot['id']}/prints").json()
        assert [p["tcgdex_card_id"] for p in prints] == [f"{P}3-219", f"{P}3-155"]
        assert [p["current"] for p in prints] == [True, False]

        r = client.put(f"{API}/decks/{d.id}/slots/{slot['id']}", json={"tcgdex_card_id": f"{P}1-114"})
        assert r.status_code == 400                                   # andere Karte, kein anderer Druck
        got = client.put(f"{API}/decks/{d.id}/slots/{slot['id']}", json={"tcgdex_card_id": f"{P}3-155"}).json()
        slot = next(s for s in got["slots"] if s["name"] == "Lana's Test")
        assert slot["tcgdex_card_id"] == f"{P}3-155" and slot["match"] == "set" and slot["number"] == "155"
        db.expire_all()
        c = db.get(PokemonCard, card.id)
        assert c.tcgdex_card_id == f"{P}3-155" and c.set_id == f"{P}3" and c.regulation_mark == "H"

        # nur über den Namen gefunden, die eigene Karte kennt ihren Druck: die Zeile übernimmt ihn
        s2 = db.get(DeckSlot, slot["id"])
        s2.tcgdex_card_id, s2.match = f"{P}3-219", "name"
        db.commit()
        got = client.get(f"{API}/decks/{d.id}").json()
        slot = next(s for s in got["slots"] if s["name"] == "Lana's Test")
        assert slot["tcgdex_card_id"] == f"{P}3-155" and slot["match"] == "set"
        db.execute(delete(PokemonCard).where(PokemonCard.id == card.id).execution_options(all_users=True))
        db.commit()
    finally:
        db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id.like(f"{P}3-%")))
        db.commit()
        db.close()


def test_basic_energy_named_like_tcg_live(client, catalog):
    """Basis-Energien heißen wie in Pokémon TCG Live „Basic Psychic Energy" — auf
    Karten, auf Deckzeilen und im Abgleich; die Spezial-Energien bleiben."""
    from sqlalchemy import text

    from app.main import _run_light_migrations
    from app.services import catalog as catalog_svc
    from app.services.card_names import basic_energy_name

    assert basic_energy_name("Psychic Energy") == "Basic Psychic Energy"
    assert basic_energy_name("basic psychic energy") == "Basic Psychic Energy"
    assert basic_energy_name("Darkness Energy", "Special") == "Darkness Energy"
    assert basic_energy_name("Luminous Energy") == "Luminous Energy"
    assert basic_energy_name("Psycho-Energie") == "Psycho-Energie"
    assert svc.rule_name("Psychic Energy") == svc.rule_name("Basic {P} Energy") == "basic psychic energy"

    with SessionLocal() as db:
        row = db.get(TcgdexCatalog, f"{P}e-005")
        f = catalog_svc.catalog_fields(db, row, "EN")
        assert f["kartenname"] == "Basic Psychic Energy" and f["englischer_name"] == "Basic Psychic Energy"
        assert [r.card_id for r in svc.prints_for(db, "Basic {P} Energy")] == [f"{P}e-005"]

    # TCG-Live-Liste mit „Basic {P} Energy" → Druck gefunden, Export unverändert
    deck = client.post(f"{API}/decks", json={"name": "Test-Energy", "text": "Energy: 1\n4 Basic {P} Energy TDE 5\n"}).json()["deck"]
    assert deck["slots"][0]["tcgdex_card_id"] == f"{P}e-005"
    assert "4 Basic {P} Energy TDE 5" in client.get(f"{API}/decks/{deck['id']}/export").text
    # aus dem Katalog hinzugefügt → Zeile heißt „Basic Grass Energy"
    added = client.post(f"{API}/decks/{deck['id']}/slots", json={"tcgdex_card_id": f"{P}e-009", "count": 2}).json()
    assert any(s["name"] == "Basic Grass Energy" for s in added["slots"]), [s["name"] for s in added["slots"]]

    # gespeicherte Karten und Zeilen: beim Start einmal umbenannt (Spezial-Energie nicht)
    with SessionLocal() as db:
        db.add_all([
            TcgdexCatalog(card_id=f"{P}e-old", region="west", set_id=f"{P}e", local_id="old", name="Darkness Energy",
                          name_en="Darkness Energy", category="Energy", energy_type="Special"),
        ])
        db.commit()
        uid = db.scalar(text("SELECT id FROM users ORDER BY id LIMIT 1"))
        db.add_all([
            PokemonCard(kartenname="Psychic Energy", englischer_name="Psychic Energy", user_id=uid,
                        tcgdex_card_id=f"{P}e-005", besessen=True),
            PokemonCard(kartenname="Psycho-Energie", englischer_name="Psychic Energy", user_id=uid, sprache="DE",
                        tcgdex_card_id=f"{P}e-005", besessen=True),
            PokemonCard(kartenname="Darkness Energy", englischer_name="Darkness Energy", user_id=uid,
                        tcgdex_card_id=f"{P}e-old", besessen=True),
        ])
        db.execute(text("UPDATE deck_slots SET name = 'Grass Energy' WHERE tcgdex_card_id = :c"), {"c": f"{P}e-009"})
        db.commit()
    _run_light_migrations()
    _run_light_migrations()                                   # zweimal: nichts doppelt
    with SessionLocal() as db:
        cards = db.execute(select(PokemonCard.kartenname, PokemonCard.englischer_name)
                           .where(PokemonCard.tcgdex_card_id.like(f"{P}e-%"))
                           .execution_options(all_users=True)).all()
        assert sorted(cards) == sorted([("Basic Psychic Energy", "Basic Psychic Energy"),
                                        ("Psycho-Energie", "Basic Psychic Energy"),
                                        ("Darkness Energy", "Darkness Energy")]), cards
        assert db.scalar(text("SELECT name FROM deck_slots WHERE tcgdex_card_id = :c"), {"c": f"{P}e-009"}) \
            == "Basic Grass Energy"
