"""
Benutzerverwaltung (nur Admins): Liste mit Bestandszahlen, Admin-Recht und
Aktiv-Status umschalten. Angelegt werden Benutzer beim ersten SSO-Login;
gelöscht wird bewusst nicht (Deaktivieren ist umkehrbar, Löschen nähme die
ganze Sammlung samt Bildern mit).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.auth_user import AuthUser
from app.database import get_db
from app.models.card import PokemonCard
from app.models.collection import Collection
from app.models.sealed import SealedProduct
from app.models.user import User

router = APIRouter(prefix="/users", tags=["users"])


class UserRow(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    email: Optional[str]
    is_admin: bool
    is_active: bool
    sso_linked: bool
    created_at: Optional[datetime]
    last_login_at: Optional[datetime]
    owned_cards: int
    collections: int
    sealed: int


class UserPatch(BaseModel):
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None


def _counts(db: Session, model, owned_only: bool = False) -> dict[int, int]:
    stmt = select(model.user_id, func.count(model.id)).group_by(model.user_id)
    if owned_only:
        stmt = stmt.where(PokemonCard.besessen.is_(True))
    return dict(db.execute(stmt.execution_options(all_users=True)).all())


def _rows(db: Session) -> list[UserRow]:
    cards = _counts(db, PokemonCard, owned_only=True)
    colls = _counts(db, Collection)
    sealed = _counts(db, SealedProduct)
    return [
        UserRow(
            id=u.id, username=u.username, display_name=u.display_name, email=u.email,
            is_admin=bool(u.is_admin), is_active=bool(u.is_active), sso_linked=u.oidc_sub is not None,
            created_at=u.created_at, last_login_at=u.last_login_at,
            owned_cards=cards.get(u.id, 0), collections=colls.get(u.id, 0), sealed=sealed.get(u.id, 0),
        )
        for u in db.scalars(select(User).order_by(User.id)).all()
    ]


@router.get("", response_model=list[UserRow], dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    return _rows(db)


@router.patch("/{user_id}", response_model=list[UserRow], dependencies=[Depends(require_admin)])
def patch_user(user_id: int, data: UserPatch, me: AuthUser = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    if user.id == me.id and (data.is_admin is False or data.is_active is False):
        raise HTTPException(status_code=400, detail="Eigene Admin-Rechte/Zugang können nicht entzogen werden")
    if data.is_admin is not None:
        user.is_admin = data.is_admin
    if data.is_active is not None:
        user.is_active = data.is_active
    db.flush()
    if not db.scalar(select(func.count(User.id)).where(User.is_admin.is_(True), User.is_active.is_(True))):
        db.rollback()
        raise HTTPException(status_code=400, detail="Mindestens ein aktiver Admin muss bleiben")
    db.commit()
    return _rows(db)
