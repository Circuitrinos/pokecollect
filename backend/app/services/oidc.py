"""
OpenID Connect (Authorization Code + PKCE) — Login über Authentik o. ä.

Ablauf (api/v1/auth.py):
  1. GET /auth/oidc/login     → state/nonce/PKCE in ein signiertes, kurzlebiges
                                 Cookie, 302 zum Provider
  2. Provider → GET /auth/oidc/callback?code&state
  3. Code am Token-Endpoint einlösen (Back-Channel, client_secret + PKCE),
     ID-Token prüfen (Signatur via JWKS bzw. HS256 mit Client-Secret, iss,
     aud, exp, nonce), Benutzer suchen/anlegen (services/users.py)
  4. App-JWT ausstellen, 302 zu {web}/auth/callback#token=…

Die App bleibt beim bisherigen Bearer-Token-Modell (localStorage) — OIDC
ersetzt nur, WIE man an das Token kommt.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt

from app.config import settings

log = logging.getLogger(__name__)

_TTL = 3600
_discovery: Optional[dict] = None
_discovery_at = 0.0
_jwks: Optional[dict] = None
_jwks_at = 0.0

ASYMMETRIC_ALGS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}


class OIDCError(Exception):
    """Fehler im Login-Ablauf — Meldung ist für den Benutzer bestimmt."""


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15, follow_redirects=False)


async def discovery() -> dict:
    global _discovery, _discovery_at
    if _discovery is not None and time.monotonic() - _discovery_at < _TTL:
        return _discovery
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with _client() as c:
            r = await c.get(url)
            r.raise_for_status()
            doc = r.json()
    except Exception as exc:
        raise OIDCError(f"Anmeldedienst nicht erreichbar ({exc.__class__.__name__})") from exc
    for key in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(key):
            raise OIDCError(f"OIDC-Discovery unvollständig: {key} fehlt")
    _discovery, _discovery_at = doc, time.monotonic()
    return doc


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_pkce() -> tuple[str, str]:
    """→ (code_verifier, code_challenge S256)."""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def authorization_url(disc: dict, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.oidc_scopes or "openid",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{disc['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(disc: dict, code: str, redirect_uri: str, verifier: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.oidc_client_id,
        "code_verifier": verifier,
    }
    if settings.oidc_client_secret:
        data["client_secret"] = settings.oidc_client_secret
    try:
        async with _client() as c:
            r = await c.post(disc["token_endpoint"], data=data, headers={"Accept": "application/json"})
    except Exception as exc:
        raise OIDCError(f"Token-Endpoint nicht erreichbar ({exc.__class__.__name__})") from exc
    if r.status_code != 200:
        try:
            err = r.json()
            msg = err.get("error_description") or err.get("error") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise OIDCError(f"Code-Einlösung abgelehnt: {msg}")
    tokens = r.json()
    if not tokens.get("id_token"):
        raise OIDCError("Provider lieferte kein ID-Token (Scope 'openid' fehlt?)")
    return tokens


async def _jwks_doc(disc: dict, refresh: bool = False) -> dict:
    global _jwks, _jwks_at
    if not refresh and _jwks is not None and time.monotonic() - _jwks_at < _TTL:
        return _jwks
    try:
        async with _client() as c:
            r = await c.get(disc["jwks_uri"])
            r.raise_for_status()
            doc = r.json()
    except Exception as exc:
        raise OIDCError(f"Schlüssel des Anmeldedienstes nicht ladbar ({exc.__class__.__name__})") from exc
    _jwks, _jwks_at = doc, time.monotonic()
    return doc


async def _signing_key(disc: dict, id_token: str, alg: str):
    if alg == "HS256":
        # Authentik ohne gewählten Signaturschlüssel signiert mit dem Client-Secret.
        if not settings.oidc_client_secret:
            raise OIDCError("ID-Token ist HS256-signiert, aber OIDC_CLIENT_SECRET fehlt")
        return settings.oidc_client_secret
    if alg not in ASYMMETRIC_ALGS:
        raise OIDCError(f"Nicht unterstützter Signatur-Algorithmus: {alg}")
    kid = jwt.get_unverified_header(id_token).get("kid")
    for refresh in (False, True):   # unbekannte kid → einmal neu laden (Schlüsselwechsel)
        keys = (await _jwks_doc(disc, refresh=refresh)).get("keys") or []
        for k in keys:
            if kid is None or k.get("kid") == kid:
                try:
                    return jwt.PyJWK(k, algorithm=alg).key
                except jwt.PyJWKError:
                    continue
    raise OIDCError("Signaturschlüssel des ID-Tokens unbekannt")


async def validate_id_token(disc: dict, id_token: str, nonce: str) -> dict:
    try:
        alg = jwt.get_unverified_header(id_token).get("alg") or ""
    except jwt.PyJWTError as exc:
        raise OIDCError("ID-Token unlesbar") from exc
    key = await _signing_key(disc, id_token, alg)
    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[alg],
            audience=settings.oidc_client_id,
            issuer=disc["issuer"],
            leeway=60,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise OIDCError(f"ID-Token ungültig ({exc.__class__.__name__})") from exc
    if claims.get("nonce") != nonce:
        raise OIDCError("ID-Token passt nicht zu dieser Anmeldung (nonce)")
    return claims


async def userinfo(disc: dict, access_token: Optional[str]) -> dict:
    """Ergänzende Claims (E-Mail, Gruppen), falls das ID-Token sie nicht trägt."""
    endpoint = disc.get("userinfo_endpoint")
    if not endpoint or not access_token:
        return {}
    try:
        async with _client() as c:
            r = await c.get(endpoint, headers={"Authorization": f"Bearer {access_token}"})
        if r.status_code == 200:
            return r.json()
    except Exception as exc:  # optional — Login geht auch ohne
        log.info("OIDC userinfo nicht abrufbar: %s", exc)
    return {}


def reset_caches() -> None:
    """Für Tests."""
    global _discovery, _jwks
    _discovery = None
    _jwks = None
