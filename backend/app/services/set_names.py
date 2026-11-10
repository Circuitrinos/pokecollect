"""
Englische Set-Namen zu Katalogzeilen. Die Katalogzeile speichert den
deutschen Set-Namen (aus pokemon_sets.name, beim Sync); die Web-Oberfläche
wählt je nach UI-Sprache zwischen set_name und set_name_en.
"""

from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pokemon_set import PokemonSet


def english_set_names(db: Session, set_ids: Iterable[Optional[str]]) -> dict[str, str]:
    ids = {s for s in set_ids if s}
    if not ids:
        return {}
    rows = db.execute(select(PokemonSet.set_id, PokemonSet.name_en).where(PokemonSet.set_id.in_(ids))).all()
    return {sid: name for sid, name in rows if name}
