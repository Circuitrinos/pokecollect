"""
Logdateien für die Fehlersuche, die einen Container-Neustart überleben.

In der Compose-Datei ``<Ordner>/logs:/app/logs`` einhängen; dann schreibt der
Dienst (rotierend, 10 MB × 5 Dateien):

  <dienst>.log          alles ab INFO (wie ``docker logs``)
  <dienst>-errors.log   Warnungen und Fehler, mit Traceback und Anfrage (Pfad, Benutzer, ID)
  <dienst>-access.log   jede Anfrage (nur die API)

LOG_DIR setzt einen anderen Ordner, LOG_LEVEL die Stufe (Standard INFO). Ohne
eingehängten Ordner bleibt es bei der Konsole.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5
_done: Optional[Path] = None


class _NotTwice(logging.Filter):
    """Unsere Anfrage-Protokollierung hat den Fehler schon mit Kontext geschrieben —
    die zweite Fassung von uvicorn („Exception in ASGI application") fällt weg."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (record.name.startswith("uvicorn") and getattr(exc, "_pokecollect_logged", False))


def _handler(path: Path, level: int) -> RotatingFileHandler:
    h = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True)
    h.setLevel(level)
    h.setFormatter(logging.Formatter(FORMAT))
    h.addFilter(_NotTwice())
    return h


def log_dir() -> Optional[Path]:
    """Ordner der Logdateien, oder None (nicht eingehängt / nicht beschreibbar)."""
    return _done


def setup(service: str, access: bool = True) -> Optional[Path]:
    """Konsole + (wenn vorhanden) Logdateien. Mehrfacher Aufruf ist harmlos."""
    global _done
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=FORMAT)
    root.setLevel(level)
    if _done is not None:
        return _done
    configured = os.environ.get("LOG_DIR")
    d = Path(configured or "/app/logs")
    if not configured and not d.is_dir():
        return None                                # nicht eingehängt: nur Konsole
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".{service}-write-test"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        logging.getLogger(__name__).warning("Keine Logdateien: %s ist nicht beschreibbar (%s) — nur Konsole", d, exc)
        return None
    main = _handler(d / f"{service}.log", logging.INFO)
    errors = _handler(d / f"{service}-errors.log", logging.WARNING)
    # uvicorn hängt eigene Handler an „uvicorn" (ohne Weitergabe an root)
    for name in ("", "uvicorn"):
        lg = logging.getLogger(name)
        lg.addHandler(main)
        lg.addHandler(errors)
    if access:
        logging.getLogger("uvicorn.access").addHandler(_handler(d / f"{service}-access.log", logging.INFO))
    _done = d
    logging.getLogger(__name__).info("Logdateien in %s (%s.log, %s-errors.log)", d, service, service)
    return d
