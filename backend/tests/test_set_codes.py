"""Sets ohne offizielles Kürzel tragen die TCGdex-ID als Code ("swshp",
"me02.5"): Scan-Auflösung und Set-Lookup finden sie trotzdem."""
from sqlalchemy import delete

from app.database import SessionLocal
from app.models.pokemon_set import PokemonSet
from app.services.card_image_service import resolve_set_id
from app.services.scan.resolver import _resolve_set_id, _set_by_code


def test_set_by_code_ohne_kuerzel(client):
    db = SessionLocal()
    try:
        db.add(PokemonSet(code="tswshp", name="Test Black Star Promos", set_id="tswshp"))
        db.add(PokemonSet(code="TPAF", name="Test Paldean Fates", set_id="tsv04.5"))
        db.commit()
        assert _set_by_code(db, "tswshp").code == "tswshp"          # aus der Set-Auswahl
        assert _set_by_code(db, "TSWSHP").code == "tswshp"          # gelesen, groß
        assert _set_by_code(db, "tpaf").code == "TPAF"              # klein getippt
        assert _set_by_code(db, "tsv04.5").code == "TPAF"           # TCGdex-ID
        assert _set_by_code(db, "  ") is None and _set_by_code(db, "nope") is None
        assert _resolve_set_id(db, "tswshp")[0] == "tswshp"
        assert resolve_set_id(db, "Test Black Star Promos (tswshp)") == "tswshp"
    finally:
        db.execute(delete(PokemonSet).where(PokemonSet.code.in_(["tswshp", "TPAF"])))
        db.commit()
        db.close()
