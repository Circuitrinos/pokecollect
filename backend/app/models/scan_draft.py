"""
Scan-Entwürfe: ein Foto, das gescannt, aber noch nicht fertig bestätigt ist —
die Prüfseite (erkannte Karten mit allen Änderungen) liegt hier, damit man
sie später oder an einem anderen Gerät fortsetzen kann (am Handy scannen, am
Rechner bestätigen).

Das Foto liegt in der Datenbank, nicht im öffentlich ausgelieferten
Bilder-Ordner: Entwürfe sind kurzlebig (fertig bestätigt → gelöscht, sonst
nach 30 Tagen) und nur für den Besitzer sichtbar. `state` ist der Zustand der
Prüfseite als JSON (Inhalt gehört der Web-App); der Server liest daraus nur
die Übersicht und die Merker „schon gespeichert" (services/scan_drafts.py).
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, LargeBinary, Text
from sqlalchemy.orm import deferred

from app.database import Base


class ScanDraft(Base):
    __tablename__ = "scan_drafts"

    id = Column(Integer, primary_key=True, index=True)
    # Besitzer (Mehrbenutzer): gefiltert zentral in app/tenancy.py
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    state = deferred(Column(Text, nullable=False))
    summary = Column(Text, nullable=True)            # JSON: offen/gespeichert/Namen (für die Liste)
    photo = deferred(Column(LargeBinary, nullable=False))
    photo_type = Column(Text, nullable=False, default="image/jpeg", server_default="image/jpeg")
    thumb = deferred(Column(LargeBinary, nullable=True))
    erstellt_am = Column(DateTime, default=datetime.utcnow)
    aktualisiert_am = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)
