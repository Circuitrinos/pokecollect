"""
Reverse holo? — Foto einer gescannten Karte gegen ihr Katalogbild (ohne Folie),
über matcher /foil. Das Modell dazu (welcher Unterschied „Reverse Holo" heißt)
kommt aus matcher/tools/foil_eval.py --save und wird im Admin-Bereich
hochgeladen; ohne Modell antwortet der matcher nur {"model": false}.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import catalog_images, tcgdex
from app.services.card_images import safe_media_path

log = logging.getLogger(__name__)
_TIMEOUT = httpx.Timeout(60.0, connect=3.0)


def _url(path: str) -> str:
    return f"{settings.matcher_url.rstrip('/')}{path}"


async def reference(card_id: str) -> Optional[bytes]:
    """Katalogbild der Karte: aus dem Bild-Cache, sonst einmal vom erlaubten CDN (nichts wird gespeichert)."""
    db = SessionLocal()
    try:
        row = db.get(TcgdexCatalog, card_id)
        pfad, url = (row.image_pfad, row.image_url) if row else (None, None)
    finally:
        db.close()
    p = safe_media_path(pfad) if pfad else None
    if p is not None and p.is_file():
        return p.read_bytes()
    if url and tcgdex.is_allowed_image_url(url):
        try:
            return await catalog_images._fetch_image_bytes(url)
        except Exception as exc:  # noqa: BLE001 — ohne Referenz eben keine Folien-Prüfung
            log.info("Folien-Prüfung %s: Katalogbild nicht ladbar (%s)", card_id, exc)
    return None


async def check(card_id: str, crop: bytes) -> dict:
    """→ {"model": False} | {"model": True, "aligned": bool, "score", "hint", "art", "frame"} | {"reference": False}."""
    if not settings.matcher_url:
        return {"model": False}
    ref = await reference(card_id)
    if ref is None:
        return {"model": True, "reference": False}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(_url("/foil"), files={"file": ("crop.jpg", crop, "image/jpeg"),
                                                     "ref": ("ref", ref, "application/octet-stream")})
    if r.status_code == 404:          # älterer matcher ohne /foil
        return {"model": False}
    r.raise_for_status()
    return r.json()


async def model_status() -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        r = await client.get(_url("/foil/model"))
    if r.status_code == 404:
        return {"exists": False, "supported": False}
    r.raise_for_status()
    return r.json()


async def put_model(data: bytes) -> dict:
    """Modelldatei an den matcher; ValueError mit dessen Grund, wenn sie nicht passt."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
        r = await client.put(_url("/foil/model"), files={"file": ("foil_model.json", data, "application/json")})
    if r.status_code == 422:
        raise ValueError(r.json().get("detail") or "invalid model")
    r.raise_for_status()
    return r.json()


async def delete_model() -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        r = await client.delete(_url("/foil/model"))
    r.raise_for_status()
    return r.json()
