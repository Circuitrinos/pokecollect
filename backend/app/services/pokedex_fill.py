"""
Pokédex auffüllen: legt für jede Spezies 1–1025, zu der es noch KEINE Zeile
gibt, einen Pokédex-Platzhalter an (nicht besessen, wie delete_card ihn
hinterlässt). Die App setzt diese 1025 Zeilen voraus (Platzhalter-Adoption,
Binder mit allen Slots), legt sie auf einer frischen Installation aber nie an.

Namen: PokeAPI-Tabelle pokemon_species_names (eine Datei, DE = Sprache 6,
EN = Sprache 9). Bild: kommt bei Platzhaltern ohnehin aus der Pokédex-Nummer
(schemas/card.py bild_platzhalter_spiegel), daher keine URL nötig.

Idempotent: vorhandene Spezies (besessen, Platzhalter oder Wunschliste)
bleiben unberührt; ein zweiter Lauf legt nichts mehr an.
"""
from __future__ import annotations

import csv
import io
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.card import PokemonCard
from app.services.bild_spiegel import POKEDEX_MAX

log = logging.getLogger(__name__)

SPECIES_NAMES_URL = (
    "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/pokemon_species_names.csv"
)
_LANG_DE, _LANG_EN = "6", "9"


def parse_species_names(text: str) -> dict[int, tuple[str | None, str | None]]:
    """CSV → {nr: (deutsch, englisch)} für 1..POKEDEX_MAX."""
    out: dict[int, list] = {}
    for row in csv.DictReader(io.StringIO(text)):
        try:
            nr = int(row["pokemon_species_id"])
        except (KeyError, ValueError):
            continue
        if not 1 <= nr <= POKEDEX_MAX:
            continue
        lang = row.get("local_language_id")
        if lang in (_LANG_DE, _LANG_EN):
            slot = out.setdefault(nr, [None, None])
            slot[0 if lang == _LANG_DE else 1] = (row.get("name") or "").strip() or None
    return {nr: (de, en) for nr, (de, en) in out.items()}


async def fetch_species_names() -> dict[int, tuple[str | None, str | None]]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        r = await client.get(SPECIES_NAMES_URL)
        r.raise_for_status()
    names = parse_species_names(r.text)
    if len(names) < POKEDEX_MAX * 0.9:  # Format geändert? Lieber abbrechen als halb füllen.
        raise ValueError(f"unerwartete Namensliste ({len(names)} Spezies)")
    return names


def fill_pokedex(db: Session, names: dict[int, tuple[str | None, str | None]]) -> dict:
    present = set(db.scalars(
        select(PokemonCard.pokedex_nr).where(PokemonCard.pokedex_nr.isnot(None)).distinct()
    ).all())
    created = 0
    for nr in range(1, POKEDEX_MAX + 1):
        if nr in present:
            continue
        de, en = names.get(nr, (None, None))
        if not (de or en):
            continue
        db.add(PokemonCard(pokedex_nr=nr, kartenname=de or en, englischer_name=en or de, besessen=False))
        created += 1
    db.commit()
    log.info("Pokédex aufgefüllt: %d Platzhalter angelegt, %d Spezies waren schon da.", created, len(present))
    return {"created": created, "existing": len([n for n in present if 1 <= n <= POKEDEX_MAX]),
            "total": POKEDEX_MAX}
