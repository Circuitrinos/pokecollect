"""Sortierung mit Umkehren und Untersortierungen für Listen-Endpunkte.

Ein Schlüssel ist eine Liste (Spalte, absteigend?) in seiner Grundrichtung.
Reihenfolge: Hauptschlüssel (reverse kehrt ihn um) → Untersortierungen
(``then``, z.B. "name,-set": zuvor gewählte Sortierungen, Minus = umgekehrt)
→ Standard-Gleichstand des Hauptschlüssels → ``last`` (stabile Reihenfolge
über Seiten). Fehlende Werte stehen immer am Ende.
"""
from typing import Iterable, Sequence

MAX_THEN = 2


def then_pattern(keys: Iterable[str]) -> str:
    alt = "|".join(keys)
    return rf"^(-?({alt})(,-?({alt})){{0,{MAX_THEN - 1}}})?$"


def parse_then(text: str | None) -> list[tuple[str, bool]]:
    steps = []
    for part in (text or "").split(","):
        part = part.strip()
        if part:
            steps.append((part.lstrip("-"), part.startswith("-")))
    return steps[:MAX_THEN]


def order_clauses(keys: dict[str, Sequence[tuple]], sort: str, reverse: bool, then: str | None,
                  default_then: dict[str, Sequence[str]] | None = None, last=None) -> list:
    def cols(key: str, rev: bool):
        return [(c.asc() if desc == rev else c.desc()).nulls_last() for c, desc in keys[key]]

    out = cols(sort, reverse)
    used = {sort}
    for key, rev in parse_then(then):
        if key in keys and key not in used:
            out += cols(key, rev)
            used.add(key)
    for key in (default_then or {}).get(sort, ()):
        if key not in used:
            out += cols(key, reverse)
            used.add(key)
    if last is not None:
        out.append((last.desc() if reverse else last.asc()).nulls_last())
    return out
