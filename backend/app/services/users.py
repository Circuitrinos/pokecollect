"""
Benutzer anlegen/verknüpfen beim SSO-Login + Grundausstattung neuer Konten.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.user import User
from app.tenancy import primary_user_id

log = logging.getLogger(__name__)


class LoginRefused(Exception):
    """SSO-Login verweigert (deaktiviert / keine automatische Anlage)."""


def legacy_username() -> str:
    return os.getenv("APP_USERNAME", "admin")


def _admin_list() -> tuple[set[str], bool]:
    """→ (Namen/E-Mails in Kleinschreibung, ob OIDC_ADMIN_USERS explizit gesetzt ist)."""
    raw = settings.oidc_admin_users.strip()
    explicit = bool(raw)
    names = raw if explicit else legacy_username()
    return {n.strip().lower() for n in names.split(",") if n.strip()}, explicit


def _unique_username(db: Session, wanted: str) -> str:
    base = (wanted or "user").strip()[:64] or "user"
    name, n = base, 1
    while db.scalar(select(User.id).where(func.lower(User.username) == name.lower())) is not None:
        n += 1
        name = f"{base}-{n}"
    return name


def claims_identity(claims: dict) -> dict:
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    username = (
        claims.get("preferred_username")
        or claims.get("nickname")
        or (email.split("@")[0] if email else None)
        or claims["sub"]
    )
    groups = claims.get("groups")
    return {
        "sub": str(claims["sub"]),
        "username": str(username).strip(),
        "email": email,
        "name": claims.get("name") if isinstance(claims.get("name"), str) else None,
        "groups": [str(g) for g in groups] if isinstance(groups, list) else [],
    }


def find_or_create_oidc_user(db: Session, claims: dict) -> tuple[User, bool]:
    """
    → (Benutzer, neu_angelegt). Reihenfolge:
      1. bekannte Subject-ID → derselbe Benutzer
      2. Primär-Admin übernehmen (Bestand aus dem Einzelbenutzer-Betrieb), wenn
         der Login in OIDC_ADMIN_USERS steht (Default: APP_USERNAME) — oder,
         solange OIDC_ADMIN_USERS NICHT gesetzt ist und noch kein Admin per
         SSO verknüpft ist, der erste SSO-Login überhaupt
      3. sonst neues, leeres Konto (OIDC_AUTO_CREATE)
    """
    ident = claims_identity(claims)
    created = False
    user = db.scalar(select(User).where(User.oidc_sub == ident["sub"]))
    if user is None:
        admins, explicit = _admin_list()
        listed = ident["username"].lower() in admins or (
            ident["email"] is not None and ident["email"].lower() in admins
        )
        candidate: Optional[User] = None
        if listed or not explicit:
            linked_admin = db.scalar(
                select(User.id).where(User.is_admin.is_(True), User.oidc_sub.isnot(None)).limit(1)
            )
            if listed or linked_admin is None:
                candidate = db.scalar(
                    select(User).where(
                        User.oidc_sub.is_(None),
                        func.lower(User.username) == ident["username"].lower(),
                    )
                )
                if candidate is None:
                    pid = primary_user_id(db)
                    primary = db.get(User, pid) if pid is not None else None
                    if primary is not None and primary.oidc_sub is None:
                        candidate = primary
        if candidate is not None:
            user = candidate
            user.is_admin = True
            log.info("SSO-Login %r übernimmt Konto %r (Admin)", ident["username"], user.username)
        else:
            if not settings.oidc_auto_create:
                raise LoginRefused("Für dieses Konto ist PokéCollect nicht freigeschaltet")
            user = User(username=_unique_username(db, ident["username"]), is_admin=listed, is_active=True)
            db.add(user)
            created = True
            log.info("Neuer Benutzer %r per SSO angelegt", user.username)
        user.oidc_sub = ident["sub"]
    if not user.is_active:
        raise LoginRefused("Benutzer ist deaktiviert")
    if settings.oidc_admin_group and settings.oidc_admin_group in ident["groups"]:
        user.is_admin = True
    user.email = ident["email"] or user.email
    user.display_name = ident["name"] or user.display_name
    user.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user, created


async def provision_new_user(user_id: int) -> None:
    """Neues Konto: alle Pokédex-Platzhalter anlegen (wie „Pokédex auffüllen")."""
    from app.database import user_session
    from app.services import pokedex_fill

    db = user_session(user_id)
    try:
        try:
            names = await pokedex_fill.fetch_species_names()
        except Exception as exc:
            log.info("PokeAPI-Namen nicht ladbar (%s) — nehme Namen aus dem Bestand", exc)
            names = pokedex_fill.names_from_placeholders(db)
        if names:
            pokedex_fill.fill_pokedex(db, names)
    except Exception:
        log.exception("Grundausstattung für Benutzer %s fehlgeschlagen", user_id)
    finally:
        db.close()
