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


def _cat(card_id, name, category, mark=None, energy_type=None, checked=True, enriched=True, region="west"):
    return TcgdexCatalog(card_id=P + card_id, region=region, set_id=P + "set", name=name, name_en=name,
                         category=category, regulation_mark=mark, energy_type=energy_type,
                         regulation_checked=checked, enriched=enriched,
                         image_url=f"https://assets.tcgdex.net/en/sv/{card_id}/high.webp")


@pytest.fixture()
def catalog(client):
    db = SessionLocal()
    rows = [
        _cat("pika-h", "Pikachu", "Pokemon", "H"),
        _cat("pika-d", "Pikachu", "Pokemon", "D"),
        _cat("pika-i", "Pikachu", "Pokemon", "I"),
        _cat("ball-d", "Testball", "Trainer", "D"),
        _cat("ball-i", "Testball", "Trainer", "I"),
        _cat("old-trainer", "Oldie", "Trainer", None),
        _cat("fire", "Basic Fire Energy", "Energy", None, energy_type="Basic"),
        _cat("spec-e", "Testenergy", "Energy", "E", energy_type="Special"),
        _cat("spec-j", "Testenergy", "Energy", "J", energy_type="Special"),
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
    tc = SimpleNamespace(regulationMark="i", energyType=None, category="Trainer",
                         legal=SimpleNamespace(standard=True, expanded=True))
    regulation.apply_to_catalog(row, tc)
    assert (row.regulation_mark, row.legal_standard, row.legal_expanded, row.regulation_checked) == ("I", True, True, True)


def test_karten_legalitaet(client, catalog):
    pika_h = _create(client, "pika-h")
    pika_d = _create(client, "pika-d")
    ball_d = _create(client, "ball-d")
    oldie = _create(client, "old-trainer")
    fire = _create(client, "fire")
    spec_e = _create(client, "spec-e")
    unchecked = _create(client, "unchecked")
    manual = _create(client, None, regulation_mark="j")

    assert (pika_h["regulation_mark"], pika_h["legal_mark"]) == ("H", "H")
    # Pokémon: kein Reprint-Vorteil, auch wenn ein gleichnamiger Druck legal ist
    assert (pika_d["regulation_mark"], pika_d["legal_mark"]) == ("D", "D")
    # Trainer/Spezial-Energie: jüngstes gleichnamiges Zeichen zählt
    assert (ball_d["regulation_mark"], ball_d["legal_mark"]) == ("D", "I")
    assert (spec_e["regulation_mark"], spec_e["legal_mark"]) == ("E", "J")
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

    mine = {c["id"] for c in (pika_h, pika_d, ball_d, oldie, fire, spec_e, unchecked, manual)}
    assert ids(standard="legal") & mine == {pika_h["id"], ball_d["id"], fire["id"], spec_e["id"], manual["id"]}
    assert ids(standard="not_legal") & mine == {pika_d["id"], oldie["id"]}
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

    # Neuer Reprint taucht im Katalog auf → nächtliche Neuberechnung hebt den Trainer an
    oldie = _create(client, "old-trainer")
    assert oldie["legal_mark"] == "-"
    db = SessionLocal()
    try:
        db.add(_cat("oldie-j", "Oldie", "Trainer", "J"))
        db.commit()
        assert regulation.refresh_cards(db) >= 1
        got = db.get(PokemonCard, oldie["id"], execution_options={"all_users": True})
        assert (got.regulation_mark, got.legal_mark) == (None, "J")
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

    async def fake_get_card(cid, lang="en"):
        calls.append(cid)
        return TcgdexCard.model_validate({"id": cid, "category": "Pokemon", "regulationMark": "J",
                                          "legal": {"standard": True, "expanded": True}})

    monkeypatch.setattr(tcgdex, "get_card", fake_get_card)
    card = _create(client, "unchecked")
    assert card["legal_mark"] is None
    db = SessionLocal()
    try:
        res = asyncio.run(regulation.backfill_catalog(db, limit=50, owned_only=True))
        assert P + "unchecked" in calls and res["checked"] >= 1
        row = db.get(TcgdexCatalog, P + "unchecked")
        assert (row.regulation_mark, row.regulation_checked, row.legal_expanded) == ("J", True, True)
    finally:
        db.close()
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
