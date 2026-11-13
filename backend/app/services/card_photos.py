"""
Exemplare einer Aufnahme teilen sich ihr Foto.

Der Scan legt bei „3 Exemplare" drei Karten an, das Foto lädt die Scan-Seite danach
hoch. Zeigt die Karte das Katalogbild, bekam bisher nur das erste Exemplar das
Scan-Foto — die anderen galten als Karten ohne Foto (kein Neu-Abgleich übers Foto,
kein „Mein Foto"). share() kopiert den Zuschnitt auf die übrigen Exemplare (als
Scan-Foto, nicht angezeigt); das Originalfoto bleibt beim ersten (Trainingsdaten
ohne Doppel). photoless_copies() findet solche Exemplare in Altbeständen.
"""
from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.card import PokemonCard
from app.services.card_images import safe_media_path

SAME_SAVE = timedelta(seconds=20)     # Exemplare einer Speicherung entstehen gemeinsam


def has_photo(card: PokemonCard) -> bool:
    return bool(card.bild_karte_pfad or card.bild_scan_pfad)


def _photo_file(card: PokemonCard) -> Optional[Path]:
    p = safe_media_path(card.bild_scan_pfad or card.bild_karte_pfad)
    return p if p is not None and p.is_file() else None


def share(db: Session, source: PokemonCard, targets: Iterable[PokemonCard]) -> int:
    """Zuschnitt von source auf Exemplare ohne eigenes Foto kopieren. Committet nicht. → Anzahl."""
    src = _photo_file(source)
    if src is None:
        return 0
    images_dir = Path(settings.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for t in targets:
        if t.id == source.id or has_photo(t):
            continue
        dst = images_dir / f"card_{t.id}_scan{src.suffix.lower()}"
        shutil.copyfile(src, dst)
        t.bild_scan_pfad = str(dst.relative_to(images_dir.parent))
        n += 1
    return n


def _key(c: PokemonCard) -> tuple:
    """Gleiche Karte, gleiche Angaben — so legt der Scan Exemplare an."""
    ident = (c.tcgdex_card_id or "").upper() or ((c.kartenname or "").lower(), c.set_edition, c.karten_nr)
    return (c.user_id, ident, c.folierung, c.muster, c.sprache, bool(c.prize_pack), bool(c.erste_edition))


def photoless_copies(db: Session, cards: Optional[list[PokemonCard]] = None) -> dict[int, PokemonCard]:
    """Eigene Karten ohne Foto, deren Exemplar aus derselben Speicherung eins hat → {id: Quelle}."""
    if cards is None:
        cards = db.scalars(select(PokemonCard).where(PokemonCard.besessen.is_(True))
                           .execution_options(all_users=True)).all()
    groups: dict[tuple, list[PokemonCard]] = {}
    for c in cards:
        if c.besessen and c.hinzugefuegt_am is not None:
            groups.setdefault(_key(c), []).append(c)
    out: dict[int, PokemonCard] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        with_photo = [c for c in group if has_photo(c)]
        for c in group:
            if has_photo(c):
                continue
            near = [s for s in with_photo if abs(s.hinzugefuegt_am - c.hinzugefuegt_am) <= SAME_SAVE]
            if near:
                out[c.id] = min(near, key=lambda s: abs(s.hinzugefuegt_am - c.hinzugefuegt_am))
    return out
