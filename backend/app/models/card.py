from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, Text, false, func
from sqlalchemy.orm import relationship

from app.database import Base


class PokemonCard(Base):
    __tablename__ = "pokemon_cards"

    id = Column(Integer, primary_key=True, index=True)
    # Besitzer (Mehrbenutzer): gefiltert zentral in app/tenancy.py
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    kartenname = Column(Text, nullable=False)
    pokedex_nr = Column(Integer, nullable=True, index=True)
    englischer_name = Column(Text, nullable=True)
    set_edition = Column(Text, nullable=True, index=True)
    karten_nr = Column(Text, nullable=True)
    seltenheit = Column(Text, nullable=True, index=True)
    kartenversion = Column(Text, nullable=True)
    folierung = Column(Text, nullable=True)
    # Muster der Folierung als EIGENE Dimension (#63, Owner-Entscheid):
    # Pokéball/Masterball/Cosmos/… — Folierung bleibt die preis-zuordenbare
    # Grundform (Normal/Holo/Reverse Holo). Alt-Werte („Reverse Holo – Pokéball")
    # werden per Light-Migration aufgeteilt (Expand; Contract später).
    muster = Column(Text, nullable=True)
    # „besitze diese Karte als 1st Edition" — eigenes Ja/Nein, unabhängig vom
    # TCGdex-variants_firstedition (das nur sagt „gibt es als 1st Edition"), #25.
    erste_edition = Column(Boolean, nullable=False, server_default=false(), default=False)
    # Play! Pokémon-Stempel (Prize Pack Series): gleiche Karte/Nummer wie im Set,
    # aber eigene Sammler-Variante mit eigenem Preis.
    prize_pack = Column(Boolean, nullable=False, server_default=false(), default=False)
    sprache = Column(Text, nullable=True, default="DE", index=True)
    besessen = Column(Boolean, default=False, index=True)
    wunschliste = Column(Boolean, default=False, index=True)
    im_pokedex = Column(Boolean, default=False, index=True)  # Pokédex-Repräsentant für diese Pokédex-Nr.
    prioritaet = Column(Text, nullable=True)  # Chase, Hoch, Mittel, Niedrig
    wert_eur = Column(Numeric(8, 2), nullable=True)
    wert_aktualisiert = Column(DateTime, nullable=True)
    # Persönlicher Einstand (#26): Kaufpreis + Kaufdatum, beide optional.
    # Unrealisierter G/V = wert_eur − kaufpreis_eur (nur wenn beide gesetzt);
    # es gibt bewusst KEIN Verkauf-/realisiert-Konzept.
    kaufpreis_eur = Column(Numeric(8, 2), nullable=True)
    kaufdatum = Column(Date, nullable=True)
    notizen = Column(Text, nullable=True)
    zustand = Column(Text, nullable=True)
    bild_pokedex_url = Column(Text, nullable=True)
    bild_karte_url = Column(Text, nullable=True)   # auto: TCGdex high.webp (vorher pokemon.com)
    bild_karte_pfad = Column(Text, nullable=True)
    bild_thumbnail_pfad = Column(Text, nullable=True)
    bild_original_pfad = Column(Text, nullable=True)   # ungeschnittenes Originalfoto
    # Ecken des Zuschnitts im Originalfoto (JSON [[x,y]×4], normiert 0..1) –
    # Trainingsdaten für die Karten-Erkennung (Scan-Datensatz-Export)
    bild_quad = Column(Text, nullable=True)
    # Entzerrtes Scan-Foto, das NICHT angezeigt wird (Anzeige = Katalogbild),
    # aber dem Bild-Abgleich als Referenz dient (matcher/app/learn.py)
    bild_scan_pfad = Column(Text, nullable=True)
    # ── TCGdex-Referenz + Varianten (v0.7.0, additiv) ────────────────────────
    tcgdex_card_id = Column(Text, nullable=True, index=True)   # "swsh3-136"
    set_id = Column(Text, nullable=True)                       # "swsh3"
    dex_id = Column(Integer, nullable=True)                    # aus dexId[0]
    illustrator = Column(Text, nullable=True, index=True)      # aus TCGdex
    variants_normal = Column(Boolean, nullable=True)
    variants_reverse = Column(Boolean, nullable=True)
    variants_holo = Column(Boolean, nullable=True)
    variants_firstedition = Column(Boolean, nullable=True)
    # Turnier-Legalität (services/regulation.py). regulation_mark = aufgedrucktes
    # Zeichen ("H"); legal_mark = für Standard maßgebliches Zeichen (eigenes
    # oder — Trainer/Spezial-Energie — das jüngste gleichnamige Reprint);
    # "-" = geprüft, kein Zeichen; NULL = (noch) unbekannt. basic_energy: immer legal.
    regulation_mark = Column(Text, nullable=True, index=True)
    legal_mark = Column(Text, nullable=True, index=True)
    basic_energy = Column(Boolean, nullable=False, server_default=false(), default=False)
    hinzugefuegt_am = Column(DateTime, default=datetime.utcnow)
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    preis_historie = relationship("PreisHistorie", back_populates="karte", cascade="all, delete-orphan")


class PreisHistorie(Base):
    __tablename__ = "preis_historie"

    id = Column(Integer, primary_key=True, index=True)
    karte_id = Column(Integer, ForeignKey("pokemon_cards.id", ondelete="CASCADE"), index=True)
    wert_eur = Column(Numeric(8, 2))
    quelle = Column(Text, nullable=True)
    erfasst_am = Column(DateTime, default=datetime.utcnow, index=True)

    karte = relationship("PokemonCard", back_populates="preis_historie")
