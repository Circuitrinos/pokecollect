"""
Play!-Stempel (Prize Pack) im Kartenfoto vorschlagen — über matcher /stamp.

Die Vorlage lernt der Bild-Abgleich aus der eigenen Sammlung (Admin →
„Play!-Stempel" → „Vorlage lernen"): von Hand markierte Stempel und, für noch
nicht markierte Prize-Pack-Karten, die Stelle, an der das Foto vom Katalogbild
(ohne Stempel) abweicht. Hier werden nur die Proben eingesammelt und
weitergereicht; die Bildarbeit macht der matcher.
"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import func, select

from app.config import settings
from app.database import engine
from app.models.card import PokemonCard
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services.card_images import safe_media_path

MAX_SAMPLES = 300        # = matcher STAMP_MAX_SAMPLES


def _url(path: str) -> str:
    return f"{settings.matcher_url.rstrip('/')}{path}"


def _box(raw: Optional[str]) -> Optional[list[float]]:
    """Markierte Ecken (Kartenkoordinaten) → umschließendes Rechteck [x0, y0, x1, y1]."""
    try:
        q = json.loads(raw) if raw else None
        xs, ys = [float(p[0]) for p in q], [float(p[1]) for p in q]
    except (ValueError, TypeError, IndexError):
        return None
    if len(xs) != 4:
        return None
    box = [max(0.0, min(xs)), max(0.0, min(ys)), min(1.0, max(xs)), min(1.0, max(ys))]
    return box if box[2] - box[0] > 0.01 and box[3] - box[1] > 0.01 else None


def samples() -> list[dict]:
    """Prize-Pack-Fotos aller Konten: markierte zuerst, sonst mit Katalogbild zum Vergleichen."""
    c = PokemonCard.__table__.c
    k = TcgdexCatalog.__table__.c
    photo = func.coalesce(c.bild_scan_pfad, c.bild_karte_pfad)
    with engine.connect() as conn:   # Core: alle Benutzer (Admin-Werkzeug)
        rows = conn.execute(
            select(c.id, c.stamp_quad, photo.label("photo"), k.image_pfad.label("ref"))
            .select_from(PokemonCard.__table__.outerjoin(TcgdexCatalog.__table__, k.card_id == c.tcgdex_card_id))
            .where(c.prize_pack.is_(True), photo.isnot(None))
            .order_by(c.stamp_quad.is_(None), c.id.desc())
        ).mappings().all()
    out = []
    for r in rows:
        p = safe_media_path(r["photo"])
        if p is None or not p.is_file():
            continue
        box = _box(r["stamp_quad"])
        ref = None if box else safe_media_path(r["ref"])
        if box is None and (ref is None or not ref.is_file()):
            continue
        out.append({"card_id": r["id"], "photo": p, "box": box, "ref": ref})
        if len(out) >= MAX_SAMPLES:
            break
    return out


def _file(field: str, path: Path) -> tuple:
    return (field, (path.name, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/jpeg"))


async def learn() -> dict:
    """Vorlage neu lernen. → Antwort des matchers (samples, marked, guessed, …);
    ValueError mit Grund bei zu wenigen Proben; httpx.HTTPError, wenn der Dienst fehlt."""
    import asyncio

    found = await asyncio.to_thread(samples)
    if len(found) < 3:
        raise ValueError("Zu wenige Stempel zum Lernen — mindestens 3 von Hand markieren")
    files, refs, spec = [], [], []
    for i, s in enumerate(found):
        files.append(_file("photos", s["photo"]))
        entry = {"photo": i, "box": s["box"], "ref": None}
        if s["ref"] is not None:
            entry["ref"] = len(refs)
            refs.append(_file("refs", s["ref"]))
        spec.append(entry)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
        r = await client.post(_url("/stamp/learn"), files=files + refs, data={"samples": json.dumps(spec)})
    if r.status_code == 422:
        raise ValueError("Zu wenige Stempel zum Lernen — mindestens 3 von Hand markieren")
    if r.status_code != 200:
        raise httpx.HTTPError(f"matcher {r.status_code}")
    return r.json()


async def status() -> dict:
    """Gelernte Vorlage: {"exists", "samples", "marked", "built_at"}; httpx.HTTPError ohne Dienst."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        r = await client.get(_url("/stamp/template"))
    if r.status_code != 200:
        raise httpx.HTTPError(f"matcher {r.status_code}")
    return r.json()


async def find(photo: Path, hint: Optional[list]) -> dict:
    """Stempel im entzerrten Kartenfoto (um `hint` herum, wenn gegeben).
    → {"quad" (Kartenkoordinaten) oder None, "score", "ok", "template"}."""
    form = {"quad": json.dumps(hint)} if hint else {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        r = await client.post(_url("/stamp"), files=[_file("file", photo)], data=form)
    if r.status_code != 200:
        raise httpx.HTTPError(f"matcher {r.status_code}")
    body = r.json()
    q = body.get("quad")
    return {"quad": q if isinstance(q, list) and len(q) == 4 else None, "score": body.get("score"),
            "ok": bool(body.get("ok")), "template": bool(body.get("template"))}
