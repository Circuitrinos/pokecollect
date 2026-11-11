"""Schwache Scans finden (Admin-Werkzeug).

Jedes gespeicherte Scan-Foto mit bestätigter Karte wird vom Bild-Abgleich
(matcher /verify) gegen die KATALOGbilder dieser Karte geprüft — nicht gegen
das Foto selbst, das ebenfalls als Referenz gelernt ist. Wenige Treffer
(Inlier) heißen meist: schlechter Zuschnitt, falsche Karte oder ein
unbrauchbares Foto; ein großer Eckfehler heißt: der Zuschnitt sitzt schief.
Mit Originalfoto und gespeicherten Ecken schlägt der Abgleich bessere Ecken vor
(Kartenseite → „Zuschnitt korrigieren").

Läuft als Hintergrundauftrag, ein Foto nach dem anderen; geprüft wird nur, was
sich seit der letzten Prüfung geändert hat (photo_key), außer auf Wunsch alles.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy import delete, select

from app.config import settings
from app.database import SessionLocal, engine
from app.models.card import PokemonCard
from app.models.scan_check import ScanCheck
from app.services import scan_export
from app.services.card_images import safe_media_path

log = logging.getLogger(__name__)

# Schwellen: unter LOW_INLIERS Treffern oder ab LOW_CORNER_ERR Eckfehler
# (Anteil der Kartendiagonale) gilt ein Foto als schwach. Ein sauberer
# Zuschnitt liegt meist unter 0.02.
LOW_INLIERS = 25
LOW_CORNER_ERR = 0.04

STATE: dict = {"running": False, "done": 0, "total": 0, "low": 0, "started_at": None,
               "finished_at": None, "error": None}
_task: Optional[asyncio.Task] = None


def classify(res: dict) -> str:
    if not res.get("templates"):
        return "no_reference"
    err = res.get("corner_err")
    if (res.get("inliers") or 0) < LOW_INLIERS or err is None or err >= LOW_CORNER_ERR or not res.get("geo_ok"):
        return "low"
    return "ok"


def _photo_key(photo: str, quad: Optional[str], original: Optional[str]) -> str:
    p = safe_media_path(photo)
    try:
        mtime = int(p.stat().st_mtime) if p else 0
    except OSError:
        mtime = 0
    return f"{photo}|{mtime}|{original or ''}|{quad or ''}"


def _rows(card_id: Optional[int] = None) -> list[dict]:
    c = PokemonCard.__table__.c
    q = (select(c.id, c.tcgdex_card_id, scan_export.photo().label("photo"), c.bild_original_pfad, c.bild_quad)
         .where(scan_export.has_photo(), c.tcgdex_card_id.isnot(None)).order_by(c.id))
    if card_id is not None:
        q = q.where(c.id == card_id)
    with engine.connect() as conn:   # Core: alle Benutzer (Admin-Werkzeug)
        return [dict(r) for r in conn.execute(q).mappings().all()]


async def verify_one(client: httpx.AsyncClient, row: dict) -> Optional[dict]:
    """Ein Foto beim Bild-Abgleich prüfen; None, wenn der Dienst nicht antwortet."""
    crop = safe_media_path(row["photo"])
    if crop is None or not crop.is_file():
        return {"templates": 0, "error": "missing"}
    files = {"file": (crop.name, crop.read_bytes(), mimetypes.guess_type(crop.name)[0] or "image/jpeg")}
    orig = safe_media_path(row.get("bild_original_pfad"))
    data = {"card_id": row["tcgdex_card_id"]}
    if orig is not None and orig.is_file() and row.get("bild_quad"):
        files["original"] = (orig.name, orig.read_bytes(), mimetypes.guess_type(orig.name)[0] or "image/jpeg")
        data["quad"] = row["bild_quad"]
    r = await client.post(f"{settings.matcher_url.rstrip('/')}/verify", files=files, data=data)
    if r.status_code != 200:
        log.warning("Scan-Prüfung: Bild-Abgleich antwortete %s für Karte %s", r.status_code, row["id"])
        return None
    return r.json()


def _store(card_id: int, key: str, res: dict) -> str:
    status = "error" if res.get("error") else classify(res)
    db = SessionLocal()
    try:
        row = db.get(ScanCheck, card_id) or ScanCheck(card_id=card_id)
        row.checked_at = datetime.utcnow()
        row.photo_key = key
        row.status = status
        row.templates = int(res.get("templates") or 0)
        row.inliers = int(res.get("inliers") or 0)
        row.art_inliers = int(res.get("art_inliers") or 0)
        row.corner_err = res.get("corner_err")
        row.geo_ok = bool(res.get("geo_ok"))
        sq = res.get("suggested_quad")
        row.suggested_quad = json.dumps(sq) if sq else None
        db.merge(row)
        db.commit()
    finally:
        db.close()
    return status


async def run(full: bool = False) -> None:
    STATE.update(running=True, done=0, total=0, low=0, started_at=datetime.utcnow().isoformat(),
                 finished_at=None, error=None)
    try:
        rows = await asyncio.to_thread(_rows)
        db = SessionLocal()
        try:
            known = {c.card_id: (c.photo_key, c.status) for c in db.scalars(select(ScanCheck)).all()}
        finally:
            db.close()
        todo = []
        for r in rows:
            key = _photo_key(r["photo"], r["bild_quad"], r["bild_original_pfad"])
            prev = known.get(r["id"])
            if full or prev is None or prev[0] != key:
                todo.append((r, key))
        STATE["total"] = len(todo)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
            for r, key in todo:
                try:
                    res = await verify_one(client, r)
                except httpx.HTTPError as exc:
                    STATE["error"] = f"matcher: {exc.__class__.__name__}"
                    break
                if res is None:
                    STATE["error"] = "matcher"
                    break
                if await asyncio.to_thread(_store, r["id"], key, res) == "low":
                    STATE["low"] += 1
                STATE["done"] += 1
    except Exception as exc:   # Hintergrundauftrag: nie still sterben
        log.exception("Scan-Prüfung fehlgeschlagen")
        STATE["error"] = str(exc)[:200]
    finally:
        STATE.update(running=False, finished_at=datetime.utcnow().isoformat())
        log.info("Scan-Prüfung: %d/%d geprüft, %d schwach%s", STATE["done"], STATE["total"], STATE["low"],
                 f", Fehler: {STATE['error']}" if STATE["error"] else "")


async def check_one(card_id: int) -> Optional[dict]:
    """Eine Karte sofort (neu) prüfen — nach „Zuschnitt korrigieren" in der Liste.
    None: Karte ohne prüfbares Foto; wirft httpx.HTTPError, wenn der Dienst fehlt."""
    rows = await asyncio.to_thread(_rows, card_id)
    if not rows:
        return None
    r = rows[0]
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        res = await verify_one(client, r)
    if res is None:
        raise httpx.HTTPError("matcher")
    key = _photo_key(r["photo"], r["bild_quad"], r["bild_original_pfad"])
    status = await asyncio.to_thread(_store, card_id, key, res)
    return {"status": status, "inliers": int(res.get("inliers") or 0), "corner_err": res.get("corner_err"),
            "suggested_quad": res.get("suggested_quad"), "photo": r["photo"]}


def start(full: bool = False) -> bool:
    """Auftrag starten; False, wenn schon einer läuft."""
    global _task
    if STATE["running"]:
        return False
    STATE["running"] = True
    _task = asyncio.get_running_loop().create_task(run(full))
    return True


def forget(db, card_id: int) -> None:
    """Foto/Ecken geändert: alte Prüfung verwerfen (wird beim nächsten Lauf neu geprüft)."""
    db.execute(delete(ScanCheck).where(ScanCheck.card_id == card_id))
