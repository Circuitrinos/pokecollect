"""
Wer ruft an? — Auflösung des Bearer-JWT zu einem Benutzer (Mehrbenutzer).

Eine Abhängigkeit für alle: get_db (Session auf den Benutzer zuschneiden) und
require_auth/require_admin (api/deps.py) hängen an `resolve_auth`. FastAPI
cacht Abhängigkeiten je Request — das Token wird also nur einmal geprüft.

Bewusst OHNE Exception bei fehlendem/ungültigem Token: auth-freie Routen
(Login, OIDC-Callback) bekommen vom Web-Client trotzdem ein (evtl. altes)
Token mitgeschickt und dürfen daran nicht scheitern. Die Ablehnung macht
require_auth anhand von `error`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthUser:
    id: int
    username: str
    is_admin: bool


@dataclass(frozen=True)
class AuthState:
    user: Optional[AuthUser] = None
    error: Optional[str] = None   # None + user None = kein Token


def issue_token(user) -> tuple[str, int]:
    """App-JWT für einen Benutzer (sub = Name, uid = ID). → (Token, Sekunden)."""
    from datetime import datetime, timedelta

    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    token = jwt.encode(
        {"sub": user.username, "uid": user.id, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token, settings.jwt_expire_minutes * 60


def _lookup(uid: Optional[int], sub: str):
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        user = None
        if isinstance(uid, int):
            user = db.get(User, uid)
            # uid und Name müssen zusammenpassen (Restore/Neuanlage kann IDs
            # umverteilen — dann lieber neu anmelden als fremde Daten sehen).
            if user is not None and user.username != sub:
                user = None
        elif sub:
            # Alt-Token aus der Einzelbenutzer-Zeit (sub = APP_USERNAME, keine uid).
            user = db.scalar(select(User).where(func.lower(User.username) == sub.lower()))
        if user is None:
            return None
        return AuthUser(id=user.id, username=user.username, is_admin=bool(user.is_admin)), bool(user.is_active)
    finally:
        db.close()


def resolve_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer)) -> AuthState:
    if not credentials:
        return AuthState()
    try:
        # #117: exp und sub sind Pflicht — ein Token ohne Ablauf liefe sonst nie ab.
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return AuthState(error="Ungültiges Token")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        return AuthState(error="Ungültiges Token")
    found = _lookup(payload.get("uid"), sub)
    if found is None:
        return AuthState(error="Ungültiges Token")
    user, active = found
    if not active:
        return AuthState(error="Benutzer deaktiviert")
    return AuthState(user=user)
