from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.auth_user import AuthState, resolve_auth
from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db(auth: AuthState = Depends(resolve_auth)):
    """
    Request-Session. Mit angemeldetem Benutzer ist sie auf dessen Daten
    zugeschnitten (session.info["user_id"], ausgewertet in app/tenancy.py) —
    jede ORM-Abfrage auf Karten/Sammlungen/Sealed sieht nur seine Zeilen,
    neue Zeilen bekommen seine user_id.
    """
    db = SessionLocal()
    if auth.user is not None:
        db.info["user_id"] = auth.user.id
    try:
        yield db
    finally:
        db.close()


def user_session(user_id):
    """Eigene Session für Hintergrund-Jobs im Namen eines Benutzers."""
    db = SessionLocal()
    if user_id is not None:
        db.info["user_id"] = user_id
    return db


async def run_with_session(fn, *args, **kwargs):
    """
    Führt eine async Service-Funktion mit eigener, frischer Session aus —
    EINE Routine für alle Hintergrund-/Cron-Einstiege (Issue #9). Services
    nehmen `db` als ersten Parameter statt SessionLocal selbst zu erzeugen;
    BackgroundTasks laufen nach dem Schließen der Request-Session und
    brauchen deshalb diese eigene. Ohne Benutzer = System-Kontext (alle Daten,
    z. B. der tägliche Preis-Cron).
    """
    db = SessionLocal()
    try:
        return await fn(db, *args, **kwargs)
    finally:
        db.close()
