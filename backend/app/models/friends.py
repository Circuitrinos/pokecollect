"""Freunde und geteilte Sammlungen.

Freundschaft: eine Zeile je Paar, vom Anfragenden zum Angefragten; status
"pending" bis der Angefragte annimmt, dann "accepted".
Freigabe: eine Sammlung ist für einen Freund sichtbar (nur ansehen).

Beide Tabellen gehören keinem einzelnen Benutzer — sie stehen bewusst nicht in
tenancy.OWNED_MODELS; jeder Endpunkt prüft Freundschaft und Freigabe selbst.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text, UniqueConstraint

from app.database import Base


class Friendship(Base):
    __tablename__ = "friendships"
    __table_args__ = (UniqueConstraint("requester_id", "addressee_id", name="uq_friendship_pair"),)

    id = Column(Integer, primary_key=True)
    requester_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    addressee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(Text, nullable=False, default="pending")   # pending | accepted
    created_at = Column(DateTime, default=datetime.utcnow)
    responded_at = Column(DateTime, nullable=True)


class CollectionShare(Base):
    __tablename__ = "collection_shares"

    collection_id = Column(Integer, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True)
    friend_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
