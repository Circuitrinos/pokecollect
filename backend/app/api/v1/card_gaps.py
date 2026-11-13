"""Admin: Karten mit Lücken finden (alle Konten), um sie neu abzugleichen.

GET /card-gaps            Zähler je Lücke und die Karten (gap=…: nur diese Lücke)

Lücken (services/rematch.py): print (kein Katalog-Druck), number (fehlt / ohne
Nenner), illustrator, rarity, set, english_name, foiling (eigene Karte ohne
Foliierung), image (kein Bild), differs (steht anders da als im verknüpften Druck).
Neu abgeglichen wird je Karte über GET/POST /cards/{id}/rematch.
"""
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.auth_user import AuthUser
from app.database import get_db
from app.models.user import User
from app.services import rematch
from app.services.bild_spiegel import spiegel_pfad
from app.services.tcgdex import is_allowed_image_url

router = APIRouter(prefix="/card-gaps", tags=["card-gaps"], dependencies=[Depends(require_admin)])


@router.get("")
def overview(
    user: AuthUser = Depends(require_admin),
    gap: Optional[str] = Query(None, pattern="^(" + "|".join(rematch.GAPS) + ")$"),
    owner: str = Query("all", pattern="^(all|mine|others)$"),
    limit: int = Query(300, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    found = rematch.scan_gaps(db)
    if owner != "all":
        found = [(c, g) for c, g in found if (c.user_id == user.id) == (owner == "mine")]
    counts = Counter(g for _c, gaps in found for g in gaps)
    picked = [(c, g) for c, g in found if gap is None or gap in g]
    picked.sort(key=lambda x: (x[0].user_id != user.id, -x[0].id))
    names = dict(db.execute(select(User.id, User.username)).all())
    items = []
    for c, gaps in picked[:limit]:
        items.append({
            "card_id": c.id, "own": c.user_id == user.id, "username": names.get(c.user_id),
            "name": c.kartenname, "english_name": c.englischer_name, "set": c.set_edition,
            "number": c.karten_nr, "language": c.sprache, "foiling": c.folierung,
            "tcgdex_card_id": c.tcgdex_card_id, "gaps": gaps,
            "photo": c.bild_thumbnail_pfad or c.bild_karte_pfad or c.bild_scan_pfad,
            "has_photo": bool(c.bild_scan_pfad or c.bild_karte_pfad),
            "image": spiegel_pfad(c.bild_karte_url) if is_allowed_image_url(c.bild_karte_url) else None,
        })
    return {"total": len(found), "counts": {g: counts.get(g, 0) for g in rematch.GAPS},
            "shown": len(items), "matching": len(picked), "items": items}
