"""
Logdateien für die Fehlersuche, die einen Container-Neustart überleben.

In der Compose-Datei ``<Ordner>/logs:/app/logs`` einhängen; dann schreibt der
Dienst (rotierend, 10 MB × 5 Dateien):

  <dienst>.log          alles ab INFO (wie ``docker logs``)
  <dienst>-errors.log   Warnungen und Fehler, mit Traceback und Anfrage (Pfad, Benutzer, ID)
  <dienst>-access.log   jede Anfrage (nur die API)
  <dienst>-debug.log    nur mit LOG_LEVEL=DEBUG: alles, auch die Einzelheiten je Scan

LOG_DIR setzt einen anderen Ordner, LOG_LEVEL die Stufe (Standard INFO; DEBUG für
die Entwicklung). Ohne eingehängten Ordner bleibt es bei der Konsole.
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
# Bei DEBUG zu gesprächig (jedes Byte einer HTTP-Verbindung, jede SQL-Zeile …): bleiben bei INFO
QUIET = ("httpx", "httpcore", "hpack", "h11", "multipart", "python_multipart", "PIL", "urllib3",
         "asyncio", "sqlalchemy", "apscheduler", "watchfiles", "uvicorn.access")


class _NotTwice(logging.Filter):
    """Unsere Anfrage-Protokollierung hat den Fehler schon mit Kontext geschrieben —
    die zweite Fassung von uvicorn („Exception in ASGI application") fällt weg."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        return not (record.name.startswith("uvicorn") and getattr(exc, "_pokecollect_logged", False))


class _ReopeningHandler(RotatingFileHandler):
    """Rotierend — und wird die Datei gelöscht oder verschoben (Logs von Hand
    aufgeräumt), legt der nächste Eintrag sie neu an, statt still in die
    gelöschte Datei weiterzuschreiben (wie WatchedFileHandler)."""

    _ident: tuple[int, int] | None = None

    def _open(self):
        stream = super()._open()
        try:
            st = os.fstat(stream.fileno())
            self._ident = (st.st_dev, st.st_ino)
        except OSError:
            self._ident = None
        return stream

    def emit(self, record: logging.LogRecord) -> None:
        if self.stream is not None:
            try:
                st = os.stat(self.baseFilename)
                gone = (st.st_dev, st.st_ino) != self._ident
            except FileNotFoundError:
                gone = True
            except OSError:
                gone = False
            if gone:
                try:
                    self.stream.close()
                except OSError:
                    pass
                self.stream = None          # wird beim Schreiben neu geöffnet (delay)
        super().emit(record)


def _handler(path: Path, level: int) -> RotatingFileHandler:
    h = _ReopeningHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True)
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
    if not isinstance(level, int):
        level = logging.INFO
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=FORMAT)
    root.setLevel(level)
    if level <= logging.DEBUG:
        for name in QUIET:
            logging.getLogger(name).setLevel(logging.INFO)
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
    extra = [_handler(d / f"{service}-debug.log", logging.DEBUG)] if level <= logging.DEBUG else []
    # uvicorn hängt eigene Handler an „uvicorn" (ohne Weitergabe an root)
    for name in ("", "uvicorn"):
        lg = logging.getLogger(name)
        for h in (main, errors, *extra):
            lg.addHandler(h)
    if access:
        logging.getLogger("uvicorn.access").addHandler(_handler(d / f"{service}-access.log", logging.INFO))
    _done = d
    logging.getLogger(__name__).info("Logdateien in %s (%s.log, %s-errors.log%s), Stufe %s", d, service, service,
                                     f", {service}-debug.log" if extra else "", logging.getLevelName(level))
    return d
