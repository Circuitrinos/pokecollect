"""
Benutzer (Mehrbenutzer-Betrieb). Jeder Benutzer hat seine EIGENE Sammlung:
pokemon_cards, collections und sealed_products tragen eine user_id; das
Filtern übernimmt app/tenancy.py zentral für jede ORM-Abfrage einer
Request-Session. Katalog, Sets, Preise der Katalogkarten und die
Instanz-Einstellungen (API-Keys, Preis-Cron, Scan-Anbieter) bleiben geteilt.

Anmeldung: per OIDC (Authentik o. ä., oidc_sub = stabile Subject-ID des
Providers) oder — als Notzugang — per lokalem Passwort, das nur für den
Primär-Admin gilt (APP_USERNAME/APP_PASSWORD_HASH wie bisher).
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Text, false, true

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(Text, nullable=False, unique=True)
    email = Column(Text, nullable=True)
    display_name = Column(Text, nullable=True)
    # Stabile Subject-ID des OIDC-Providers; NULL = (noch) nicht verknüpft
    # (z. B. der beim Upgrade angelegte Primär-Admin vor seinem ersten SSO-Login).
    oidc_sub = Column(Text, nullable=True, unique=True)
    is_admin = Column(Boolean, nullable=False, server_default=false(), default=False)
    is_active = Column(Boolean, nullable=False, server_default=true(), default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)


class UserSetting(Base):
    """Persönliche Einstellungen (Anzeige, Sammel-Vorgaben) je Benutzer."""
    __tablename__ = "user_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=True)
