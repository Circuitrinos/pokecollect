"""
Kartennamen, wie Pokémon TCG Live sie schreibt.

Basis-Energien heißen dort immer „Basic Psychic Energy" (Deckliste: „Basic {P}
Energy"), TCGdex führt viele Drucke nur als „Psychic Energy". Für Importe und
Exporte zählt der genaue Name — darum bekommen Basis-Energien überall den
vollen englischen Namen. Die alten Spezial-Energien „Darkness Energy" / „Metal
Energy" (energy_type „Special") bleiben, wie sie sind.
"""
from __future__ import annotations

import re

BASIC_TYPES = ("grass", "fire", "water", "lightning", "psychic", "fighting", "darkness", "metal", "fairy")
_BASIC_RE = re.compile(r"\s*(?:basic\s+)?(" + "|".join(BASIC_TYPES) + r")\s+energy\s*", re.IGNORECASE)


def basic_energy_name(name: str | None, energy_type: str | None = None) -> str | None:
    """„Psychic Energy" → „Basic Psychic Energy"; alles andere unverändert."""
    if not name or (energy_type or "").strip().lower() == "special":
        return name
    m = _BASIC_RE.fullmatch(name)
    return f"Basic {m.group(1).title()} Energy" if m else name


# Für die Start-Migration: schon gespeicherte Karten und Decklisten umbenennen
SQL_PATTERN = r"^\s*(" + "|".join(BASIC_TYPES) + r")\s+energy\s*$"
