"""Fehler und Debug-Protokoll aus dem Browser ins Server-Log, für die Fehlersuche.

POST /client-log         {kind, message, stack?, url?, version?} → 204   (api-errors.log)
POST /client-log/debug   {events: [{t, event, data}], session, url?, version?} → 204   (api.log)

Die Web-App meldet unbehandelte Fehler und abgelehnte Promises (höchstens 20 je
Seitenaufruf, gleiche nur einmal); hier zusätzlich höchstens 30 je Minute und Benutzer.
Das Debug-Protokoll schickt sie nur, wenn es im Browser eingeschaltet ist
(Einstellungen → Werkzeuge oder ?debug=1): alle zwei Sekunden die gesammelten
Ereignisse, je eine Zeile „web.debug [Benutzer Sitzung +Sekunden] Ereignis {Daten}".
"""
import json
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any, Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.deps import require_auth
from app.auth_user import AuthUser

log = logging.getLogger("web.client")
dlog = logging.getLogger("web.debug")
router = APIRouter(prefix="/client-log", tags=["client-log"])
PER_MINUTE = 30
DEBUG_BATCHES_PER_MINUTE = 60
_recent: dict[int, deque] = defaultdict(deque)
_recent_debug: dict[int, deque] = defaultdict(deque)
_EVENT = re.compile(r"[^a-z0-9._\-]")


class ClientLog(BaseModel):
    kind: str = Field("error", max_length=30)
    message: str = Field(..., max_length=2000)
    stack: Optional[str] = Field(None, max_length=8000)
    url: Optional[str] = Field(None, max_length=500)
    version: Optional[str] = Field(None, max_length=40)


@router.post("", status_code=204, response_class=Response)
def client_log(entry: ClientLog, user: AuthUser = Depends(require_auth)):
    if _allowed(_recent, user.id, PER_MINUTE):
        log.warning("Browser-Fehler (%s) bei %s, Benutzer %s, Version %s: %s%s", entry.kind, entry.url or "?",
                    user.username, entry.version or "?", entry.message,
                    f"\n{entry.stack}" if entry.stack else "")
    return Response(status_code=204)


class ClientEvent(BaseModel):
    t: float = Field(0, ge=0, le=1e10)              # ms seit dem Seitenaufruf
    event: str = Field(..., max_length=60)
    data: Optional[dict[str, Any]] = None


class ClientDebug(BaseModel):
    events: list[ClientEvent] = Field(..., max_length=50)
    session: str = Field("", max_length=20)
    url: Optional[str] = Field(None, max_length=500)
    version: Optional[str] = Field(None, max_length=40)


def _allowed(store: dict[int, deque], user_id: int, per_minute: int) -> bool:
    now = time.monotonic()
    q = store[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        return False
    q.append(now)
    return True


@router.post("/debug", status_code=204, response_class=Response)
def client_debug(batch: ClientDebug, user: AuthUser = Depends(require_auth)):
    if not _allowed(_recent_debug, user.id, DEBUG_BATCHES_PER_MINUTE):
        return Response(status_code=204)
    who = f"{user.username} {_EVENT.sub('', batch.session.lower())[:12] or '-'}"
    for e in batch.events:
        name = _EVENT.sub("", e.event.lower())[:60] or "?"
        data = json.dumps(e.data, ensure_ascii=False, separators=(",", ":"), default=str)[:3000] if e.data else ""
        dlog.info("[%s +%.1fs] %s %s", who, e.t / 1000, name, data)   # json: Zeilenumbrüche sind maskiert
    return Response(status_code=204)
