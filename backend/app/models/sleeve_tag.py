"""
Hüllen-Aufkleber (services/sleeve_tags.py): nummerierte QR-Aufkleber für die
Kartenhüllen. Gedruckt werden sie blanko auf Etikettenbögen; verknüpft wird
ein Aufkleber erst, wenn er auf einer Hülle klebt (Scan mit der Handykamera →
Karte wählen).

Die Nummern sind über alle Benutzer eindeutig (der Link auf dem Aufkleber
nennt nur die Nummer), gehören aber dem Benutzer, der den Bogen gedruckt hat
(tenancy.py filtert wie bei den Karten).
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text

from app.database import Base


class TagSheet(Base):
    """Ein gedruckter Etikettenbogen — für Nachdruck und Übersicht."""
    __tablename__ = "tag_sheets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    nr = Column(Integer, nullable=False)                 # laufende Bogen-Nr. je Benutzer
    bogen = Column(Text, nullable=False, default="LWS144")
    start_slot = Column(Integer, nullable=False, default=0)   # erste freie Stelle (Rest-Bogen)
    erstellt_am = Column(DateTime, default=datetime.utcnow)


class SleeveTag(Base):
    """Ein Aufkleber; card_id gesetzt, sobald er auf der Hülle einer Karte klebt."""
    __tablename__ = "sleeve_tags"

    serial = Column(Integer, primary_key=True, autoincrement=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    sheet_id = Column(Integer, ForeignKey("tag_sheets.id", ondelete="CASCADE"), nullable=False, index=True)
    slot = Column(Integer, nullable=False)               # Platz auf dem Bogen, 0-basiert
    # Karte gelöscht → Aufkleber wird wieder frei
    card_id = Column(Integer, ForeignKey("pokemon_cards.id", ondelete="SET NULL"),
                     nullable=True, unique=True)
    verknuepft_am = Column(DateTime, nullable=True)
