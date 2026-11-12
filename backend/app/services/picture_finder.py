"""Bilder für Katalogkarten finden (Admin-Werkzeug „Bild finden").

Seltene Karten (Promos wie die McDonald's-Sets) haben bei TCGdex oft kein
Bild. Vorschläge kommen aus offenen Quellen:

  - TCGdex in anderen Sprachen (dieselbe Karten-ID),
  - pokemontcg.io (kostenlose Katalog-API, Bilder auf images.pokemontcg.io),
  - pokemon.com (die üblichen Bildadressen, ungeprüft — lädt eins nicht,
    blendet das Frontend es aus).

Bilder aus der Websuche (Google/Bing) holt das Backend NICHT selbst: der
Nutzer kopiert ein Bild und fügt es ein (oder lädt die Datei hoch); das Bild
wird dann lokal gespeichert. So gibt es keinen Server-Abruf beliebiger
Adressen.

Ein gewähltes Bild gilt für die Katalogkarte (geteilt, daher nur Admins) und
für eigene Karten dieser ID, die noch kein Bild haben (alle Benutzer).
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageOps
from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine
from app.models.card import PokemonCard
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import pokemon_com, tcgdex

log = logging.getLogger(__name__)

PTCG_API = "https://api.pokemontcg.io/v2/cards"
KATALOG = "katalog:"          # virtuelle Bildquelle: lokal gespeichertes Katalogbild (Spiegel löst sie auf)
_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
MANUAL_DIR = "catalog_manual"
MAX_BYTES = 15 * 1024 * 1024
_LANGS = ("en", "de", "fr", "es", "it", "pt")


def katalog_url(card_id: str) -> str:
    return f"{KATALOG}{card_id}"


def katalog_id(url: Optional[str]) -> Optional[str]:
    """Karten-ID aus einer katalog:-Adresse (None, wenn keine oder ungültig)."""
    if not url or not url.startswith(KATALOG):
        return None
    cid = url[len(KATALOG):]
    return cid if _ID.fullmatch(cid) else None


def source_of(url: str) -> str:
    host = (httpx.URL(url).host or "") if url.startswith("https://") else ""
    return {"assets.tcgdex.net": "tcgdex", "images.pokemontcg.io": "pokemontcg",
            "tcgplayer-cdn.tcgplayer.com": "tcgplayer", "www.pokemon.com": "pokemon.com",
            "assets.pokemon.com": "pokemon.com"}.get(host, "manual")


def _number(local_id: Optional[str]) -> Optional[str]:
    """Aufgedruckte Nummer ohne führende Nullen (pokemontcg.io schreibt „1", nicht „001")."""
    if not local_id:
        return None
    s = local_id.strip().lstrip("0")
    return s or "0"


async def _tcgdex_langs(row: TcgdexCatalog) -> list[dict]:
    async def one(lang: str):
        try:
            tc = await tcgdex.get_card(row.card_id, lang)
        except Exception:   # Quelle nicht erreichbar: einfach keine Vorschläge
            return None
        url = tcgdex.image_url(tc.image) if tc and tc.image else None
        return {"url": url, "source": "tcgdex", "label": f"TCGdex · {lang.upper()}"} if url else None
    found = await asyncio.gather(*(one(lang) for lang in _LANGS))
    return [f for f in found if f]


async def _pokemontcg(row: TcgdexCatalog) -> list[dict]:
    name = (row.name_en or row.name or "").replace('"', "").strip()
    if not name:
        return []
    num = _number(row.local_id)
    queries = [f'name:"{name}" number:{num}'] if num and re.fullmatch(r"[A-Za-z0-9]+", num) else []
    queries.append(f'name:"{name}"')
    headers = {"X-Api-Key": settings.pokemontcg_api_key} if settings.pokemontcg_api_key else {}
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        for q in queries:
            try:
                r = await client.get(PTCG_API, params={"q": q, "pageSize": 24,
                                                       "select": "id,name,number,set,images"})
            except httpx.HTTPError as exc:
                log.info("pokemontcg.io nicht erreichbar: %s", exc)
                return out
            if r.status_code != 200:
                return out
            for c in (r.json().get("data") or []):
                imgs = c.get("images") or {}
                url = imgs.get("large") or imgs.get("small")
                if not url:
                    continue
                s = c.get("set") or {}
                out.append({"url": url, "source": "pokemontcg",
                            "label": f"{s.get('name') or s.get('id') or '?'} · {c.get('number') or '?'}",
                            "match": bool(num) and (c.get("number") or "").lstrip("0") == num})
            if out:
                break
    out.sort(key=lambda x: not x["match"])
    for x in out:
        x.pop("match", None)
    return out[:18]


async def candidates(row: TcgdexCatalog) -> list[dict]:
    """Vorschläge aus offenen Quellen, ohne Doppelte; nur erlaubte Bild-Hosts."""
    tc_langs, ptcg = await asyncio.gather(_tcgdex_langs(row), _pokemontcg(row))
    pcom = [{"url": u, "source": "pokemon.com", "label": "pokemon.com"}
            for u in pokemon_com.candidate_urls(row.set_id, row.local_id)[:2]] if row.region == "west" else []
    seen: set[str] = set()
    out = []
    for c in [*tc_langs, *ptcg, *pcom]:
        if c["url"] in seen or not tcgdex.is_allowed_image_url(c["url"]):
            continue
        seen.add(c["url"])
        out.append(c)
    return out


def _propagate(card_id: str, url: str) -> int:
    """Eigene Karten dieser ID ohne Bild bekommen das neue Katalogbild (alle Benutzer)."""
    with engine.begin() as conn:   # Core: bewusst ohne Benutzer-Filter (der Katalog ist geteilt)
        res = conn.execute(
            update(PokemonCard.__table__)
            .where(func.upper(PokemonCard.__table__.c.tcgdex_card_id) == card_id.upper())
            .where(or_(PokemonCard.__table__.c.bild_karte_url.is_(None),
                       PokemonCard.__table__.c.bild_karte_url.like(f"{KATALOG}%")))
            .values(bild_karte_url=url)
        )
        return res.rowcount or 0


def use_url(db: Session, row: TcgdexCatalog, url: str) -> int:
    """Bild von einem erlaubten Host übernehmen (vorher per Spiegel geprüft)."""
    row.image_url = url
    row.image_source = "manual" if source_of(url) == "pokemon.com" else source_of(url)
    row.image_pfad = None      # alten lokalen Cache nicht mehr bevorzugen
    db.commit()
    return _propagate(row.card_id, url)


def store_upload(db: Session, row: TcgdexCatalog, data: bytes) -> int:
    """Eingefügtes/hochgeladenes Bild prüfen, als JPEG speichern und als Katalogbild setzen."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("size")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            im = ImageOps.exif_transpose(im).convert("RGB")
            if max(im.size) > 1600:
                im.thumbnail((1600, 1600))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=90)
    except Exception as exc:
        raise ValueError("image") from exc
    folder = Path(settings.images_dir) / MANUAL_DIR
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", row.card_id)[:64]
    target = folder / f"{safe}.jpg"
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(out.getvalue())
    tmp.replace(target)
    row.image_pfad = str(target.relative_to(Path(settings.images_dir).parent))
    row.image_source = "manual"
    db.commit()
    return _propagate(row.card_id, katalog_url(row.card_id))


def manual_file(card_id: str) -> Optional[Path]:
    """Datei eines lokal gespeicherten Katalogbilds (für den Bild-Spiegel)."""
    from app.database import SessionLocal
    from app.services.card_images import safe_media_path

    db = SessionLocal()
    try:
        row = db.get(TcgdexCatalog, card_id)
        if row is None:
            row = db.query(TcgdexCatalog).filter(func.upper(TcgdexCatalog.card_id) == card_id.upper()).first()
        p = safe_media_path(row.image_pfad) if row is not None else None
        return p if p is not None and p.is_file() else None
    finally:
        db.close()


OUTLINE_MAX_BYTES = 12 * 1024 * 1024    # matcher MAX_BYTES


def same_name_refs(db: Session, row: TcgdexCatalog, limit: int = 24) -> list[str]:
    """Andere Drucke gleichen Namens mit Bild — Referenzen fürs Zuschneiden (Promos
    und Reprints teilen meist das Artwork). Gleiches Set zuerst, dann westlich."""
    from sqlalchemy import case, select

    names = [n.lower() for n in (row.name_en, row.name) if n]
    if not names:
        return []
    q = (select(TcgdexCatalog.card_id)
         .where(TcgdexCatalog.card_id != row.card_id)
         .where(or_(func.lower(TcgdexCatalog.name_en).in_(names), func.lower(TcgdexCatalog.name).in_(names)))
         .where(or_(TcgdexCatalog.image_url.isnot(None), TcgdexCatalog.image_pfad.isnot(None)))
         .order_by(case((TcgdexCatalog.set_id == row.set_id, 0), else_=1),
                   case((TcgdexCatalog.region == "west", 0), else_=1), TcgdexCatalog.card_id.desc())
         .limit(limit))
    return list(db.scalars(q).all())


async def outline(data: bytes, quad: Optional[list], refs: Optional[list[str]] = None) -> dict:
    """Grobe Ecken eines Bilds (Zuschneiden im Bild-Finder) an die echten
    Kartenkanten anlegen — matcher /outline: über das Artwork gleichnamiger
    Drucke (refs), sonst über einen typischen Kartenrahmen (frame_ok: die
    Kanten sehen wie eine Karte aus) und zuletzt nur über die Kanten.
    → {"quad": Ecken (0..1) oder None, "method": "reference" | "frame" | "edges",
    "frame_ok": bool, "reference": Katalog-ID};
    wirft httpx.HTTPError, wenn der Dienst nicht antwortet."""
    import json

    form = {"quad": json.dumps(quad)} if quad else {}
    if refs:
        form["refs"] = ",".join(refs)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        r = await client.post(f"{settings.matcher_url.rstrip('/')}/outline",
                              files={"file": ("picture.jpg", data, "image/jpeg")}, data=form)
    if r.status_code != 200:
        raise httpx.HTTPError(f"matcher {r.status_code}")
    body = r.json()
    q = body.get("quad")
    return {"quad": q if isinstance(q, list) and len(q) == 4 else None,
            "method": body.get("method") or "edges", "frame_ok": bool(body.get("frame_ok")),
            "reference": body.get("reference")}
