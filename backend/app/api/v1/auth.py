"""
Anmeldung (Mehrbenutzer).

- POST /auth/login            lokales Passwort — Notzugang des Primär-Admins
                              (APP_USERNAME + APP_PASSWORD_HASH bzw. In-App-Hash)
- GET  /auth/config           was die Login-Seite anbieten soll (auth-frei)
- GET  /auth/oidc/login       SSO-Einstieg → 302 zum Provider (auth-frei)
- GET  /auth/oidc/callback    Rückkehr vom Provider → 302 zu /auth/callback#token=…
- GET  /auth/me               wer bin ich (mit Token)
"""

import json
import logging
import os
import secrets
import time
from typing import Optional
from urllib.parse import quote, urlsplit

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from passlib.context import CryptContext
from sqlalchemy import select

from app.api.deps import require_auth
from app.auth_user import AuthUser, issue_token
from app.config import settings
from app.database import SessionLocal
from app.messages import localize
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.services import oidc
from app.services.users import LoginRefused, find_or_create_oidc_user, provision_new_user
from app.tenancy import primary_user_id

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Notzugang: Zugangsdaten aus Env (APP_USERNAME + APP_PASSWORD_HASH, bcrypt).
# Es gibt KEINEN Default-Hash — ohne Passwort UND ohne OIDC verweigert die App
# den Start (main.py::_ensure_password_configured, Issue #1).
_USERNAME = os.getenv("APP_USERNAME", "admin")

COOKIE = "pc_oidc"
COOKIE_PATH = "/api/v1/auth/oidc"
COOKIE_TTL = 600
CALLBACK_PATH = "/api/v1/auth/oidc/callback"


def _get_password_hash() -> str:
    """DB-Hash hat Vorrang vor der Env-Var (In-App-Passwortwechsel)."""
    try:
        from app.models.setting import AppSetting
        db = SessionLocal()
        try:
            row = db.get(AppSetting, "app_password_hash")
        finally:
            db.close()
        if row and row.value:
            return row.value
    except Exception:
        pass
    return os.getenv("APP_PASSWORD_HASH", "")


def password_login_enabled() -> bool:
    return bool(settings.password_login and _get_password_hash())


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest):
    if not password_login_enabled():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Passwort-Login ist deaktiviert")
    stored_hash = _get_password_hash()
    if data.username != _USERNAME or not pwd_context.verify(data.password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültige Anmeldedaten",
        )
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == _USERNAME))
        if user is None:
            pid = primary_user_id(db)
            user = db.get(User, pid) if pid is not None else None
        if user is None or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültige Anmeldedaten")
        token, expires = issue_token(user)
    finally:
        db.close()
    return TokenResponse(access_token=token, expires_in=expires)


@router.get("/config")
def auth_config():
    """Für die Login-Seite: welche Anmeldewege gibt es?"""
    return {
        "oidc_enabled": settings.oidc_enabled,
        "oidc_provider_name": settings.oidc_provider_name or "SSO",
        "password_login": password_login_enabled(),
    }


@router.get("/me", dependencies=[Depends(require_auth)])
def me(user: AuthUser = Depends(require_auth)):
    db = SessionLocal()
    try:
        row = db.get(User, user.id)
        return {
            "id": row.id,
            "username": row.username,
            "display_name": row.display_name,
            "email": row.email,
            "is_admin": bool(row.is_admin),
            "sso_linked": row.oidc_sub is not None,
            "oidc_enabled": settings.oidc_enabled,
            "oidc_provider_name": settings.oidc_provider_name or "SSO",
            "password_login": password_login_enabled(),
        }
    finally:
        db.close()


# ── OIDC ─────────────────────────────────────────────────────────────────────

def _scheme(request: Request) -> str:
    # Hinter Cloudflare-Tunnel → Caddy kommt die Anfrage per http an; das
    # ursprüngliche Schema steht in Cf-Visitor (Caddy reicht es unverändert durch).
    cfv = request.headers.get("cf-visitor")
    if cfv:
        try:
            scheme = json.loads(cfv).get("scheme")
            if scheme in ("http", "https"):
                return scheme
        except (ValueError, AttributeError):
            pass
    xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if xfp in ("http", "https"):
        return xfp
    return request.url.scheme


def _host(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return host.split(",")[0].strip()


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def _redirect_uri(request: Request) -> str:
    if settings.oidc_redirect_uri.strip():
        return settings.oidc_redirect_uri.strip()
    base = settings.public_url.strip().rstrip("/") or f"{_scheme(request)}://{_host(request)}"
    return base + CALLBACK_PATH


def _allowed_origins(redirect_uri: str) -> set[str]:
    raw = [settings.public_url, redirect_uri, *settings.cors_origins_list,
           *settings.oidc_allowed_origins.split(",")]
    return {o for o in (_origin(r.strip()) for r in raw if r and r.strip()) if o}


def _return_origin(request: Request, origin: str, redirect_uri: str) -> str:
    """
    Wohin das Token nach dem Login geht. "" = relativ (gleicher Host wie der
    Callback). Ein fremder Origin nur aus der Positivliste — sonst könnte ein
    präparierter Link das frische Token an eine fremde Seite schicken.
    """
    origin = _origin(origin or "")
    if not origin or urlsplit(origin).netloc.lower() == _host(request).lower():
        return ""
    return origin if origin in _allowed_origins(redirect_uri) else ""


def _safe_next(nxt: Optional[str]) -> str:
    if not nxt or not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
        return "/"
    return nxt


def _fail(ret: str, message: str, request: Request | None = None) -> RedirectResponse:
    if request is not None:
        message = localize(message, request.headers)
    resp = RedirectResponse(f"{ret}/login?sso_error={quote(message[:300])}", status_code=302)
    resp.delete_cookie(COOKIE, path=COOKIE_PATH)
    return resp


@router.get("/oidc/login")
async def oidc_login(request: Request, next: str = "/", origin: str = ""):
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="SSO ist nicht eingerichtet")
    redirect_uri = _redirect_uri(request)
    # Das Status-Cookie muss am Host des Callbacks liegen → bei Aufruf über
    # einen anderen Host (z. B. LAN-IP statt Domain) erst dorthin springen.
    cb_host = urlsplit(redirect_uri).netloc
    if cb_host and cb_host.lower() != _host(request).lower():
        query = f"?{request.url.query}" if request.url.query else ""
        if not origin:
            origin = f"{_scheme(request)}://{_host(request)}"
            query = f"?next={quote(_safe_next(next))}&origin={quote(origin)}"
        return RedirectResponse(f"{_origin(redirect_uri)}/api/v1/auth/oidc/login{query}", status_code=302)

    ret = _return_origin(request, origin, redirect_uri)
    try:
        disc = await oidc.discovery()
    except oidc.OIDCError as exc:
        return _fail(ret, str(exc), request)
    verifier, challenge = oidc.new_pkce()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    cookie = jwt.encode(
        {"st": state, "n": nonce, "cv": verifier, "ru": redirect_uri, "ret": ret,
         "nx": _safe_next(next), "exp": int(time.time()) + COOKIE_TTL},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    resp = RedirectResponse(oidc.authorization_url(disc, redirect_uri, state, nonce, challenge), status_code=302)
    resp.set_cookie(
        COOKIE, cookie, max_age=COOKIE_TTL, path=COOKIE_PATH, httponly=True,
        samesite="lax", secure=redirect_uri.startswith("https://"),
    )
    return resp


@router.get("/oidc/callback")
async def oidc_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
):
    try:
        ck = jwt.decode(
            request.cookies.get(COOKIE) or "",
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError:
        return _fail("", "Anmeldung abgelaufen oder unterbrochen — bitte erneut versuchen", request)
    ret = ck.get("ret") or ""
    if error:
        return _fail(ret, error_description or error, request)
    if not code or not state or not secrets.compare_digest(state, str(ck.get("st", ""))):
        return _fail(ret, "Ungültiger Anmeldestatus — bitte erneut versuchen", request)
    try:
        disc = await oidc.discovery()
        tokens = await oidc.exchange_code(disc, code, ck["ru"], ck["cv"])
        claims = await oidc.validate_id_token(disc, tokens["id_token"], ck["n"])
        if not claims.get("email") or "groups" not in claims or "preferred_username" not in claims:
            extra = await oidc.userinfo(disc, tokens.get("access_token"))
            if extra.get("sub") == claims.get("sub"):
                claims = {**extra, **claims}   # ID-Token gewinnt
        db = SessionLocal()
        try:
            user, created = find_or_create_oidc_user(db, claims)
            token, _ = issue_token(user)
            user_id = user.id
        finally:
            db.close()
    except (oidc.OIDCError, LoginRefused) as exc:
        log.warning("SSO-Login fehlgeschlagen: %s", exc)
        return _fail(ret, str(exc), request)
    if created:
        background_tasks.add_task(provision_new_user, user_id)
    resp = RedirectResponse(
        f"{ret}/auth/callback#token={quote(token)}&next={quote(ck.get('nx') or '/')}",
        status_code=302,
    )
    resp.delete_cookie(COOKIE, path=COOKIE_PATH)
    return resp
