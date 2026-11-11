"""Freundschaften abfragen (für Freunde-API und geteilte Sammlungen)."""
from typing import Optional

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.models.collection import Collection
from app.models.friends import CollectionShare, Friendship
from app.models.user import User


def _pair(a: int, b: int):
    return or_(and_(Friendship.requester_id == a, Friendship.addressee_id == b),
               and_(Friendship.requester_id == b, Friendship.addressee_id == a))


def between(db: Session, a: int, b: int) -> Optional[Friendship]:
    return db.scalar(select(Friendship).where(_pair(a, b)))


def are_friends(db: Session, a: int, b: int) -> bool:
    f = between(db, a, b)
    return f is not None and f.status == "accepted"


def friend_ids(db: Session, uid: int) -> set[int]:
    rows = db.execute(
        select(Friendship.requester_id, Friendship.addressee_id)
        .where(Friendship.status == "accepted",
               or_(Friendship.requester_id == uid, Friendship.addressee_id == uid))
    ).all()
    return {b if a == uid else a for a, b in rows}


def active_users(db: Session, ids: set[int]) -> dict[int, User]:
    if not ids:
        return {}
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids), User.is_active.is_(True)))}


def remove_shares_between(db: Session, a: int, b: int) -> None:
    """Freigaben in beide Richtungen löschen (beim Entfreunden)."""
    for owner, friend in ((a, b), (b, a)):
        owned = select(Collection.id).where(Collection.user_id == owner)
        db.execute(
            delete(CollectionShare)
            .where(CollectionShare.friend_id == friend, CollectionShare.collection_id.in_(owned))
            .execution_options(all_users=True)
        )
