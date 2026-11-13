"""Admin: schwache Scan-Fotos finden und Zuschnitte korrigieren lassen.

GET  /scan-quality             Stand des Auftrags, Zähler, Liste (Standard: schwache)
POST /scan-quality/run         Prüfung starten (nur Geändertes; full=true: alles)
POST /scan-quality/{id}/dismiss  als in Ordnung markieren (bis sich das Foto ändert)
POST /scan-quality/{id}/check    diese Karte sofort neu prüfen (nach dem Korrigieren)
POST /scan-quality/{id}/try      einen Zuschnitt prüfen, ohne ihn zu speichern („Alle korrigieren")
GET  /scan-quality/stamps        Prize-Pack-Fotos zum Markieren des Play!-Stempels (Trainingsdaten)
GET  /scan-quality/stamps/template   gelernte Stempel-Vorlage (Vorschläge beim Markieren)
POST /scan-quality/stamps/template   Vorlage neu lernen (markierte + übrige Prize-Pack-Fotos)
"""

import json
import logging

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select

from app.api.deps import require_admin
from app.auth_user import AuthUser
from app.database import SessionLocal, engine
from app.models.card import PokemonCard
from app.models.scan_check import ScanCheck
from app.models.tcgdex_catalog import TcgdexCatalog
from app.models.user import User
from app.services import scan_export, scan_quality
from app.services.bild_spiegel import spiegel_pfad
from app.services.tcgdex import is_allowed_image_url


def _quad(raw):
    try:
        q = json.loads(raw) if raw else None
    except ValueError:
        return None
    return q if isinstance(q, list) and len(q) == 4 else None


MAX_TRY_BYTES = 15 * 1024 * 1024


def _valid_quad(raw: str):
    """4×[x,y], normiert (etwas Rand erlaubt) — sonst None."""
    q = _quad(raw)
    try:
        pts = [[float(x), float(y)] for x, y in q] if q else None
    except (TypeError, ValueError):
        return None
    if not pts or not all(-0.5 <= v <= 1.5 for p in pts for v in p):
        return None
    return pts

log = logging.getLogger(__name__)
router = APIRouter(prefix="/scan-quality", tags=["scan-quality"], dependencies=[Depends(require_admin)])


@router.get("")
def overview(
    user: AuthUser = Depends(require_admin),
    status: str = Query("low", pattern="^(low|no_reference|error|dismissed|ok)$"),
    limit: int = Query(200, ge=1, le=1000),
):
    c = PokemonCard.__table__.c
    s = ScanCheck.__table__.c
    k = TcgdexCatalog.__table__.c
    with engine.connect() as conn:   # Core: alle Benutzer (Admin-Werkzeug)
        counts = dict(conn.execute(select(s.status, func.count()).group_by(s.status)).all())
        candidates = conn.execute(
            select(func.count()).select_from(PokemonCard.__table__)
            .where(scan_export.has_photo(), c.tcgdex_card_id.isnot(None))
        ).scalar_one()
        rows = conn.execute(
            select(s.card_id, s.status, s.inliers, s.art_inliers, s.corner_err, s.templates,
                   s.suggested_quad, s.checked_at,
                   c.user_id, c.kartenname, c.englischer_name, c.set_edition, c.karten_nr,
                   c.tcgdex_card_id, c.bild_original_pfad, c.bild_quad, c.bild_karte_url,
                   scan_export.photo().label("photo"), k.image_url.label("catalog_url"),
                   User.username)
            .select_from(ScanCheck.__table__.join(PokemonCard.__table__, c.id == s.card_id)
                         .join(User.__table__, User.id == c.user_id, isouter=True)
                         .join(TcgdexCatalog.__table__, k.card_id == c.tcgdex_card_id, isouter=True))
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
            "has_suggestion": r["suggested_quad"] is not None,
            "suggested_quad": _quad(r["suggested_quad"]), "quad": _quad(r["bild_quad"]),
            "original": r["bild_original_pfad"],
            # Katalogbild zum Vergleich (gespiegelt, wie auf der Kartenseite)
            "catalog_image": spiegel_pfad(r["catalog_url"] if is_allowed_image_url(r["catalog_url"])
                                          else r["bild_karte_url"]),
            "checked_at": r["checked_at"].isoformat() if r["checked_at"] else None,
            "own": r["user_id"] == user.id, "username": r["username"],
            "name": r["kartenname"], "english_name": r["englischer_name"], "set": r["set_edition"],
            "number": r["karten_nr"], "tcgdex_card_id": r["tcgdex_card_id"],
            "photo": r["photo"], "has_original": bool(r["bild_original_pfad"]),
        } for r in rows],
    }


@router.get("/stamps")
def stamps(
    user: AuthUser = Depends(require_admin),
    marked: bool = False,
    limit: int = Query(200, ge=1, le=1000),
):
    """Prize-Pack-Karten mit eigenem Foto: Stempel markiert oder noch nicht (alle Konten)."""
    c = PokemonCard.__table__.c
    photo = func.coalesce(c.bild_scan_pfad, c.bild_karte_pfad)
    base = (c.prize_pack.is_(True)) & photo.isnot(None)
    with engine.connect() as conn:   # Core: alle Benutzer (Admin-Werkzeug)
        done = conn.execute(select(func.count()).select_from(PokemonCard.__table__)
                            .where(base, c.stamp_quad.isnot(None))).scalar_one()
        total = conn.execute(select(func.count()).select_from(PokemonCard.__table__).where(base)).scalar_one()
        rows = conn.execute(
            select(c.id, c.user_id, c.kartenname, c.englischer_name, c.set_edition, c.karten_nr,
                   c.tcgdex_card_id, c.stamp_quad, c.aktualisiert_am, photo.label("photo"), User.username)
            .select_from(PokemonCard.__table__.join(User.__table__, User.id == c.user_id, isouter=True))
            .where(base, c.stamp_quad.isnot(None) if marked else c.stamp_quad.is_(None))
            .order_by((c.user_id == user.id).desc(), c.id.desc())
            .limit(limit)
        ).mappings().all()
    return {
        "counts": {"photos": total, "marked": done, "unmarked": total - done},
        "items": [{
            "card_id": r["id"], "own": r["user_id"] == user.id, "username": r["username"],
            "name": r["kartenname"], "english_name": r["englischer_name"], "set": r["set_edition"],
            "number": r["karten_nr"], "tcgdex_card_id": r["tcgdex_card_id"], "photo": r["photo"],
            "stamp_quad": _quad(r["stamp_quad"]),
            "updated_at": r["aktualisiert_am"].isoformat() if r["aktualisiert_am"] else None,
        } for r in rows],
    }


@router.get("/stamps/template")
async def stamp_template():
    """Gelernte Play!-Stempel-Vorlage im Bild-Abgleich (Vorschläge beim Markieren)."""
    from app.services import stamp_finder

    try:
        return await stamp_finder.status()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")


@router.get("/foil/model")
async def foil_model():
    """Reverse-Holo-Modell im Bild-Abgleich (aus matcher/tools/foil_eval.py --save)."""
    from app.services import foil_check

    try:
        return await foil_check.model_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")


@router.put("/foil/model")
async def put_foil_model(file: UploadFile = File(...)):
    """foil_model.json hochladen — ab dann fragt der Scan bei normalen Karten „Reverse Holo?"."""
    from app.services import foil_check

    data = await file.read(256 * 1024 + 1)
    if not data or len(data) > 256 * 1024:
        raise HTTPException(status_code=400, detail="Keine gültige Modelldatei")
    try:
        return await foil_check.put_model(data)
    except ValueError as e:
        log.info("Folien-Modell abgelehnt: %s", e)
        raise HTTPException(status_code=422, detail="Keine gültige Modelldatei")
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")


@router.delete("/foil/model")
async def delete_foil_model():
    from app.services import foil_check

    try:
        return await foil_check.delete_model()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")


@router.post("/stamps/template")
async def learn_stamp_template():
    """Vorlage aus den markierten Stempeln und den übrigen Prize-Pack-Fotos (gegen das Katalogbild) lernen."""
    from app.services import stamp_finder

    try:
        return await stamp_finder.learn()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")


@router.post("/run")
async def run(full: bool = False):
    scan_quality.start(full)
    return scan_quality.STATE


@router.post("/{card_id}/check")
async def check(card_id: int):
    try:
        res = await scan_quality.check_one(card_id)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")
    if res is None:
        raise HTTPException(status_code=404, detail="Scan-Prüfung nicht gefunden")
    return res


@router.post("/{card_id}/try")
async def try_crop(card_id: int, file: UploadFile = File(...), quad: str = Form(...)):
    quad_pts = _valid_quad(quad)
    if quad_pts is None:
        raise HTTPException(status_code=422, detail="Ungültige Ecken")
    data = await file.read(MAX_TRY_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Leere Datei")
    if len(data) > MAX_TRY_BYTES:
        raise HTTPException(status_code=413, detail="Bild zu groß (max. 15 MB)")
    try:
        res = await scan_quality.try_crop(card_id, data, quad_pts)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")
    if res is None:
        raise HTTPException(status_code=404, detail="Scan-Prüfung nicht gefunden")
    return res


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
