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
