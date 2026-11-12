"""
Scan-Entwürfe (models/scan_draft.py): Übersicht, Merker „schon gespeichert"
und Aufräumen.

Der Zustand gehört der Web-App (Prüfseite als JSON). Der Server versteht nur
so viel davon:
  {"candidates": [{"key": str, "saved": {"ids": [...], "name": str} | null,
                   "suggested": {"kartenname": ...}, "raw": {"name": ...}, …}, …], …}

„saved" wird nur mehr, nie weniger: ein Abgleich vom zweiten Gerät (oder ein
verspätetes Speichern) kann eine schon gespeicherte Karte nicht wieder offen
machen — sonst ließe sie sich doppelt anlegen. Das Bestätigen selbst markiert
serverseitig in derselben Transaktion (mark_saved) und lehnt bereits
gespeicherte Karten ab (already_saved).
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.scan_draft import ScanDraft

log = logging.getLogger(__name__)

MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_PHOTO_BYTES = 12 * 1024 * 1024
MAX_PER_USER = 30            # ältere Entwürfe fallen beim Anlegen eines neuen weg
KEEP_DAYS = 30
THUMB_SIDE = 640            # Vorschau auf „Offene Scans" (das ganze Foto, auch am Handy scharf)
MAX_OUTLINES = 60


def parse_state(raw: str | bytes) -> dict:
    """JSON-Zustand prüfen: Objekt mit einer Liste candidates (je mit key). ValueError sonst."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError("Entwurf zu groß")
    try:
        state = json.loads(raw)
    except ValueError:
        raise ValueError("Entwurf ist kein gültiges JSON") from None
    cands = state.get("candidates") if isinstance(state, dict) else None
    if not isinstance(cands, list) or not all(isinstance(c, dict) and isinstance(c.get("key"), str) for c in cands):
        raise ValueError("Entwurf ohne Kartenliste")
    return state


def _name(c: dict) -> str:
    s = c.get("suggested") if isinstance(c.get("suggested"), dict) else {}
    r = c.get("raw") if isinstance(c.get("raw"), dict) else {}
    return str(s.get("kartenname") or r.get("name") or "?")[:80]


def _quad(v: Any) -> list[list[float]] | None:
    if isinstance(v, list) and len(v) == 4 and all(
            isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in p)
            for p in v):
        return [[round(min(1.0, max(0.0, float(x))), 4) for x in p] for p in v]
    return None


def _outline(c: dict) -> list[list[float]] | None:
    """Wo die Karte im Foto liegt: von Hand gesetzte Ecken, sonst die erkannten (Ecken oder Rechteck)."""
    raw = c.get("raw") if isinstance(c.get("raw"), dict) else {}
    q = _quad(c.get("editedQuad")) or _quad(raw.get("quad"))
    if q:
        return q
    b = raw.get("bbox")
    if isinstance(b, list) and len(b) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in b):
        x, y, w, h = (float(v) for v in b)
        return _quad([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    return None


def _id(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def summary(state: dict) -> dict:
    """Für die Liste: offene/gespeicherte Karten, die ersten Namen, Ziel (mit Sammlung/Deck) und Modus."""
    cands = state.get("candidates", [])
    open_ = [c for c in cands if not c.get("saved")]
    target = state.get("target") if isinstance(state.get("target"), str) else None
    return {"open": len(open_), "saved": len(cands) - len(open_),
            "names": [_name(c) for c in open_[:4]],
            "target": target,
            "collection_id": _id(state.get("collectionId")) if target == "collection" else None,
            "deck_id": _id(state.get("deckId")) if target == "deck" else None,
            "mode": state.get("mode") if isinstance(state.get("mode"), str) else None,
            # Umrisse der Karten fürs ganze Foto (Kopien ohne eigene Stelle im Foto fehlen)
            "outlines": [{"quad": q, "saved": bool(c.get("saved"))}
                         for c in cands if not c.get("variantOf") and (q := _outline(c))][:MAX_OUTLINES]}


def saved_map(state: dict) -> dict[str, Any]:
    return {c["key"]: c["saved"] for c in state.get("candidates", []) if c.get("saved")}


def merge_saved(old: dict, new: dict) -> dict:
    """Neuer Zustand, aber jede schon gespeicherte Karte bleibt gespeichert."""
    known = saved_map(old)
    for c in new.get("candidates", []):
        if not c.get("saved") and c.get("key") in known:
            c["saved"] = known[c["key"]]
    return new


def thumbnail(photo: bytes) -> bytes | None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(photo)) as img:
            img = img.convert("RGB")
            img.thumbnail((THUMB_SIDE, THUMB_SIDE))
            out = io.BytesIO()
            img.save(out, "JPEG", quality=80)
            return out.getvalue()
    except Exception:  # noqa: BLE001 — Vorschau ist optional, das Foto selbst prüft die Web-App
        return None


def store(draft: ScanDraft, state: dict) -> None:
    draft.state = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    draft.summary = json.dumps(summary(state), ensure_ascii=False)
    draft.aktualisiert_am = datetime.utcnow()


def load_state(draft: ScanDraft) -> dict:
    try:
        return parse_state(draft.state)
    except ValueError:
        return {"candidates": []}


def locked(db: Session, draft_id: int) -> ScanDraft | None:
    """Eigener Entwurf, für die Dauer der Transaktion gesperrt (zwei Geräte bestätigen gleichzeitig)."""
    return db.execute(select(ScanDraft).where(ScanDraft.id == draft_id).with_for_update()).scalar_one_or_none()


def already_saved(state: dict, keys: list[str]) -> list[str]:
    known = saved_map(state)
    return [k for k in keys if k in known]


def mark_saved(draft: ScanDraft, state: dict, marks: dict[str, dict]) -> None:
    for c in state.get("candidates", []):
        if c.get("key") in marks:
            c["saved"] = marks[c["key"]]
    store(draft, state)


def prune_user(db: Session) -> None:
    """Beim Anlegen: eigene Entwürfe älter als KEEP_DAYS und über MAX_PER_USER hinaus entfernen."""
    old = datetime.utcnow() - timedelta(days=KEEP_DAYS)
    db.execute(delete(ScanDraft).where(ScanDraft.aktualisiert_am < old).execution_options(synchronize_session=False))
    ids = db.execute(select(ScanDraft.id).order_by(ScanDraft.aktualisiert_am.desc())).scalars().all()
    if len(ids) >= MAX_PER_USER:
        db.execute(delete(ScanDraft).where(ScanDraft.id.in_(ids[MAX_PER_USER - 1:]))
                   .execution_options(synchronize_session=False))


def prune_all(db: Session) -> int:
    """Nächtlich: Entwürfe aller Konten, die KEEP_DAYS nicht angefasst wurden."""
    old = datetime.utcnow() - timedelta(days=KEEP_DAYS)
    res = db.execute(delete(ScanDraft).where(ScanDraft.aktualisiert_am < old)
                     .execution_options(all_users=True, synchronize_session=False))
    db.commit()
    n = res.rowcount or 0
    if n:
        log.info("Scan-Entwürfe: %d alte entfernt", n)
    return n
