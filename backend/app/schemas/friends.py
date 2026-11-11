from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.collection import CollectionCardResponse


class FriendUser(BaseModel):
    id: int
    username: str
    display_name: Optional[str] = None


class Friend(FriendUser):
    since: Optional[datetime] = None


class FriendRequest(BaseModel):
    id: int
    user: FriendUser
    created_at: Optional[datetime] = None


class FriendsOverview(BaseModel):
    friends: list[Friend] = []
    incoming: list[FriendRequest] = []
    outgoing: list[FriendRequest] = []


class FriendRequestCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)


class SharesUpdate(BaseModel):
    friend_ids: list[int] = Field(default_factory=list, max_length=500)


class SharedCollection(BaseModel):
    id: int
    name: str
    beschreibung: Optional[str] = None
    owner: FriendUser
    karten_anzahl: int = 0
    binder_layout: str = "3x3"
    binder_slots: Optional[int] = None
    view_mode: str = "grid"
    grid_sort: str = "manual"
    grid_sort_reverse: bool = False
    grid_sort_then: str = ""


class SharedCollectionDetail(SharedCollection):
    cards: list[CollectionCardResponse] = []
