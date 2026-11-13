"""
Karten-Scan (Web: Webcam/Foto-Upload).

POST /api/v1/scan          Bild + Modus → Kandidaten (Hybrid: Gemini, sonst OCR)
POST /api/v1/scan/commit   bestätigte Karten ablegen (Pokédex oder Sammlung)
GET  /api/v1/scan/status   welche Engine aktiv ist (für die UI)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.card import PokemonCard
from app.models.collection import Collection, collection_cards
from app.models.deck import Deck
from app.models.gemini_usage import GeminiUsage
from app.models.setting import AppSetting
from app.schemas.scan import (
    ScanCandidate, ScanCommitRequest, ScanCommitResponse, ScanMode,
    ScanRawRead, ScanResponse,
)
from app.services import decks as decks_svc
from app.services import regulation
from app.services.card_creation import create_owned_card
from app.services.catalog_lookup import catalog_row_for
from app.services.scan import crop_hints, gemini, local_match, model_catalog, ocr, vlm
from app.services.scan.rate_window import gemini_rate
from app.services.scan.resolver import resolve_one, resolve_reads
from app.services.tcgdex import is_allowed_image_url

log = logging.getLogger(__name__)
router = APIRouter(prefix="/scan", tags=["scan"])

_MAX_BYTES = 12 * 1024 * 1024  # 12 MB


def _setting(db: Session, key: str, env_default: str) -> str:
    """DB-Einstellung mit Fallback auf .env/Config."""
    row = db.get(AppSetting, key)
    return (row.value if row and row.value else env_default) or ""


def _gemini_config(db: Session) -> tuple[str, str]:
    key = _setting(db, "gemini_api_key", settings.gemini_api_key)
    model = _setting(db, "gemini_model", settings.gemini_model)
    return key, model


# ── Scan-Stufe B: pluggbares Lese-Modell (Issue #57) ─────────────────────────
# OpenAI und OpenRouter teilen die OpenAI-kompatible chat/completions-API und
# unterscheiden sich nur in der base_url. Gemini spricht seine eigene REST-API.
_OPENAI_BASE = "https://api.openai.com/v1"
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_READER_PROVIDERS = {"gemini", "openai", "openrouter", "local", "ocr"}


def _reader_config(db: Session) -> tuple[str, str, str, str | None]:
    """
    (provider, api_key, model, base_url) der aktiven Lese-Stufe. Unbekannte
    Provider fallen sicher auf "gemini" (rückwärtskompatibler Default) zurück;
    base_url ist None für Gemini/OCR (kein OpenAI-kompatibler Endpunkt).
    """
    provider = (_setting(db, "scan_reader_provider", "gemini") or "gemini").lower()
    if provider not in _READER_PROVIDERS:
        provider = "gemini"
    if provider == "openai":
        return (provider, _setting(db, "openai_api_key", ""),
                _setting(db, "openai_model", "gpt-4o-mini") or "gpt-4o-mini",
                _OPENAI_BASE)
    if provider == "openrouter":
        return (provider, _setting(db, "openrouter_api_key", ""),
                _setting(db, "openrouter_model", "google/gemini-2.5-flash")
                or "google/gemini-2.5-flash",
                _OPENROUTER_BASE)
    if provider == "gemini":
        key, model = _gemini_config(db)
        return provider, key, model, None
    if provider == "local":  # Bild-Abgleich-Dienst im selben Stack, kein Key
        return provider, "", "", settings.matcher_url
    return "ocr", "", "", None


# Menschenlesbarer Hinweis je harter Fehlerart eines OpenAI-/OpenRouter-Lese-
# modells (Issue #57). Das UI zeigt per hinweis_art eigene i18n-Texte.
_READER_HINWEIS = {
    vlm.FEHLER_RATE: "Rate-Limit des Lesemodells erreicht – Erkennung über lokale OCR.",
    vlm.FEHLER_KEY: "API-Key des Lesemodells ungültig oder fehlt – Erkennung über lokale OCR.",
    vlm.FEHLER_VLM: "Lesemodell nicht erreichbar – Erkennung über lokale OCR.",
}


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _gemini_limit_reached(db: Session) -> bool:
    """
    Manuelles Tageslimit (Setting gemini_daily_limit) DURCHSETZEN (Issue #3).
    0 = unbegrenzt (bisherige Nur-Anzeige-Semantik bleibt für 0 erhalten).
    """
    limit = int(_setting(db, "gemini_daily_limit", "0") or 0)
    if limit <= 0:
        return False
    row = db.get(GeminiUsage, _today_utc())
    return bool(row and (row.requests or 0) >= limit)


def _record_usage(db: Session, total_tokens: int) -> None:
    """Zählt eine Gemini-Anfrage + Tokens für den aktuellen UTC-Tag."""
    try:
        day = _today_utc()
        row = db.get(GeminiUsage, day)
        if row:
            row.requests = (row.requests or 0) + 1
            row.tokens = (row.tokens or 0) + int(total_tokens or 0)
        else:
            db.add(GeminiUsage(day=day, requests=1, tokens=int(total_tokens or 0)))
        db.commit()
    except Exception as exc:  # Nutzung tracken darf den Scan nie stören
        db.rollback()
        log.debug("Gemini-Usage konnte nicht gezählt werden: %s", exc)


@router.get("/models")
async def scan_models(provider: str = "openrouter"):
    """
    Bild-fähige Modell-Vorschläge für die Combobox der Lese-Stufe (Issue #57).
    OpenRouter live (öffentlich, ohne Key); OpenAI/Gemini kuratiert. Nur
    Vorschläge — das Modellfeld bleibt frei tippbar.
    """
    return await model_catalog.list_models(provider)


@router.post("/detect")
async def scan_detect(file: UploadFile = File(...)):
    """
    Live-Vorschau der Kamera (mehrere Karten / Mappenseite): Umrisse, die der
    Bild-Abgleich in diesem Bild findet. available=false, wenn der matcher-
    Dienst fehlt oder noch lädt – das UI zeigt dann einfach keine Umrisse.
    """
    data = await file.read()
    if not data or len(data) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Leeres oder zu großes Vorschaubild")
    cards = await local_match.detect(data, base_url=settings.matcher_url,
                                     mime_type=(file.content_type or "image/jpeg").lower())
    return {"available": cards is not None, "cards": cards or []}


@router.get("/status")
def scan_status(db: Session = Depends(get_db)):
    """Welche Erkennungs-Engine ist aktiv? (provider-bewusste Anzeige im UI)"""
    provider, key, model, _base = _reader_config(db)
    ocr_on = ocr.is_enabled()
    local: dict | None = None
    if provider == "gemini":
        reader_on = gemini.is_enabled(key)
    elif provider in ("openai", "openrouter"):
        reader_on = vlm.is_enabled(key)
    elif provider == "local":
        local = local_match.state(_base)
        reader_on = local["status"] == "ready"
    else:  # "ocr": bewusst nur lokale Erkennung
        reader_on = False
    active = provider if reader_on else ("ocr" if ocr_on else "none")
    return {
        # Bild-Abgleich: Zustand des matcher-Dienstes (lädt nach einem Neustart
        # eine Weile) – das UI zeigt das an und fragt erneut, bis er bereit ist.
        "local": local,
        "provider": provider,
        "model": model,
        # Rückwärtskompatibel: alte Clients lesen weiter gemini/ocr/active.
        "gemini": provider == "gemini" and reader_on,
        "ocr": ocr_on,
        "active": active,
    }


# Google-AI-Studio Free-Tier-Limits (Stand 2025/2026). Flash vs Pro.
_GEMINI_FREE_LIMITS = {
    "flash": {"rpd": 1500, "rpm": 15, "tpm": 1_000_000},
    "pro": {"rpd": 50, "rpm": 2, "tpm": 2_000_000},
}

# Menschenlesbarer Hinweis je harter Gemini-Fehlerart (Issue #21). Das UI zeigt
# per hinweis_art eigene, i18n-fähige Texte; dieser DE-Text dient Logs und
# nicht-i18n-Konsumenten.
_GEMINI_HINWEIS = {
    gemini.FEHLER_RATE: "Gemini-Rate-Limit erreicht – Erkennung über lokale OCR.",
    gemini.FEHLER_KEY: "Gemini-API-Key ungültig oder fehlt – Erkennung über lokale OCR.",
    gemini.FEHLER_GEMINI: "Gemini nicht erreichbar – Erkennung über lokale OCR.",
}


@router.get("/usage")
def scan_usage(db: Session = Depends(get_db)):
    """Gemini-Nutzung + Free-Tier-Limits (zur Kostenkontrolle / Überblick)."""
    today = _today_utc()
    rows = db.scalars(
        select(GeminiUsage).order_by(GeminiUsage.day.desc()).limit(30)
    ).all()
    today_row = next((r for r in rows if r.day == today), None)
    total_req = int(db.scalar(select(func.coalesce(func.sum(GeminiUsage.requests), 0))) or 0)
    total_tok = int(db.scalar(select(func.coalesce(func.sum(GeminiUsage.tokens), 0))) or 0)

    _, model = _gemini_config(db)
    tier = "pro" if "pro" in (model or "").lower() else "flash"
    limits = dict(_GEMINI_FREE_LIMITS[tier])
    # Manuelles Tageslimit überschreibt (falls gesetzt > 0)
    manual = int(_setting(db, "gemini_daily_limit", "0") or 0)
    if manual > 0:
        limits["rpd"] = manual

    avg_tokens = round(total_tok / total_req) if total_req else 0
    # Live-Verbrauch der letzten 60s (RPM/TPM) je Prozess (Issue #22); flüchtig,
    # Reset bei Neustart – kein DB-Wert.
    rpm_used, tpm_used = gemini_rate.snapshot()
    return {
        "today": {
            "day": today,
            "requests": today_row.requests if today_row else 0,
            "tokens": today_row.tokens if today_row else 0,
        },
        "total": {"requests": total_req, "tokens": total_tok},
        "avg_tokens_per_scan": avg_tokens,
        "model": model,
        "limits": limits,
        "rpm_used": rpm_used,
        "tpm_used": tpm_used,
        "days": [{"day": r.day, "requests": r.requests, "tokens": r.tokens} for r in rows],
    }


def _page_corners(page: str) -> str | None:
    """Ecken der schräg fotografierten Mappenseite (App) prüfen → JSON für den Bild-Abgleich, sonst None."""
    if not page or len(page) > 400:
        return None
    try:
        q = json.loads(page)
        pts = [[round(float(x), 4), round(float(y), 4)] for x, y in q]
    except (ValueError, TypeError):
        return None
    if len(pts) != 4 or any(not (-1.0 <= v <= 2.0) for p in pts for v in p):
        return None
    return json.dumps(pts)


@router.post("", response_model=ScanResponse)
async def scan(
    file: UploadFile = File(...),
    mode: ScanMode = Form("single"),
    rows: int = Form(0),
    cols: int = Form(0),
    default_language: str = Form("DE"),
    page: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Erkennt Karten auf einem Foto.
    - mode=single  : eine Karte
    - mode=multi   : mehrere lose Karten (Gemini empfohlen)
    - mode=binder  : ganze Mappenseite; rows×cols = Raster (z.B. 3×3).
      page (optional): die vier Ecken der Seite im Foto als Anteile,
      [[x, y], …] ab oben links im Uhrzeigersinn — das geneigte Raster der
      App bei einem schräg aufgenommenen Foto; der Bild-Abgleich entzerrt die
      Seite damit, bevor er sie in Fächer teilt.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Leeres Bild")
    if len(data) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Bild zu groß (max. 12 MB)")

    mime = (file.content_type or "image/jpeg").lower()
    if not mime.startswith("image/"):
        raise HTTPException(status_code=400, detail="Content-Type muss image/* sein")
    t0 = time.perf_counter()
    # „card": entzerrter Zuschnitt aus dem Ecken-Editor. Der Bild-Abgleich
    # überspringt dafür die Erkennung; alle anderen Leser sehen ein Einzelbild.
    reader_mode = "single" if mode == "card" else mode

    engine = "none"
    reads = None
    hinweis: str | None = None
    hinweis_art: str | None = None
    limit_erreicht = False
    # Lese-Stufe B (Issue #57): das in den Einstellungen gewählte Modell liest
    # die Karte; bei fehlendem Key/hartem Fehler/„ocr" greift die lokale OCR.
    provider, reader_key, reader_model, reader_base = _reader_config(db)
    local_extras: dict[int, local_match.LocalExtra] = {}
    if provider == "local":
        result = await local_match.extract(
            data, base_url=reader_base or "", mime_type=mime, mode=mode, rows=rows, cols=cols,  # card → matcher
            page=_page_corners(page) if mode == "binder" else None)
        if result.reads is not None:
            reads, local_extras, engine = result.reads, result.extras, "local"
        else:
            hinweis_art = result.fehler_art
            hinweis = ("Bild-Abgleich startet noch – Erkennung über lokale OCR."
                       if result.fehler_art == local_match.FEHLER_STARTET
                       else "Bild-Abgleich nicht erreichbar – Erkennung über lokale OCR.")
    elif provider == "gemini" and gemini.is_enabled(reader_key):
        if _gemini_limit_reached(db):
            limit_erreicht = True
            hinweis_art = "tageslimit"
            hinweis = "Gemini-Tageslimit erreicht – Erkennung über lokale OCR."
            log.info("Gemini-Tageslimit erreicht – Scan fällt auf lokale OCR zurück.")
        else:
            result = await gemini.extract(
                data, api_key=reader_key, model=reader_model, mime_type=mime)
            if result.tokens is not None:
                _record_usage(db, result.tokens)
                # Live-Verbrauch (RPM/TPM) im 60s-Fenster mitzählen (Issue #22).
                gemini_rate.record(requests=1, tokens=result.tokens)
            if result.reads is not None:
                reads = result.reads
                engine = "gemini"
            elif result.fehler_art:
                # Harter Gemini-Fehler → OCR-Fallback, Ursache nach außen melden.
                hinweis_art = result.fehler_art
                hinweis = _GEMINI_HINWEIS.get(result.fehler_art)
                # Rate-Limit ist wie das Tageslimit ein erreichtes Kontingent →
                # limit_erreicht (grobes Bool für Bestandskonsumenten).
                limit_erreicht = result.fehler_art == gemini.FEHLER_RATE
    elif provider in ("openai", "openrouter") and vlm.is_enabled(reader_key):
        result = await vlm.extract(
            data, base_url=reader_base, api_key=reader_key, model=reader_model,
            mime_type=mime, title="PokeCollect")  # ASCII: HTTP-Header vertragen kein „é"
        if result.reads is not None:
            reads = result.reads
            engine = provider
        elif result.fehler_art:
            # Harter Fehler des Lesemodells → OCR-Fallback, Ursache melden.
            hinweis_art = result.fehler_art
            hinweis = _READER_HINWEIS.get(result.fehler_art)
            limit_erreicht = result.fehler_art == vlm.FEHLER_RATE
    if reads is None:
        reads = ocr.extract(data, mode=reader_mode, rows=rows, cols=cols)
        engine = "ocr"

    reads = reads or []
    t_read = time.perf_counter()
    # Koordinaten-Vorstufe (Panel-Runde 3, Auflage A1 — Regression aus Runde
    # 2): MUSS vor der Auswahl laufen. Ohne sie liest die Auswahl unten die
    # ROHE bbox-Fläche ohne Gültigkeitsfilter — ein Koordinaten-Müll-Wert wie
    # [2.0, 0.1, 0.63, 0.88] (vlm.py-Fallback-Bug) hat trotzdem eine reale,
    # aber bedeutungslose Fläche und kann eine echte, nur unplausibel-
    # großformatige Hauptkarte aus der Auswahl verdrängen. Prüft NUR
    # Koordinaten, kein Bild nötig (Details: crop_hints.py).
    reads = crop_hints.discard_invalid_coords(reads)
    # Einzelkarten-Modus: nur die Hauptkarte behalten (größte Bounding-Box),
    # falls versehentlich eine Karte darunter mit erkannt wurde. MUSS vor der
    # Sanitierung laufen (Panel-Nacharbeit #36, Auflage 1 — Blocker): die
    # Auswahl entscheidet, WELCHER Read die Hauptkarte ist, und dafür ist auch
    # eine (später ggf. verworfene) unplausible, aber KOORDINATEN-gültige Box
    # ein gültiges Größensignal. In der Runde-1-Reihenfolge (Sanitierung
    # zuerst) zählte eine verworfene bbox als Fläche 0.0 — eine
    # überdimensionierte, aber richtig sitzende Hauptkarten-Box verlor dann
    # gegen einen winzigen plausiblen Nachbarn, und der GANZE Read der
    # Hauptkarte (Name, Nummer, alles) ging verloren, nicht nur ihr
    # Zuschnitt.
    if reader_mode == "single" and len(reads) > 1:
        def _area(r) -> float:
            return (r.bbox[2] * r.bbox[3]) if (r.bbox and len(r.bbox) == 4) else 0.0
        reads = [max(reads, key=_area)]
    # Plausibilitäts-Sanitierung der Zuschnitt-Hinweise (#36): verwirft (None)
    # unplausible bbox/quad AM GEWINNER, bevor das Frontend sie sieht — die
    # Auswahl oben ist bereits entschieden, hier geht nur noch die
    # Zuschnitt-EIGNUNG des Hinweises verloren, nicht mehr der ganze Read.
    # Verworfen heißt: kein eigener Foto-Zuschnitt, die Karte bekommt das
    # Katalogbild statt eines eigenen Fotos (das Frontend hat KEINE Kaskade
    # auf eine "nächste Stufe" — Details in crop_hints.py). `mode` steuert
    # zusätzlich die Flächen-Kappe (nur multi/binder, Auflage 2). Braucht die
    # echten Bildmaße, die nur der Aufrufer hier hat.
    reads = crop_hints.sanitize_crop_hints(reads, data, mode=reader_mode)

    candidates = await resolve_reads(db, reads, default_lang=default_language)
    t_end = time.perf_counter()
    log.info("Scan %s über %s: %d Karte(n) in %d ms (Erkennung %d ms, Auflösung/TCGdex %d ms)",
             mode, engine, len(candidates), round((t_end - t0) * 1000),
             round((t_read - t0) * 1000), round((t_end - t_read) * 1000))
    # Bild-Abgleich: Alternativen + „bitte wählen" an die Kandidaten hängen
    # (Zuordnung über das Read-Objekt; resolve_reads sortiert um).
    for c in candidates:
        extra = local_extras.get(id(c.raw))
        if extra:
            c.alternatives = extra.alternatives
            c.needs_choice = extra.needs_choice
            c.stamp = extra.stamp
            if extra.needs_choice:  # kein eindeutiger Treffer → ehrliche, niedrige Sicherheit
                c.confidence = min(c.confidence, extra.confidence)
    return ScanResponse(
        engine=engine, mode=mode, candidates=candidates,
        limit_erreicht=limit_erreicht, hinweis=hinweis, hinweis_art=hinweis_art,
    )


@router.post("/resolve", response_model=ScanCandidate)
async def scan_resolve(read: ScanRawRead, db: Session = Depends(get_db)):
    """
    Löst eine (manuell angepasste) Erkennung neu gegen TCGdex auf – für die
    Live-Vorschau im Bestätigungs-Dialog, wenn der Nutzer Set/Nummer ändert.
    """
    return await resolve_one(db, read, default_lang=read.language or "DE")


def _page_size(layout: Optional[str]) -> int:
    """Fächer je Mappenseite aus dem Layout ("3x3" → 9)."""
    try:
        a, b = (int(x) for x in (layout or "3x3").lower().split("x"))
        return max(1, a * b)
    except ValueError:
        return 9


def _plan_slots(db: Session, collection: Collection, payload: ScanCommitRequest) -> tuple[dict[tuple[int, int], int], set[int]]:
    """
    Fach je (Item, Exemplar). Belegte Fächer werden nie doppelt vergeben —
    ist das gewünschte belegt, gilt das nächste freie dahinter.
      - Mappenseiten-Scan (item.position): Seite ab start_position, ohne Wahl
        eine neue Seite hinter der letzten belegten.
      - Sonst der Reihe nach ab start_position, ohne Wahl ab dem ersten freien.
    """
    used = set(db.scalars(
        select(collection_cards.c.position)
        .where(collection_cards.c.collection_id == collection.id)
        .where(collection_cards.c.position.isnot(None))
    ).all())
    start = payload.start_position
    ps = _page_size(collection.binder_layout)
    base = start if start is not None else ((max(used) // ps + 1) * ps if used else 0)

    def take(p: int) -> int:
        while p in used:
            p += 1
        used.add(p)
        return p

    plan: dict[tuple[int, int], int] = {}
    for i, item in enumerate(payload.items):
        if item.position is not None:
            plan[(i, 0)] = take(base + item.position)
    cursor = start if start is not None else 0
    for i, item in enumerate(payload.items):
        for c in range(item.copies):
            if (i, c) not in plan:
                plan[(i, c)] = take(cursor)
                cursor = plan[(i, c)] + 1
    return plan, used


_CARD_ID_RE = re.compile(r"[\w.\-]{1,64}")


FOIL_MAX_BYTES = 5 * 1024 * 1024


@router.post("/foil")
async def scan_foil(file: UploadFile = File(...), card_id: str = Form(...)):
    """
    Reverse holo? Der Zuschnitt einer erkannten Karte gegen ihr Katalogbild
    (matcher /foil, mit dem hochgeladenen Folien-Modell). Nie ein Fehler fürs
    UI: ohne Modell, matcher oder Katalogbild gibt es eben keinen Hinweis.
    → {"model": bool, "hint"?: bool, "score"?: float, …}
    """
    import httpx

    from app.services import foil_check

    if not _CARD_ID_RE.fullmatch(card_id or ""):
        raise HTTPException(status_code=422, detail="Ungültige Karten-ID")
    data = await file.read(FOIL_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Leere Datei")
    if len(data) > FOIL_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Bild zu groß (max. 5 MB)")
    try:
        return await foil_check.check(card_id, data)
    except httpx.HTTPError as exc:
        log.info("Folien-Prüfung %s: matcher nicht erreichbar (%s)", card_id, exc)
        return {"model": False}


@router.post("/outline")
async def scan_outline(file: UploadFile = File(...), quad: str = Form(""), card_id: str = Form(""),
                       db: Session = Depends(get_db)):
    """
    Ecken im Scan-Foto vorschlagen (übersehene Karte nachtragen, Ecken anpassen):
    grobe Ecken an die echten Kartenkanten anlegen. Mit card_id (die erkannte
    Karte) über ihr Artwork und das gleichnamiger Drucke, sonst über einen
    typischen Kartenrahmen, zuletzt über die Kanten.
    → {"quad", "method", "frame_ok", "reference", "reference_label"}.
    """
    import httpx

    from app.services import picture_finder

    data = await file.read(picture_finder.OUTLINE_MAX_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Leere Datei")
    if len(data) > picture_finder.OUTLINE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Bild zu groß (max. 12 MB)")
    try:
        q = picture_finder.parse_rough_quad(quad)
    except ValueError:
        raise HTTPException(status_code=422, detail="Ungültige Ecken")
    refs: list[str] = []
    if card_id and _CARD_ID_RE.fullmatch(card_id):
        refs.append(card_id)
        row = catalog_row_for(db, card_id)
        if row is not None:
            refs += picture_finder.same_name_refs(db, row, limit=8)
    try:
        return await picture_finder.outline_labelled(db, data, q, refs or None)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")


@router.post("/commit", response_model=ScanCommitResponse)
def scan_commit(payload: ScanCommitRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Legt die bestätigten Karten an. Ziel:
      - pokedex     → besessen, Pokédex-Vertreter automatisch (exklusiv)
      - collection  → besessen + Sammlung (mit Binder-Slot)
      - wishlist    → nicht besessen, auf Wunschliste (mit Priorität)
      - deck        → besessen + ins Deck (deck_id), die Deckliste wächst mit
    Besessene Karten laufen über den Domain-Service (Adoption + Auto-Flag
    + Bild-Fetch, Issue #4).
    """
    is_wishlist = payload.target == "wishlist"

    collection: Collection | None = None
    if payload.target == "collection":
        if not payload.collection_id:
            raise HTTPException(status_code=400, detail="collection_id fehlt")
        collection = db.get(Collection, payload.collection_id)
        if not collection:
            raise HTTPException(status_code=404, detail="Sammlung nicht gefunden")

    deck: Deck | None = None
    if payload.target == "deck":
        if not payload.deck_id:
            raise HTTPException(status_code=400, detail="deck_id fehlt")
        deck = db.get(Deck, payload.deck_id)
        if not deck:
            raise HTTPException(status_code=404, detail="Deck nicht gefunden")

    # Scan-Entwurf: gesperrt lesen (zwei Geräte bestätigen gleichzeitig) und Karten,
    # die dort schon gespeichert sind, nicht noch einmal anlegen.
    from app.services import scan_drafts as drafts_svc

    draft = drafts_svc.locked(db, payload.draft_id) if payload.draft_id is not None else None
    draft_state = drafts_svc.load_state(draft) if draft is not None else None
    if draft_state is not None and drafts_svc.already_saved(
            draft_state, [i.draft_key for i in payload.items if i.draft_key]):
        raise HTTPException(status_code=409, detail="Schon gespeichert — vermutlich an einem anderen Gerät")

    plan: dict[tuple[int, int], int] = {}
    used: set[int] = set()
    if collection:
        plan, used = _plan_slots(db, collection, payload)

    created_ids: list[int] = []
    first_ids: list[int] = []
    copy_ids: list[list[int]] = []
    positions: list[int] = []
    for item_no, item in enumerate(payload.items):
        item_ids: list[int] = []
        bild_url = item.bild_karte_url if is_allowed_image_url(item.bild_karte_url) else None
        if bild_url is None and item.tcgdex_card_id:
            # Ohne Katalogbild im Payload (eigenes Foto gewählt): das Katalogbild
            # trotzdem als Rückfall setzen. Das Foto hat in der Anzeige Vorrang;
            # Exemplare ohne Foto zeigen so nicht den Platzhalter, falls der
            # TCGdex-Abruf im Hintergrund scheitert.
            row = catalog_row_for(db, item.tcgdex_card_id)
            if row is not None and is_allowed_image_url(row.image_url):
                bild_url = row.image_url
        for copy_no in range(1 if is_wishlist else item.copies):
            fields = dict(
                kartenname=item.kartenname,
                pokedex_nr=item.pokedex_nr,
                englischer_name=item.englischer_name,
                set_edition=item.set_edition,
                karten_nr=item.karten_nr,
                seltenheit=item.seltenheit,
                kartenversion=item.kartenversion,
                folierung=item.folierung,
                muster=item.muster,
                prize_pack=item.prize_pack,
                erste_edition=item.erste_edition,
                sprache=item.sprache or "DE",
                zustand=item.zustand,
                notizen=item.notizen,
                tcgdex_card_id=item.tcgdex_card_id,
                set_id=item.set_id,
                dex_id=item.dex_id,
                bild_karte_url=bild_url,
            )
            if is_wishlist:
                # EN-Namen-Fill wie in create_owned_card (Wunschliste läuft daran
                # vorbei; typisch JP: TCGdex kennt keinen EN-Namen, v1.7.3).
                if not fields.get("englischer_name") and fields.get("tcgdex_card_id"):
                    row = catalog_row_for(db, fields["tcgdex_card_id"])
                    if row is not None and row.name_en:
                        fields["englischer_name"] = row.name_en
                card = PokemonCard(**fields, besessen=False, wunschliste=True, prioritaet=item.prioritaet)
                db.add(card)
                db.flush()  # ID
                regulation.apply_to_card(db, card)
            else:
                card = create_owned_card(db, fields, background_tasks=background_tasks, commit=False)

            # Sammlung + Fach aus dem Plan (nie None, #30)
            if collection:
                slot = plan[(item_no, copy_no)]
                positions.append(slot)
                db.execute(
                    collection_cards.insert().values(
                        collection_id=collection.id,
                        card_id=card.id,
                        position=slot,
                    )
                )

            # Deck: Karte hinein, die Liste wächst, wo kein Platz frei ist
            if deck:
                decks_svc.assign(db, deck, card)

            created_ids.append(card.id)
            item_ids.append(card.id)
            if copy_no == 0:
                first_ids.append(card.id)
        copy_ids.append(item_ids)

    if draft is not None:
        drafts_svc.mark_saved(draft, draft_state, {
            item.draft_key: {"ids": ids, "name": item.kartenname, "name_en": item.englischer_name}
            for item, ids in zip(payload.items, copy_ids, strict=True) if item.draft_key})
    db.commit()
    next_position = None
    if positions:
        next_position = max(positions) + 1
        while next_position in used:
            next_position += 1
    return ScanCommitResponse(created=len(created_ids), card_ids=first_ids,
                              all_card_ids=created_ids, copy_ids=copy_ids,
                              positions=positions, next_position=next_position)
