"""
Scan-Stufe B, Provider „local": Bild-Abgleich über den matcher-Dienst
(Artwork-Vergleich gegen einen lokalen Index, ohne Cloud, ohne API-Key).

Der Dienst liefert je erkannter Karte die Ecken, die besten Kandidaten
(TCGdex-IDs) und ob die Wahl eindeutig ist. Hier wird daraus je Karte ein
ScanRawRead mit `tcgdex_card_id` (der Resolver nimmt die Karte direkt) plus
die Alternativen für die Auswahl im Bestätigungs-Dialog.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.schemas.scan import ScanAlternative, ScanRawRead

log = logging.getLogger(__name__)

FEHLER_NICHT_BEREIT = "reader_fehler"
FEHLER_STARTET = "bild_abgleich_startet"   # Dienst lädt noch (typisch kurz nach einem Neustart)
_TIMEOUT = httpx.Timeout(60.0, connect=3.0)


@dataclass
class LocalExtra:
    alternatives: list[ScanAlternative] = field(default_factory=list)
    needs_choice: bool = False
    confidence: float = 0.0


@dataclass
class LocalResult:
    reads: Optional[list[ScanRawRead]] = None
    extras: dict[int, LocalExtra] = field(default_factory=dict)  # id(read) -> extra
    fehler_art: Optional[str] = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT)


def status(base_url: str) -> dict:
    """GET /health des Dienstes (kurz, für /scan/status). Fehler → {}."""
    if not base_url:
        return {}
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=2.0)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def is_ready(base_url: str) -> bool:
    return status(base_url).get("status") == "ready"


_DETECT_TIMEOUT = httpx.Timeout(4.0, connect=2.0)


async def detect(image_bytes: bytes, *, base_url: str, mime_type: str) -> Optional[list[dict]]:
    """Live-Vorschau: Kartenumrisse im Kamerabild (nur Erkennung). None = nicht verfügbar."""
    if not base_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=_DETECT_TIMEOUT) as client:
            r = await client.post(f"{base_url.rstrip('/')}/detect",
                                  files={"file": ("frame", image_bytes, mime_type)})
    except Exception:
        return None
    if r.status_code == 429:
        return []                   # Dienst rechnet noch am letzten Bild → dieses auslassen
    if r.status_code != 200:
        return None
    return [{"quad": c["quad"]} for c in r.json().get("cards", []) if isinstance(c.get("quad"), list)]


def poke_learn(base_url: str) -> None:
    """Neues Scan-Foto gespeichert → der matcher lernt es zeitnah (best effort)."""
    if not base_url:
        return
    try:
        httpx.post(f"{base_url.rstrip('/')}/learn", timeout=2.0)
    except Exception as exc:
        log.debug("matcher /learn nicht erreichbar: %s", exc)


def state(base_url: str) -> dict:
    """Kurzform für /scan/status: {"status": ready|loading|converting|error|unreachable, "phase": …}."""
    st = status(base_url)
    return {"status": st.get("status") or "unreachable", "phase": st.get("phase")}


def _bbox(quad: list[list[float]]) -> list[float]:
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    x0, y0 = max(0.0, min(xs)), max(0.0, min(ys))
    return [x0, y0, min(1.0, max(xs)) - x0, min(1.0, max(ys)) - y0]


async def extract(image_bytes: bytes, *, base_url: str, mime_type: str, mode: str,
                  rows: int = 0, cols: int = 0) -> LocalResult:
    t0 = time.perf_counter()
    try:
        async with _client() as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/match",
                files={"file": ("scan", image_bytes, mime_type)},
                data={"mode": mode, "rows": str(rows), "cols": str(cols), "top_k": "5"},
            )
    except Exception as exc:
        log.warning("Matcher nicht erreichbar (%s): %s", base_url, exc)
        return LocalResult(fehler_art=FEHLER_NICHT_BEREIT)
    if r.status_code != 200:
        log.warning("Matcher antwortete %s: %s", r.status_code, r.text[:200])
        startet = r.status_code == 503 and "not ready" in r.text
        return LocalResult(fehler_art=FEHLER_STARTET if startet else FEHLER_NICHT_BEREIT)
    body = r.json()
    log.info("Bild-Abgleich (%s): %d Karte(n) in %d ms, davon Matcher %s ms %s",
             mode, len(body.get("cards") or []), round((time.perf_counter() - t0) * 1000),
             body.get("ms"), body.get("timing") or "")

    reads: list[ScanRawRead] = []
    extras: dict[int, LocalExtra] = {}
    for card in body.get("cards", []):
        cands = card.get("candidates") or []
        if not cands:
            continue
        top = cands[0]
        quad = card.get("quad")
        read = ScanRawRead(
            name=top.get("name"),
            number=top.get("local_id"),
            position=card.get("position"),
            confidence=card.get("confidence"),
            quad=quad,
            bbox=_bbox(quad) if quad else None,
            tcgdex_card_id=top.get("card_id"),
        )
        reads.append(read)
        extras[id(read)] = LocalExtra(
            alternatives=[ScanAlternative(
                tcgdex_card_id=c["card_id"], name=c.get("name"), set_name=c.get("set_name"),
                local_id=c.get("local_id"), image_url=c.get("image_url"),
                art_inliers=int(c.get("art_inliers") or 0),
            ) for c in cands if c.get("card_id")],
            needs_choice=not card.get("auto", False),
            confidence=float(card.get("confidence") or 0.0),
        )
    return LocalResult(reads=reads, extras=extras)
