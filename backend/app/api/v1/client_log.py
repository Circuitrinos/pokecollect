"""Fehler aus dem Browser ins Server-Log (api-errors.log), für die Fehlersuche.

POST /client-log   {kind, message, stack?, url?, version?} → 204

Die Web-App meldet unbehandelte Fehler und abgelehnte Promises (höchstens 20 je
Seitenaufruf, gleiche nur einmal); hier zusätzlich höchstens 30 je Minute und Benutzer.
"""
import logging
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.deps import require_auth
from app.auth_user import AuthUser

log = logging.getLogger("web.client")
router = APIRouter(prefix="/client-log", tags=["client-log"])
PER_MINUTE = 30
_recent: dict[int, deque] = defaultdict(deque)


class ClientLog(BaseModel):
    kind: str = Field("error", max_length=30)
    message: str = Field(..., max_length=2000)
    stack: Optional[str] = Field(None, max_length=8000)
    url: Optional[str] = Field(None, max_length=500)
    version: Optional[str] = Field(None, max_length=40)


@router.post("", status_code=204, response_class=Response)
def client_log(entry: ClientLog, user: AuthUser = Depends(require_auth)):
    now = time.monotonic()
    q = _recent[user.id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) < PER_MINUTE:
        q.append(now)
        log.warning("Browser-Fehler (%s) bei %s, Benutzer %s, Version %s: %s%s", entry.kind, entry.url or "?",
                    user.username, entry.version or "?", entry.message,
                    f"\n{entry.stack}" if entry.stack else "")
    return Response(status_code=204)
