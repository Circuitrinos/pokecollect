"""Prüfung gespeicherter Scan-Fotos (Admin-Werkzeug „Schwache Scans").

Je Karte mit Scan-Foto eine Zeile: wie gut der Zuschnitt zu den Katalogbildern
der bestätigten Karte passt (Bild-Abgleich /verify) und — mit Originalfoto und
Ecken — vorgeschlagene bessere Ecken. Gehört keinem Benutzer (Admin-Sicht über
alle Konten), deshalb nicht in tenancy.OWNED_MODELS.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, Text

from app.database import Base


class ScanCheck(Base):
    __tablename__ = "scan_checks"

    card_id = Column(Integer, ForeignKey("pokemon_cards.id", ondelete="CASCADE"), primary_key=True)
    checked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    photo_key = Column(Text, nullable=False)          # Foto + Stand + Ecken: geändert → neu prüfen
    status = Column(Text, nullable=False, index=True)  # ok | low | no_reference | error | dismissed
    templates = Column(Integer, nullable=False, default=0)
    inliers = Column(Integer, nullable=False, default=0)
    art_inliers = Column(Integer, nullable=False, default=0)
    corner_err = Column(Float, nullable=True)
    geo_ok = Column(Boolean, nullable=False, default=False)
    suggested_quad = Column(Text, nullable=True)      # JSON [[x,y]×4], Bruchteile des Originals
