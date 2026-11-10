"""Katalog liefert den englischen Set-Namen mit (UI zeigt ihn bei Sprache EN)."""

from app.database import SessionLocal
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog

API = "/api/v1"


def test_catalog_list_and_detail_carry_english_set_name(client, monkeypatch):
    from app.services import tcgdex

    async def no_live(*a, **k):
        return None
    for name in ("fetch_card", "get_card", "card_detail"):
        if hasattr(tcgdex, name):
            monkeypatch.setattr(tcgdex, name, no_live)

    db = SessionLocal()
    try:
        if not db.query(PokemonSet).filter(PokemonSet.set_id == "zz-en1").first():
            db.add(PokemonSet(code="ZZE", name="Testset Deutsch", name_en="Test Set English", set_id="zz-en1"))
        if not db.get(TcgdexCatalog, "zz-en1-001"):
            db.add(TcgdexCatalog(card_id="zz-en1-001", region="west", set_id="zz-en1", set_code="ZZE",
                                 set_name="Testset Deutsch", local_id="001", name="Testkarte", name_en="Test Card",
                                 enriched=True))
        db.commit()
    finally:
        db.close()
    try:
        items = client.get(f"{API}/catalog", params={"q": "Testkarte"}).json()["items"]
        row = next(i for i in items if i["card_id"] == "zz-en1-001")
        assert row["set_name"] == "Testset Deutsch" and row["set_name_en"] == "Test Set English"
        d = client.get(f"{API}/catalog/zz-en1-001/detail").json()
        assert d["set_name_en"] == "Test Set English"
    finally:
        db = SessionLocal()
        try:
            db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == "zz-en1-001").delete()
            db.query(PokemonSet).filter(PokemonSet.set_id == "zz-en1").delete()
            db.commit()
        finally:
            db.close()
