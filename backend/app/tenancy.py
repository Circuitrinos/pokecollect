"""
Mehrbenutzer-Trennung an EINER Stelle (statt Filter an ~120 Abfragen).

Jede Request-Session trägt session.info["user_id"] (database.get_db). Zwei
Session-Events setzen das durch:

- do_orm_execute: hängt an jede ORM-SELECT/UPDATE/DELETE-Anweisung ein
  with_loader_criteria für die Besitz-Tabellen — auch in Joins, Subqueries,
  db.get(), Relationship-Loads (Collection.cards) und query().update().
  Preisverlauf und Soll-Listen hängen über ihre Karte/Sammlung am Besitzer.
- before_flush: neue Karten/Sammlungen/Sealed-Produkte bekommen die user_id
  des Benutzers (ohne Benutzer-Kontext: die des Primär-Admins).

NICHT erfasst — und darum an den Aufrufstellen geprüft: Core-Anweisungen auf
den n:m-Tabellen (collection_cards, sealed_product_sets; laufen dort nur nach
Besitzprüfung von Sammlung/Karte/Produkt) und rohes SQL (text()).

Ohne user_id (Cron, Hintergrund-Jobs, Start) gilt der System-Kontext: alle
Zeilen. Bewusste Ausnahmen im Request lassen sich per
execution_options(all_users=True) markieren.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, with_loader_criteria

from app.models.card import PokemonCard, PreisHistorie
from app.models.collection import Collection, CollectionSoll
from app.models.sealed import SealedProduct
from app.models.user import User

log = logging.getLogger(__name__)

USER_KEY = "user_id"
OWNED_MODELS = (PokemonCard, Collection, SealedProduct)
OWNED_TABLES = ("pokemon_cards", "collections", "sealed_products")


def current_user_id(db: Session) -> Optional[int]:
    return db.info.get(USER_KEY)


@event.listens_for(Session, "do_orm_execute")
def _scope_to_user(state):
    uid = state.session.info.get(USER_KEY)
    if uid is None:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get("all_users"):
        return
    state.statement = state.statement.options(
        with_loader_criteria(PokemonCard, lambda cls: cls.user_id == uid, include_aliases=True),
        with_loader_criteria(Collection, lambda cls: cls.user_id == uid, include_aliases=True),
        with_loader_criteria(SealedProduct, lambda cls: cls.user_id == uid, include_aliases=True),
        with_loader_criteria(
            PreisHistorie,
            lambda cls: cls.karte_id.in_(
                select(PokemonCard.id).where(PokemonCard.user_id == uid)
            ),
            include_aliases=True,
        ),
        with_loader_criteria(
            CollectionSoll,
            lambda cls: cls.collection_id.in_(
                select(Collection.id).where(Collection.user_id == uid)
            ),
            include_aliases=True,
        ),
    )


@event.listens_for(Session, "before_flush")
def _stamp_owner(session, flush_context, instances):
    uid = session.info.get(USER_KEY)
    for obj in session.new:
        if not isinstance(obj, OWNED_MODELS):
            continue
        if uid is not None:
            obj.user_id = uid          # im Benutzer-Kontext immer der eigene
        elif obj.user_id is None:
            obj.user_id = _fallback_owner(session)


def _fallback_owner(session: Session) -> Optional[int]:
    cached = session.info.get("_fallback_owner")
    if cached is None:
        cached = primary_user_id(session)
        session.info["_fallback_owner"] = cached
    return cached


# ── Benutzer-Grundausstattung ────────────────────────────────────────────────

def primary_user_id(db: Session) -> Optional[int]:
    """Der Primär-Admin: ältester aktiver Admin (sonst ältester Benutzer)."""
    uid = db.scalar(
        select(User.id).where(User.is_admin.is_(True)).order_by(User.id).limit(1)
    )
    if uid is None:
        uid = db.scalar(select(User.id).order_by(User.id).limit(1))
    return uid


def ensure_primary_user(db: Session, username: str) -> int:
    """
    Upgrade aus dem Einzelbenutzer-Betrieb: gibt es noch keinen Benutzer, wird
    der bisherige (APP_USERNAME) als Admin angelegt. Sein erster SSO-Login
    verknüpft sich mit ihm (services/oidc.py) und übernimmt so den Bestand.
    """
    uid = primary_user_id(db)
    if uid is not None:
        return uid
    user = User(username=username or "admin", is_admin=True, is_active=True)
    db.add(user)
    db.commit()
    log.info("Primär-Admin %r angelegt (Bestand wird ihm zugeordnet)", user.username)
    return user.id


def adopt_orphans(conn_or_db, owner_id: int) -> dict[str, int]:
    """Zeilen ohne Besitzer (Alt-Bestand, Alt-Backup) dem Primär-Admin geben."""
    counts = {}
    for table in OWNED_TABLES:
        res = conn_or_db.execute(
            text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),  # Tabellenname fest
            {"uid": owner_id},
        )
        counts[table] = res.rowcount or 0
    return counts
