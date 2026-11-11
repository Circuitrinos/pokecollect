"""Admin: schwache Scan-Fotos finden und Zuschnitte korrigieren lassen.

GET  /scan-quality             Stand des Auftrags, Zähler, Liste (Standard: schwache)
POST /scan-quality/run         Prüfung starten (nur Geändertes; full=true: alles)
POST /scan-quality/{id}/dismiss  als in Ordnung markieren (bis sich das Foto ändert)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import require_admin
from app.auth_user import AuthUser
from app.database import SessionLocal, engine
from app.models.card import PokemonCard
from app.models.scan_check import ScanCheck
from app.models.user import User
from app.services import scan_export, scan_quality

router = APIRouter(prefix="/scan-quality", tags=["scan-quality"], dependencies=[Depends(require_admin)])


@router.get("")
def overview(
    user: AuthUser = Depends(require_admin),
    status: str = Query("low", pattern="^(low|no_reference|error|dismissed|ok)$"),
    limit: int = Query(200, ge=1, le=1000),
):
    c = PokemonCard.__table__.c
    s = ScanCheck.__table__.c
    with engine.connect() as conn:   # Core: alle Benutzer (Admin-Werkzeug)
        counts = dict(conn.execute(select(s.status, func.count()).group_by(s.status)).all())
        candidates = conn.execute(
            select(func.count()).select_from(PokemonCard.__table__)
            .where(scan_export.has_photo(), c.tcgdex_card_id.isnot(None))
        ).scalar_one()
        rows = conn.execute(
            select(s.card_id, s.status, s.inliers, s.art_inliers, s.corner_err, s.templates,
                   s.suggested_quad.isnot(None).label("has_suggestion"), s.checked_at,
                   c.user_id, c.kartenname, c.englischer_name, c.set_edition, c.karten_nr,
                   c.tcgdex_card_id, c.bild_original_pfad, scan_export.photo().label("photo"),
                   User.username)
            .select_from(ScanCheck.__table__.join(PokemonCard.__table__, c.id == s.card_id)
                         .join(User.__table__, User.id == c.user_id, isouter=True))
            .where(s.status == status)
            .order_by(s.inliers.asc(), s.corner_err.desc().nulls_first(), s.card_id)
            .limit(limit)
        ).mappings().all()
    checked = sum(counts.values())
    return {
        "job": scan_quality.STATE,
        "counts": {**{k: counts.get(k, 0) for k in ("ok", "low", "no_reference", "error", "dismissed")},
                   "unchecked": max(0, candidates - checked), "photos": candidates},
        "thresholds": {"inliers": scan_quality.LOW_INLIERS, "corner_err": scan_quality.LOW_CORNER_ERR},
        "items": [{
            "card_id": r["card_id"], "status": r["status"], "inliers": r["inliers"],
            "art_inliers": r["art_inliers"], "corner_err": r["corner_err"], "templates": r["templates"],
            "has_suggestion": bool(r["has_suggestion"]),
            "checked_at": r["checked_at"].isoformat() if r["checked_at"] else None,
            "own": r["user_id"] == user.id, "username": r["username"],
            "name": r["kartenname"], "english_name": r["englischer_name"], "set": r["set_edition"],
            "number": r["karten_nr"], "tcgdex_card_id": r["tcgdex_card_id"],
            "photo": r["photo"], "has_original": bool(r["bild_original_pfad"]),
        } for r in rows],
    }


@router.post("/run")
async def run(full: bool = False):
    scan_quality.start(full)
    return scan_quality.STATE


@router.post("/{card_id}/dismiss")
def dismiss(card_id: int):
    db = SessionLocal()
    try:
        row = db.get(ScanCheck, card_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Scan-Prüfung nicht gefunden")
        row.status = "dismissed"
        db.commit()
        return {"card_id": card_id, "status": row.status}
    finally:
        db.close()
