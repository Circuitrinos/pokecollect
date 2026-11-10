"""Turnier-Legalität: Regulation Mark aus TCGdex/Katalog, Reprint-Regel, Filter."""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models.card import PokemonCard
from app.models.setting import AppSetting
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import regulation
from app.services.tcgdex import TcgdexCard

API = "/api/v1"
P = "testreg-"


def _cat(card_id, name, category, mark=None, energy_type=None, checked=True, enriched=True, region="west",
         effect=None, effect_checked=None):
    return TcgdexCatalog(card_id=P + card_id, region=region, set_id=P + "set", name=name, name_en=name,
                         category=category, regulation_mark=mark, energy_type=energy_type,
                         regulation_checked=checked, enriched=enriched,
                         effect_key=regulation.effect_key(effect),
                         effect_checked=checked if effect_checked is None else effect_checked,
                         image_url=f"https://assets.tcgdex.net/en/sv/{card_id}/high.webp")


@pytest.fixture()
def catalog(client):
    db = SessionLocal()
    rows = [
        _cat("pika-h", "Pikachu", "Pokemon", "H"),
        _cat("pika-d", "Pikachu", "Pokemon", "D"),
        _cat("pika-i", "Pikachu", "Pokemon", "I"),
        _cat("ball-d", "Testball", "Trainer", "D",
             effect="Search your deck for a Pokémon, reveal it, and put it into your hand."),
        # gleicher Text, nur Akzente/Satzzeichen/Groß-klein anders → Reprint zählt
        _cat("ball-i", "Testball", "Trainer", "I",
             effect="search your deck for a Pokemon — reveal it and put it into your hand"),
        # gleicher Name, anderer Text („damage" statt „damage counter") → zählt nicht
        _cat("hurt-d", "Testhurt", "Trainer", "D", effect="Put 1 damage counter on 1 of your opponent's Pokémon."),
        _cat("hurt-i", "Testhurt", "Trainer", "I", effect="This card does 10 damage to 1 of your opponent's Pokémon."),
        _cat("old-trainer", "Oldie", "Trainer", None, effect="Draw a card."),
        _cat("fire", "Basic Fire Energy", "Energy", None, energy_type="Basic"),
        _cat("spec-e", "Testenergy", "Energy", "E", energy_type="Special", effect="Provides 2 Colorless Energy."),
        _cat("spec-j", "Testenergy", "Energy", "J", energy_type="Special", effect="Provides 2 Colorless Energy."),
        _cat("unchecked", "Mew", "Pokemon", None, checked=False),
    ]
    db.add_all(rows)
    db.commit()
    yield
    db.execute(delete(PokemonCard).where((PokemonCard.tcgdex_card_id.like(P + "%"))
                                         | (PokemonCard.kartenname == "X"))
               .execution_options(all_users=True))
    db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id.like(P + "%")))
    db.execute(delete(AppSetting).where(AppSetting.key == regulation.SETTING_KEY))
    db.commit()
    db.close()


def _create(client, tid, **kw):
    """Karte mit TCGdex-Referenz über den Scan-Commit (POST /cards kennt keine
    tcgdex_card_id), ohne Referenz über POST /cards (Handeingabe)."""
    if tid is None:
        r = client.post(f"{API}/cards", json={"kartenname": "X", "besessen": True, "sprache": "EN", **kw})
        assert r.status_code == 201, r.text
        return r.json()
    r = client.post(f"{API}/scan/commit", json={"items": [{"kartenname": "X", "sprache": "EN",
                                                          "tcgdex_card_id": P + tid}]})
    assert r.status_code == 200, r.text
    return client.get(f"{API}/cards/{r.json()['card_ids'][0]}").json()


def test_tcgdex_card_liest_felder_tolerant():
    tc = TcgdexCard.model_validate({"id": "sv01-1", "regulationMark": "G", "energyType": "Basic",
                                    "legal": {"standard": False, "expanded": True}})
    assert tc.regulationMark == "G" and tc.energyType == "Basic"
    assert tc.legal.standard is False and tc.legal.expanded is True
    kaputt = TcgdexCard.model_validate({"id": "x", "regulationMark": 7, "legal": "yes", "energyType": []})
    assert kaputt.regulationMark is None and kaputt.legal is None and kaputt.energyType is None
    teils = TcgdexCard.model_validate({"id": "y", "legal": {"standard": "vielleicht"}})
    assert teils.legal.standard is None


def test_norm_mark():
    assert regulation.norm_mark(" h ") == "H"
    for v in (None, "", "HH", "1", 5, "-"):
        assert regulation.norm_mark(v) is None


def test_apply_to_catalog():
    row = TcgdexCatalog(card_id="x")
    tc = SimpleNamespace(regulationMark="i", energyType=None, category="Trainer", effect="Draw 2 cards.",
                         legal=SimpleNamespace(standard=True, expanded=True))
    regulation.apply_to_catalog(row, tc)
    assert (row.regulation_mark, row.legal_standard, row.legal_expanded, row.regulation_checked) == ("I", True, True, True)
    assert row.effect_checked and row.effect_key == regulation.effect_key("draw 2 cards")


def test_effect_key():
    k = regulation.effect_key
    assert k("Search your deck for a Pokémon.") == k("search  your deck for a POKEMON")
    assert k("Put 1 damage counter on it.") != k("Put 1 damage on it.")
    assert k(None) is None and k("  ...  ") is None


def test_karten_legalitaet(client, catalog):
    pika_h = _create(client, "pika-h")
    pika_d = _create(client, "pika-d")
    ball_d = _create(client, "ball-d")
    hurt_d = _create(client, "hurt-d")
    oldie = _create(client, "old-trainer")
    fire = _create(client, "fire")
    spec_e = _create(client, "spec-e")
    unchecked = _create(client, "unchecked")
    manual = _create(client, None, regulation_mark="j")

    assert (pika_h["regulation_mark"], pika_h["legal_mark"]) == ("H", "H")
    # Pokémon: kein Reprint-Vorteil, auch wenn ein gleichnamiger Druck legal ist
    assert (pika_d["regulation_mark"], pika_d["legal_mark"]) == ("D", "D")
    # Trainer/Spezial-Energie: jüngstes gleichnamiges Zeichen zählt
    assert (ball_d["regulation_mark"], ball_d["legal_mark"], ball_d["name_mark"]) == ("D", "I", None)
    assert (spec_e["regulation_mark"], spec_e["legal_mark"]) == ("E", "J")
    # gleicher Name, anderer Text: kein Reprint-Vorteil, aber Hinweis über name_mark
    assert (hurt_d["regulation_mark"], hurt_d["legal_mark"], hurt_d["name_mark"]) == ("D", "D", "I")
    assert (oldie["regulation_mark"], oldie["legal_mark"]) == (None, "-")
    assert fire["basic_energy"] is True and fire["legal_mark"] == "-"
    assert (unchecked["regulation_mark"], unchecked["legal_mark"]) == (None, None)
    assert (manual["regulation_mark"], manual["legal_mark"]) == ("J", "J")

    def ids(**params):
        r = client.get(f"{API}/cards", params={"limit": 5000, **params})
        assert r.status_code == 200, r.text
        body = r.json()
        items = body["items"] if isinstance(body, dict) else body
        return {c["id"] for c in items}

    mine = {c["id"] for c in (pika_h, pika_d, ball_d, hurt_d, oldie, fire, spec_e, unchecked, manual)}
    assert ids(standard="legal") & mine == {pika_h["id"], ball_d["id"], fire["id"], spec_e["id"], manual["id"]}
    assert ids(standard="not_legal") & mine == {pika_d["id"], hurt_d["id"], oldie["id"]}
    assert ids(standard="unknown") & mine == {unchecked["id"]}
    assert ids(regulation_mark="H,j") & mine == {pika_h["id"], manual["id"]}
    assert ids(regulation_mark="-") & mine == {oldie["id"], fire["id"]}
    assert client.get(f"{API}/cards", params={"standard": "egal"}).status_code == 422

    # Rotation: niedrigstes Zeichen I → H fällt raus, der Reprint-Trainer bleibt
    r = client.put(f"{API}/settings", json={"standard_min_mark": "I"})
    assert r.status_code == 200 and r.json()["standard_min_mark"] == "I"
    assert ids(standard="legal") & mine == {ball_d["id"], fire["id"], spec_e["id"], manual["id"]}
    assert client.put(f"{API}/settings", json={"standard_min_mark": "1"}).status_code == 422


def test_handeingabe_und_reprint_nachtraeglich(client, catalog):
    card = _create(client, None)
    assert card["regulation_mark"] is None and card["legal_mark"] is None
    r = client.put(f"{API}/cards/{card['id']}", json={"regulation_mark": "g"})
    assert r.status_code == 200 and (r.json()["regulation_mark"], r.json()["legal_mark"]) == ("G", "G")
    assert client.put(f"{API}/cards/{card['id']}", json={"regulation_mark": "GH"}).status_code == 422
    r = client.put(f"{API}/cards/{card['id']}", json={"regulation_mark": ""})
    assert r.json()["regulation_mark"] is None

    # Neue Reprints tauchen im Katalog auf → nächtliche Neuberechnung
    oldie = _create(client, "old-trainer")
    assert oldie["legal_mark"] == "-"
    db = SessionLocal()
    try:
        db.add(_cat("oldie-i", "Oldie", "Trainer", "I", effect="Draw a card from the bottom of your deck."))
        db.commit()
        assert regulation.refresh_cards(db) >= 1
        got = db.get(PokemonCard, oldie["id"], execution_options={"all_users": True})
        assert (got.legal_mark, got.name_mark) == ("-", "I")          # anderer Text → nur Hinweis
        db.add(_cat("oldie-j", "Oldie", "Trainer", "J", effect="Draw a card."))
        db.commit()
        assert regulation.refresh_cards(db) >= 1
        db.expire_all()
        got = db.get(PokemonCard, oldie["id"], execution_options={"all_users": True})
        assert (got.regulation_mark, got.legal_mark, got.name_mark) == (None, "J", None)
    finally:
        db.close()


def test_einzelkarte_zeigt_expanded(client, catalog):
    db = SessionLocal()
    try:
        db.get(TcgdexCatalog, P + "pika-h").legal_expanded = True
        db.commit()
    finally:
        db.close()
    card = _create(client, "pika-h")
    assert client.get(f"{API}/cards/{card['id']}").json()["legal_expanded"] is True


def test_nachholer(client, catalog, monkeypatch):
    from app.services import tcgdex

    calls = []

    texts = {P + "unchecked": ("Pokemon", "J", None), P + "pretext-d": ("Trainer", "D", "Heal 30 damage."),
             P + "pretext-i": ("Trainer", "I", "Heal 30 damage."), P + "raw": ("Pokemon", "H", None),
             P + "nocat": ("Pokemon", "I", None)}

    async def fake_get_card(cid, lang="en"):
        calls.append(cid)
        if cid not in texts:
            return None                 # fremde Testdaten nicht anfassen
        cat, mark, effect = texts[cid]
        return TcgdexCard.model_validate({"id": cid, "category": cat, "regulationMark": mark, "effect": effect,
                                          "rarity": "Common", "legal": {"standard": True, "expanded": True}})

    monkeypatch.setattr(tcgdex, "get_card", fake_get_card)
    db = SessionLocal()
    # Zeilen aus der Version ohne Text-Prüfung: Zeichen da, Text fehlt
    db.add(_cat("pretext-d", "Pretext", "Trainer", "D", effect_checked=False))
    db.add(_cat("pretext-i", "Pretext", "Trainer", "I", effect_checked=False))
    # noch nicht angereichert (die Nacht-Anreicherung hat die Zeile noch nicht erreicht)
    db.add(_cat("raw", "Rawcard", "Pokemon", None, checked=False, enriched=False))
    db.commit()
    card = _create(client, "unchecked")
    old = _create(client, "pretext-d")
    raw = _create(client, "raw")
    nocat = _create(client, "nocat")            # TCGdex-ID ohne Katalogzeile
    assert raw["legal_mark"] is None and nocat["legal_mark"] is None
    assert card["legal_mark"] is None
    assert (old["legal_mark"], old["name_mark"]) == ("D", "I")      # Text noch unbekannt → nicht legal
    try:
        res = asyncio.run(regulation.backfill_catalog(db, limit=50, owned_only=True))
        assert P + "unchecked" in calls and P + "pretext-d" in calls and res["checked"] >= 2
        row = db.get(TcgdexCatalog, P + "unchecked")
        assert (row.regulation_mark, row.regulation_checked, row.legal_expanded) == ("J", True, True)
        db.expire_all()
        raw_row = db.get(TcgdexCatalog, P + "raw")
        assert raw_row.enriched and raw_row.rarity == "Common" and raw_row.regulation_mark == "H"
        assert res["direct"] >= 1
        res = asyncio.run(regulation.backfill_catalog(db, limit=50))
        assert P + "pretext-i" in calls
    finally:
        db.close()
    got = client.get(f"{API}/cards/{old['id']}").json()
    assert (got["legal_mark"], got["name_mark"]) == ("I", None)
    assert client.get(f"{API}/cards/{raw['id']}").json()["legal_mark"] == "H"
    got = client.get(f"{API}/cards/{nocat['id']}").json()
    assert (got["regulation_mark"], got["legal_mark"]) == ("I", "I")
    assert client.get(f"{API}/catalog/regulation").json()["pending_owned"] == 0
    got = client.get(f"{API}/cards/{card['id']}").json()
    assert (got["regulation_mark"], got["legal_mark"]) == ("J", "J")
    st = client.get(f"{API}/catalog/regulation").json()
    assert st["min_mark"] == "H" and "pending_owned" in st


def test_katalog_liste_zeigt_zeichen(client, catalog):
    r = client.get(f"{API}/catalog", params={"search": "Testball", "limit": 50})
    assert r.status_code == 200, r.text
    marks = {i["card_id"]: i["regulation_mark"] for i in r.json()["items"]}
    assert marks.get(P + "ball-i") == "I"


def test_scan_uebernimmt_zeichen(client, catalog):
    r = client.post(f"{API}/scan/commit", json={"items": [{"kartenname": "Testball", "sprache": "EN",
                                                          "tcgdex_card_id": P + "ball-d", "copies": 2}]})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        cards = db.scalars(select(PokemonCard).where(PokemonCard.id.in_(r.json()["all_card_ids"]))
                           .execution_options(all_users=True)).all()
        assert {(c.regulation_mark, c.legal_mark) for c in cards} == {("D", "I")}
    finally:
        db.close()
