"""Freunde (Anfrage per genauem Benutzernamen, annehmen, entfernen) und
Sammlungen, die Freunde mit dir geteilt haben (nur ansehen).

Freundschaften und Freigaben gehören keinem einzelnen Benutzer und laufen
nicht durch den automatischen Benutzer-Filter (tenancy.py): jede Route prüft
selbst, wer was sehen darf. Fremde Sammlungen werden nur mit all_users
gelesen, NACHDEM Freigabe und Freundschaft feststehen.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_auth
from app.auth_user import AuthUser
from app.database import get_db
from app.models.card import PokemonCard
from app.models.collection import Collection, collection_cards
from app.models.friends import CollectionShare, Friendship
from app.models.user import User
from app.schemas.collection import CollectionCardResponse
from app.schemas.card import CardResponse
from app.schemas.friends import (
    Friend, FriendRequest, FriendRequestCreate, FriendsOverview, FriendUser,
    SharedCollection, SharedCollectionDetail,
)
from app.services import friends as friends_svc

router = APIRouter(prefix="/friends", tags=["friends"])
shared_router = APIRouter(prefix="/shared", tags=["friends"])

# Was Freunde an geteilten Karten NICHT sehen: Kaufpreis, Kaufdatum, Notizen,
# Wunschlisten-Priorität und das unbearbeitete Originalfoto.
HIDDEN_FOR_FRIENDS = ("kaufpreis_eur", "kaufdatum", "notizen", "prioritaet", "bild_original_pfad")


def _user(u: User) -> FriendUser:
    return FriendUser(id=u.id, username=u.username, display_name=u.display_name)


def _overview(db: Session, uid: int) -> FriendsOverview:
    rows = db.scalars(
        select(Friendship).where((Friendship.requester_id == uid) | (Friendship.addressee_id == uid))
        .order_by(Friendship.created_at)
    ).all()
    other = {f.id: (f.addressee_id if f.requester_id == uid else f.requester_id) for f in rows}
    users = friends_svc.active_users(db, set(other.values()))
    out = FriendsOverview()
    for f in rows:
        u = users.get(other[f.id])
        if u is None:
            continue                       # deaktiviertes Konto: ausblenden
        if f.status == "accepted":
            out.friends.append(Friend(**_user(u).model_dump(), since=f.responded_at or f.created_at))
        elif f.addressee_id == uid:
            out.incoming.append(FriendRequest(id=f.id, user=_user(u), created_at=f.created_at))
        else:
            out.outgoing.append(FriendRequest(id=f.id, user=_user(u), created_at=f.created_at))
    out.friends.sort(key=lambda x: (x.display_name or x.username).lower())
    return out


@router.get("", response_model=FriendsOverview)
def overview(user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    return _overview(db, user.id)


@router.post("/requests", response_model=FriendsOverview, status_code=201)
def send_request(data: FriendRequestCreate, user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    name = data.username.strip()
    target = db.scalar(select(User).where(func.lower(User.username) == name.lower(), User.is_active.is_(True)))
    if target is None:
        raise HTTPException(status_code=404, detail="Kein Konto mit diesem Benutzernamen")
    if target.id == user.id:
        raise HTTPException(status_code=422, detail="Du kannst dich nicht selbst hinzufügen")
    existing = friends_svc.between(db, user.id, target.id)
    if existing is not None:
        if existing.status == "accepted":
            raise HTTPException(status_code=409, detail="Ihr seid schon befreundet")
        if existing.requester_id == user.id:
            raise HTTPException(status_code=409, detail="Anfrage ist schon gesendet")
        existing.status = "accepted"           # die andere Seite hat schon angefragt
        existing.responded_at = datetime.utcnow()
    else:
        db.add(Friendship(requester_id=user.id, addressee_id=target.id, status="pending"))
    db.commit()
    return _overview(db, user.id)


@router.post("/requests/{request_id}/accept", response_model=FriendsOverview)
def accept_request(request_id: int, user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    f = db.get(Friendship, request_id)
    if f is None or f.addressee_id != user.id or f.status != "pending":
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    f.status = "accepted"
    f.responded_at = datetime.utcnow()
    db.commit()
    return _overview(db, user.id)


@router.delete("/requests/{request_id}", response_model=FriendsOverview)
def drop_request(request_id: int, user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    """Ablehnen (Angefragter) oder zurückziehen (Anfragender)."""
    f = db.get(Friendship, request_id)
    if f is None or f.status != "pending" or user.id not in (f.requester_id, f.addressee_id):
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    db.delete(f)
    db.commit()
    return _overview(db, user.id)


@router.delete("/{friend_id}", response_model=FriendsOverview)
def remove_friend(friend_id: int, user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    f = friends_svc.between(db, user.id, friend_id)
    if f is None or f.status != "accepted":
        raise HTTPException(status_code=404, detail="Kein Freund")
    friends_svc.remove_shares_between(db, user.id, friend_id)
    db.delete(f)
    db.commit()
    return _overview(db, user.id)


# ── Mit mir geteilte Sammlungen ───────────────────────────────────────────────

def _shared_collections(db: Session, uid: int, collection_id: int | None = None) -> list[tuple[Collection, User]]:
    """Sammlungen, die Freunde mit uid teilen (Freundschaft muss noch bestehen)."""
    friends = friends_svc.active_users(db, friends_svc.friend_ids(db, uid))
    if not friends:
        return []
    q = (select(Collection)
         .join(CollectionShare, CollectionShare.collection_id == Collection.id)
         .where(CollectionShare.friend_id == uid, Collection.user_id.in_(list(friends.keys())))
         .order_by(Collection.name)
         .execution_options(all_users=True))
    if collection_id is not None:
        q = q.where(Collection.id == collection_id)
    return [(c, friends[c.user_id]) for c in db.scalars(q)]


def _shared(db: Session, coll: Collection, owner: User, count: int) -> dict:
    return dict(
        id=coll.id, name=coll.name, beschreibung=coll.beschreibung, owner=_user(owner),
        karten_anzahl=count, binder_layout=coll.binder_layout or "3x3", binder_slots=coll.binder_slots,
        view_mode=coll.view_mode or "grid", grid_sort=coll.grid_sort or "manual",
        grid_sort_reverse=bool(coll.grid_sort_reverse), grid_sort_then=coll.grid_sort_then or "",
    )


@shared_router.get("", response_model=list[SharedCollection])
def list_shared(user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    pairs = _shared_collections(db, user.id)
    ids = [c.id for c, _ in pairs]
    counts = dict(db.execute(
        select(collection_cards.c.collection_id, func.count(collection_cards.c.card_id))
        .where(collection_cards.c.collection_id.in_(ids))
        .group_by(collection_cards.c.collection_id)
    ).all()) if ids else {}
    return [SharedCollection(**_shared(db, c, o, counts.get(c.id, 0))) for c, o in pairs]


@shared_router.get("/{collection_id}", response_model=SharedCollectionDetail)
def get_shared(collection_id: int, user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    pairs = _shared_collections(db, user.id, collection_id)
    if not pairs:
        raise HTTPException(status_code=404, detail="Sammlung nicht gefunden")
    coll, owner = pairs[0]
    rows = db.execute(
        select(PokemonCard, collection_cards.c.position)
        .join(collection_cards, collection_cards.c.card_id == PokemonCard.id)
        .where(collection_cards.c.collection_id == coll.id, PokemonCard.user_id == owner.id)
        .order_by(collection_cards.c.position.nulls_last(), PokemonCard.id)
        .execution_options(all_users=True)
    ).all()
    hidden = dict.fromkeys(HIDDEN_FOR_FRIENDS)
    cards = [
        CollectionCardResponse(**{**CardResponse.model_validate(card).model_dump(), **hidden}, position=pos)
        for card, pos in rows
    ]
    return SharedCollectionDetail(**_shared(db, coll, owner, len(cards)), cards=cards)
