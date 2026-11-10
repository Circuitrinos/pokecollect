"""
Tägliche Cron-Jobs:
  - Preisupdate aller besessenen Karten (Stunde aus Setting price_update_hour,
    Default 03:00; Änderung wird nach API-Neustart aktiv)
  - Katalog-Sync + Enrichment (04:00)
"""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, run_with_session
from app.models.card import PokemonCard
from app.models.setting import AppSetting
from app.services.pricing import refresh_prices_for_cards, refresh_sealed_prices

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _daily_price_update(db: Session):
    enabled_row = db.get(AppSetting, "price_update_enabled")
    if enabled_row and enabled_row.value == "false":
        log.info("Preisupdate deaktiviert – übersprungen.")
        return
    log.info("Starte täglichen Preisupdate …")
    ids = list(db.scalars(
        select(PokemonCard.id).where(PokemonCard.besessen == True)  # noqa: E712
    ).all())
    if ids:
        await refresh_prices_for_cards(db, ids)
    else:
        log.info("Keine besessenen Karten – nichts zu aktualisieren.")
    # Sealed: Auto-Wert für katalog-verknüpfte Produkte (#46).
    await refresh_sealed_prices(db)


async def _daily_catalog_sync(db: Session):
    """Sets + Katalog-Basis aktualisieren, einen Schwung anreichern, die
    €-Preise bereits angereicherter Zeilen rollierend nachsehen (#66) und
    zuletzt den lokalen Bild-Cache in einer Etappe füllen (#43)."""
    from app.services.set_sync import sync_sets
    from app.services.catalog import sync_catalog, enrich_catalog, refresh_catalog_eur
    from app.services.catalog_images import run_catalog_image_cache
    log.info("Starte täglichen Katalog-Sync …")

    # Je Schritt ein eigener Fang (#75): Vorher lagen alle in EINEM try/except —
    # scheiterte die Anreicherung, lief der €-Repass in derselben Nacht gar nicht
    # mehr, obwohl er mit dem Fehler nichts zu tun hat. Gemessen an einer
    # einzelnen kaputten Karte: „€-Repass überhaupt gelaufen = False".
    async def schritt(name: str, coro) -> bool:
        try:
            await coro
            return True
        except Exception as exc:  # noqa: BLE001 — ein Schritt darf die folgenden nicht mitreißen
            log.error("Katalog-Sync, Schritt %s fehlgeschlagen: %s", name, exc)
            # Panel-Fund BLOCKER: Alle Schritte teilen SICH DIESELBE Session.
            # Ohne rollback() steht sie nach einem DB-Fehler auf „aborted", und
            # jeder Folgeschritt scheitert an InFailedSqlTransaction — der Fang
            # hülfe dann nur gegen Fehler, die die Session gar nicht berühren.
            # Schlimmer noch: Ohne rollback() schreibt das commit() des NÄCHSTEN
            # Schritts die halbfertige Arbeit des abgebrochenen mit.
            db.rollback()
            return False

    sets_ok = await schritt("Sets", sync_sets())
    if sets_ok:
        await schritt("Katalog-Basis", sync_catalog(db))
    else:
        # Die Katalog-Basis liest die Set-Metadaten; ohne frische Sets schriebe
        # sie für brandneue Sets NULL-Codes in den Katalog (Panel-Fund KLEIN).
        # Sie heilt in der nächsten Nacht, der halbe Zustand entsteht gar nicht erst.
        log.warning("Katalog-Basis übersprungen — die Sets sind nicht aktuell.")
    await schritt("Anreicherung", enrich_catalog(db, limit=2000))
    # #66: eigenes, kleineres Budget — die Erstanreicherung behält mit 2000
    # Vorrang, aber der €-Repass läuft NEBENHER mit; sonst kämen die schon
    # angereicherten ~11,8k Zeilen erst nach Abschluss der Anreicherung dran.
    await schritt("€-Repass", refresh_catalog_eur(db, limit=1000))
    # Bilder, die TCGdex fehlen: offizielle Datenbank (pokemon.com), ersetzt
    # auch TCGplayer-Produktfotos (image_fallback.py)
    from app.services.image_fallback import fill_catalog_from_pokemon_com
    await schritt("pokemon.com-Bilder", fill_catalog_from_pokemon_com(db, limit=500))
    # Regulation Marks für vor dieser Version angereicherte Zeilen nachholen
    # (eigene Karten zuerst) und die Legalität eigener Karten neu rechnen —
    # neue gleichnamige Reprints machen alte Trainer wieder Standard-legal.
    from app.services import regulation
    await schritt("Regulation Marks", regulation.backfill_catalog(db, limit=3000))
    await schritt("Legalität", _sync(regulation.refresh_cards, db))
    # #43: eigenes, noch kleineres Budget — ein Bild-Download ist teurer als
    # ein JSON-Preis-Abruf (echter Dateidownload + Schreiben + PIL-Validierung).
    # Lädt nur, wenn die Einstellung catalog_image_cache_level das erlaubt
    # ("urls", der Default, lädt nichts). Panel-Nacharbeit #43: 60/Nacht ergäbe
    # bei Stufe "all" (voller TCGdex-Katalog, NICHT die ~1.025 eigenen Karten
    # aus CLAUDE.md — der €-Repass-Kommentar drei Zeilen weiter oben nennt
    # dafür ~11,8k bereits angereicherte Zeilen) ≈16 Monate bis zum vollen
    # Katalog; 500 ≈2 Monate, ~50 MB/Nacht sequenziell — der €-Repass hat bei
    # vergleichbarem Katalogumfang analog limit=1000, ein Bild-Download ist
    # aber teurer je Zeile (echter Dateidownload statt JSON-Abruf).
    await schritt("Bild-Cache", run_catalog_image_cache(db, limit=500))


async def _sync(fn, *args):
    """Synchronen Schritt in schritt() einhängen (der erwartet ein Awaitable)."""
    return fn(*args)


async def _regulation_startup(db: Session):
    """Einmal kurz nach dem Start: Regulation Marks eigener Karten nachholen,
    falls deren Katalogzeilen sie noch nicht haben (Update auf diese Version)."""
    from app.services import regulation
    try:
        await regulation.backfill_catalog(db, limit=5000, owned_only=True)
        await regulation.backfill_catalog(db, limit=3000)
    except Exception as exc:  # noqa: BLE001 — nie den Betrieb stören
        db.rollback()
        log.warning("Regulation-Nachholer beim Start fehlgeschlagen: %s", exc)


async def _regulation_startup_job():
    await run_with_session(_regulation_startup)


def _price_update_hour(db: Session) -> int:
    """Stunde aus dem Setting price_update_hour (0–23), Default 3."""
    try:
        row = db.get(AppSetting, "price_update_hour")
        if row and row.value and row.value.strip().isdigit():
            return min(23, max(0, int(row.value.strip())))
    except Exception as exc:  # DB evtl. noch nicht bereit → Default nutzen
        log.warning("price_update_hour nicht lesbar (%s) – nutze 03:00.", exc)
    return 3


async def _price_update_job():
    await run_with_session(_daily_price_update)


async def _catalog_sync_job():
    await run_with_session(_daily_catalog_sync)


def start_scheduler():
    db = SessionLocal()
    try:
        hour = _price_update_hour(db)
    finally:
        db.close()
    scheduler.add_job(_price_update_job, "cron", hour=hour, minute=0)
    scheduler.add_job(_catalog_sync_job, "cron", hour=4, minute=0)
    if settings.regulation_backfill_on_start:
        scheduler.add_job(_regulation_startup_job, "date",
                          run_date=datetime.now() + timedelta(seconds=60))
    scheduler.start()
    log.info("Cron-Scheduler gestartet (Preise %02d:00, Katalog 04:00)", hour)
