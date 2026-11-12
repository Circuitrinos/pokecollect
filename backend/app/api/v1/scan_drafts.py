"""
Scan-Entwürfe: die Prüfseite eines Scans speichern und später (oder an einem
anderen Gerät) fortsetzen. Modell: models/scan_draft.py, Logik:
services/scan_drafts.py.

  GET    /scan/drafts               eigene Entwürfe (neueste zuerst)
  POST   /scan/drafts               neu: Foto + Zustand (multipart)
  GET    /scan/drafts/{id}          Zustand
  GET    /scan/drafts/{id}/photo    das gescannte Foto (nur der Besitzer)
  GET    /scan/drafts/{id}/thumb    Vorschau
  PUT    /scan/drafts/{id}          Zustand ersetzen („gespeichert" bleibt erhalten)
  DELETE /scan/drafts/{id}          fertig / verworfen
"""
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from app.database import get_db
from app.models.scan_draft import ScanDraft
from app.services import scan_drafts as svc

router = APIRouter(prefix="/scan/drafts", tags=["scan"])


class DraftState(BaseModel):
    state: dict


def _out(d: ScanDraft) -> dict:
    try:
        summ = json.loads(d.summary) if d.summary else {}
    except ValueError:
        summ = {}
    return {"id": d.id, "summary": summ,
            "erstellt_am": d.erstellt_am.isoformat() + "Z" if d.erstellt_am else None,
            "aktualisiert_am": d.aktualisiert_am.isoformat() + "Z" if d.aktualisiert_am else None}


def _draft_or_404(db: Session, draft_id: int, *cols) -> ScanDraft:
    q = select(ScanDraft).where(ScanDraft.id == draft_id)
    for c in cols:
        q = q.options(undefer(c))
    d = db.execute(q).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail="Scan-Entwurf nicht gefunden")
    return d


def _state(raw) -> dict:
    try:
        return svc.parse_state(raw if isinstance(raw, (str, bytes)) else json.dumps(raw))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("")
def list_drafts(db: Session = Depends(get_db)):
    rows = db.execute(select(ScanDraft).order_by(ScanDraft.aktualisiert_am.desc())).scalars().all()
    return [_out(d) for d in rows]


@router.post("")
async def create_draft(photo: UploadFile = File(...), state: str = Form(...), db: Session = Depends(get_db)):
    data = await photo.read(svc.MAX_PHOTO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Leere Datei")
    if len(data) > svc.MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Bild zu groß (max. 12 MB)")
    st = _state(state)
    svc.prune_user(db)
    d = ScanDraft(photo=data, photo_type=(photo.content_type or "image/jpeg")[:40], thumb=svc.thumbnail(data))
    svc.store(d, st)
    db.add(d)
    db.commit()
    db.refresh(d)
    return _out(d)


@router.get("/{draft_id}")
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    d = _draft_or_404(db, draft_id, ScanDraft.state)
    return {**_out(d), "state": svc.load_state(d)}


@router.get("/{draft_id}/photo")
def draft_photo(draft_id: int, db: Session = Depends(get_db)):
    d = _draft_or_404(db, draft_id, ScanDraft.photo)
    return Response(d.photo, media_type=d.photo_type or "image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{draft_id}/thumb")
def draft_thumb(draft_id: int, db: Session = Depends(get_db)):
    d = _draft_or_404(db, draft_id, ScanDraft.thumb, ScanDraft.photo)
    return Response(d.thumb or d.photo, media_type="image/jpeg" if d.thumb else (d.photo_type or "image/jpeg"),
                    headers={"Cache-Control": "private, max-age=3600"})


@router.put("/{draft_id}")
def update_draft(draft_id: int, payload: DraftState, db: Session = Depends(get_db)):
    """Zustand ersetzen. Antwort: Übersicht + alle „gespeichert"-Merker (auch die eines anderen Geräts)."""
    st = _state(payload.state)
    d = svc.locked(db, draft_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Scan-Entwurf nicht gefunden")
    st = svc.merge_saved(svc.load_state(d), st)
    svc.store(d, st)
    db.commit()
    return {**_out(d), "saved": svc.saved_map(st)}


@router.delete("/{draft_id}", status_code=204)
def delete_draft(draft_id: int, db: Session = Depends(get_db)):
    d = _draft_or_404(db, draft_id)
    db.delete(d)
    db.commit()
    return Response(status_code=204)
