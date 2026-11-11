"""
Decks (Spiel-Decks): eine Deckliste (DeckSlot, z. B. aus Pokémon TCG Live
importiert) plus die eigenen Karten, die gerade im Deck stecken
(pokemon_cards.deck_id — eine Karte steckt höchstens in EINEM Deck).

Logik (Legalität, Auffüllen, Import/Export): services/decks.py.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text

from app.database import Base

FORMATS = ("standard", "expanded", "unlimited")
SECTIONS = ("pokemon", "trainer", "energy")


class Deck(Base):
    __tablename__ = "decks"

    id = Column(Integer, primary_key=True, index=True)
    # Besitzer (Mehrbenutzer): gefiltert zentral in app/tenancy.py
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(Text, nullable=False)
    format = Column(Text, nullable=False, default="standard", server_default="standard")
    notizen = Column(Text, nullable=True)
    erstellt_am = Column(DateTime, default=datetime.utcnow)
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DeckSlot(Base):
    """
    Eine Zeile der Deckliste: count × Karte. tcgdex_card_id ist der gefundene
    Katalog-Druck (NULL = nicht gefunden); name/set_code/number halten fest, wie
    die Karte in der Liste steht (Export schreibt sie unverändert zurück).
    Keine Relationship nötig — das Löschen des Decks räumt per DB-CASCADE ab.
    """
    __tablename__ = "deck_slots"

    id = Column(Integer, primary_key=True, index=True)
    deck_id = Column(Integer, ForeignKey("decks.id", ondelete="CASCADE"), nullable=False, index=True)
    tcgdex_card_id = Column(Text, nullable=True, index=True)
    name = Column(Text, nullable=False)
    set_code = Column(Text, nullable=True)     # PTCGL-Kürzel, z. B. "TEF"
    number = Column(Text, nullable=True)       # aufgedruckte Nr. ohne führende Nullen
    count = Column(Integer, nullable=False, default=1)
    section = Column(Text, nullable=False, default="pokemon")   # pokemon | trainer | energy
    # Wie der Druck gefunden wurde: "set" (Kürzel + Nr.), "name" (nur Name), NULL = gar nicht
    match = Column(Text, nullable=True)
    position = Column(Integer, nullable=True)
