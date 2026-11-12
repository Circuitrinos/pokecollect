"""
Daten-Export & Backup/Restore (Issue #17).

- GET  /data/export.csv  — alle Kartenfelder als CSV (UTF-8 mit BOM, Semikolon)
- GET  /data/backup      — ZIP: data.json (ALLE Tabellen, generisch) + images/
- POST /data/restore     — ZIP-Upload, validieren, DESTRUKTIV einspielen

Alles hängt unter dem Auth-Zwang (api/v1/__init__.py, Issue #1). Das Backup
ist ein PERSÖNLICHES Voll-Backup und enthält bewusst auch die Settings-Zeilen
inkl. Passwort-Hash (bcrypt — kein Klartext-Secret, aber nicht öffentlich
teilen). Restore ist destruktiv und verlangt confirm="JA_WIRKLICH".
"""

import codecs
import csv
import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import Integer, func, select, text
from starlette.background import BackgroundTask

from app.api.deps import require_admin, require_auth
from app.auth_user import AuthUser
from app.config import settings
from app.database import Base, engine, user_session
from app.models.card import PokemonCard
from app.services import bild_spiegel, scan_export
from app.services.card_images import MEDIA_PATH_COLUMNS, safe_media_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data", tags=["data"])

# Versionsfeld des Backup-Formats — Restore prüft darauf. Bei inkompatiblen
# Format-Änderungen hochzählen und im Restore eine Migration/Absage einbauen.
BACKUP_FORMAT = 1
RESTORE_MAX_BYTES = 500 * 1024 * 1024  # 500 MB
RESTORE_CONFIRM = "JA_WIRKLICH"


# ── Hilfen ────────────────────────────────────────────────────────────────────

def _jsonify(value):
    """DB-Wert → JSON-serialisierbar (datetime/Decimal)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _csv_cell(value, english: bool = False) -> str:
    """DB-Wert → Excel-freundliche CSV-Zelle (deutsches Excel: Semikolon+Komma;
    englisches Excel: Komma+Punkt, yes/no)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        if english:
            return "yes" if value else "no"
        return "ja" if value else "nein"
    if isinstance(value, Decimal):
        return str(value) if english else str(value).replace(".", ",")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if english and isinstance(value, str):
        return _CSV_VALUES_EN.get(value, value)
    return str(value)


# Englischer Export: verständliche Spaltennamen statt der deutschen DB-Namen
# (nicht genannte Spalten behalten ihren Namen) und englische Auswahlwerte.
_CSV_HEADERS_EN = {
    "user_id": "user_id", "kartenname": "card_name", "pokedex_nr": "pokedex_no",
    "englischer_name": "english_name", "set_edition": "set", "karten_nr": "card_number",
    "seltenheit": "rarity", "kartenversion": "card_version", "folierung": "foiling",
    "muster": "pattern", "erste_edition": "first_edition", "sprache": "language",
    "besessen": "owned", "wunschliste": "wishlist", "im_pokedex": "in_pokedex",
    "prioritaet": "priority", "wert_eur": "value", "wert_aktualisiert": "value_updated",
    "kaufpreis_eur": "purchase_price", "kaufdatum": "purchase_date", "notizen": "notes",
    "zustand": "condition", "bild_pokedex_url": "pokedex_image_url", "bild_karte_url": "card_image_url",
    "bild_karte_pfad": "card_image_path", "bild_thumbnail_pfad": "thumbnail_path",
    "bild_original_pfad": "original_photo_path", "bild_quad": "photo_corners",
    "bild_scan_pfad": "scan_photo_path", "hinzugefuegt_am": "added_at", "aktualisiert_am": "updated_at",
}
_CSV_VALUES_EN = {
    "Hoch": "High", "Mittel": "Medium", "Niedrig": "Low",
    "Sterne": "Stars", "Energie": "Energy", "Sonstiges": "Other",
}


# ── CSV-Export ────────────────────────────────────────────────────────────────

@router.get("/export.csv")
def export_csv(
    user: AuthUser = Depends(require_auth),
    lang: str = Query("DE", pattern="^(DE|EN)$"),
):
    """
    Alle PokemonCard-Felder des angemeldeten Benutzers als CSV. UTF-8 MIT BOM + Semikolon-Delimiter,
    damit (deutsches) Excel die Datei per Doppelklick korrekt öffnet. lang=EN: Komma-Delimiter,
    Dezimalpunkt, englische Spaltennamen — so öffnet englisches Excel/Sheets sie richtig.
    """
    english = lang == "EN"
    columns = [c.name for c in PokemonCard.__table__.columns]

    # Zeilen VOR dem Response-Bau materialisieren: seit FastAPI 0.106 werden
    # yield-Dependencies (get_db) geschlossen, bevor ein StreamingResponse-
    # Generator läuft — deshalb hier eine eigene, kurze Session.
    db = user_session(user.id)   # nur die eigenen Karten (app/tenancy.py)
    try:
        rows = [
            [_csv_cell(getattr(card, c), english) for c in columns]
            for card in db.query(PokemonCard).order_by(PokemonCard.id).all()
        ]
    finally:
        db.close()

    def _iter():
        yield codecs.BOM_UTF8  # BOM → Excel erkennt UTF-8
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter="," if english else ";", lineterminator="\r\n")
        writer.writerow([_CSV_HEADERS_EN.get(c, c) for c in columns] if english else columns)
        for row in rows:
            writer.writerow(row)
        yield buf.getvalue().encode("utf-8")

    filename = f"pokecollect-export-{date.today().isoformat()}.csv"
    return StreamingResponse(
        _iter(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Backup ───────────────────────────────────────────────────────────────────

# Zwischenstände, kein Bestand (Scan-Entwürfe: Prüfseite + Foto als bytea) —
# nicht im Backup; ein Restore leert sie wie jede Tabelle.
TRANSIENT_TABLES = {"scan_drafts"}


def _dump_all_tables() -> dict[str, list[dict]]:
    """
    ALLE Tabellen generisch dumpen (Base.metadata.sorted_tables = FK-Ordnung).
    Erfasst damit auch n:m-Tabellen ohne Model-Klasse (collection_cards) und
    künftige Tabellen automatisch.
    """
    dump: dict[str, list[dict]] = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name in TRANSIENT_TABLES:
                continue
            result = conn.execute(table.select())
            dump[table.name] = [
                {k: _jsonify(v) for k, v in row.items()}
                for row in result.mappings()
            ]
    return dump


@router.get("/backup", dependencies=[Depends(require_admin)])
def backup():
    """
    Voll-Backup als ZIP: data.json (alle Tabellen + Versionsfeld) + der
    komplette images/-Ordner. Wird in eine Temp-Datei gebaut (Bilder können
    hunderte MB sein) und nach dem Senden gelöscht.
    """
    payload = {
        "format": BACKUP_FORMAT,
        "app_version": settings.app_version,
        "created_at": datetime.utcnow().isoformat(),
        "tables": _dump_all_tables(),
    }

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("data.json", json.dumps(payload, ensure_ascii=False))
            images_dir = Path(settings.images_dir)
            if images_dir.is_dir():
                for p in sorted(images_dir.rglob("*")):
                    if p.is_file():
                        rel = p.relative_to(images_dir).as_posix()
                        # #120: die Ablage des Bild-Spiegels ist Zwischenspeicher
                        # fremder Bilder, kein Bestand — sie wird bei Bedarf neu
                        # geholt, und ihre Namen hängen am JWT-Geheimnis.
                        if rel.startswith(f"{bild_spiegel.UNTERORDNER}/"):
                            continue
                        zf.write(p, arcname=f"images/{rel}")
        tmp.close()
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise

    filename = f"pokecollect-backup-{date.today().isoformat()}.zip"
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(os.unlink, tmp.name),
    )


# ── Restore ──────────────────────────────────────────────────────────────────

def _validate_payload(raw: bytes) -> dict:
    """Struktur + Versionsfeld prüfen — VOR jeder destruktiven Aktion."""
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="data.json ist kein gültiges JSON")
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), dict):
        raise HTTPException(status_code=400, detail="data.json hat nicht die erwartete Struktur (tables fehlt)")
    if "app_version" not in payload:
        raise HTTPException(status_code=400, detail="data.json ohne Versionsfeld (app_version) — kein PokéCollect-Backup?")
    if payload.get("format") != BACKUP_FORMAT:
        raise HTTPException(
            status_code=400,
            detail=f"Backup-Format {payload.get('format')!r} wird nicht unterstützt (erwartet: {BACKUP_FORMAT})",
        )
    for name, rows in payload["tables"].items():
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise HTTPException(status_code=400, detail=f"Tabelle {name!r} in data.json ist keine Zeilenliste")
    return payload


def _restore_tables(payload: dict) -> dict[str, int]:
    """
    DESTRUKTIV: alle Tabellen leeren (umgekehrte FK-Ordnung), dann aus dem
    Backup befüllen (FK-Ordnung via sorted_tables). Eine Transaktion —
    schlägt etwas fehl, bleibt die alte DB unangetastet.
    """
    tables = list(Base.metadata.sorted_tables)
    counts: dict[str, int] = {}
    backup_tables: dict = payload["tables"]

    unknown = set(backup_tables) - {t.name for t in tables}
    if unknown:
        logger.warning("Restore: unbekannte Tabellen im Backup werden ignoriert: %s", sorted(unknown))

    # Mehrbenutzer: ein Backup aus der Einzelbenutzer-Zeit hat keine Benutzer.
    # Dann bleiben die bestehenden Benutzer erhalten und der eingespielte
    # Bestand geht an den Primär-Admin (adopt_orphans unten).
    keep = set() if "users" in backup_tables else {"users", "user_settings"}

    with engine.begin() as conn:
        for table in reversed(tables):
            if table.name not in keep:
                conn.execute(table.delete())
        for table in tables:
            if table.name in keep:
                continue
            rows = backup_tables.get(table.name, [])
            # Auf die Spalten der Tabelle normalisieren: fremde Keys fallen
            # weg, fehlende werden NULL (executemany braucht homogene Dicts).
            col_names = [c.name for c in table.columns]
            prepared = [{c: row.get(c) for c in col_names} for row in rows]
            # Traversal-Schutz (#37): Medienpfad-Spalten aus dem (potenziell
            # manipulierten) Backup validieren. Ein `../…`- oder absoluter Pfad
            # würde sonst als DB-Wert landen und ein späteres unlink könnte eine
            # Datei ausserhalb des Bildordners treffen → hier auf NULL neutralisieren.
            media_cols = MEDIA_PATH_COLUMNS & set(col_names)
            for prow in prepared:
                for mc in media_cols:
                    if prow.get(mc) is not None and safe_media_path(prow[mc]) is None:
                        prow[mc] = None
            if prepared:
                conn.execute(table.insert(), prepared)
            counts[table.name] = len(prepared)

        # PostgreSQL-Sequenzen auf MAX(id) nachziehen — sonst kollidiert der
        # nächste INSERT nach dem Restore mit bestehenden IDs.
        for table in tables:
            pk_cols = list(table.primary_key.columns)
            if len(pk_cols) != 1 or not isinstance(pk_cols[0].type, Integer):
                continue
            col = pk_cols[0]
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"),
                {"t": table.name, "c": col.name},
            ).scalar()
            if seq:
                # Tabellen-/Spaltennamen stammen aus den eigenen Models (kein User-Input).
                conn.execute(text(
                    f"SELECT setval('{seq}', COALESCE((SELECT MAX({col.name}) FROM {table.name}), 1), "
                    f"(SELECT MAX({col.name}) FROM {table.name}) IS NOT NULL)"
                ))

        # Zeilen ohne Besitzer → Primär-Admin (Alt-Backup); fehlt jeder Benutzer
        # (Backup mit leerer users-Tabelle), legt der nächste Start ihn an.
        from app.tenancy import adopt_orphans
        owner = conn.execute(text(
            "SELECT id FROM users ORDER BY is_admin DESC, id LIMIT 1"
        )).scalar()
        if owner is not None:
            adopt_orphans(conn, owner)
    return counts


def _restore_images(zf: zipfile.ZipFile) -> int:
    """images/-Ordner leeren und aus dem ZIP zurückschreiben (Traversal-sicher)."""
    images_dir = Path(settings.images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    for child in images_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    written = 0
    for info in zf.infolist():
        name = info.filename
        if info.is_dir() or not name.startswith("images/"):
            continue
        rel = PurePosixPath(name).relative_to("images")
        if rel.is_absolute() or ".." in rel.parts:
            raise HTTPException(status_code=400, detail=f"Unsicherer Pfad im ZIP: {name}")
        target = images_dir.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        written += 1
    return written


_DATASET_README = """PokéCollect scan dataset

Every card saved from a scan (its straightened photo and the whole photo it
was cut from), for all users:

  labels.jsonl   one JSON object per card photo:
                   card_id    TCGdex id the user confirmed (e.g. sv03-125)
                   name, set_id, set, number, language, prize_pack
                   crop       straightened card photo
                   original   the whole photo it was cut from (shared by all cards of one photo)
                   quad       crop corners in the original, normalised 0..1, or null (older scans);
                              corrected corners replace the old ones ("Fix crop" in the app)
                   check      scan quality check, or null if not run: status (ok | low |
                              no_reference | dismissed), inliers and corner_err against the
                              card's catalog image (corner_err = share of the card diagonal)
                   foiling    Normal | Holo | Reverse Holo (as the user set it), pattern the
                              foil pattern (Pokéball, Masterball, Cosmos, Etched, …) or null,
                              version, rarity, first_edition
                   stamp_quad where the Play! Pokémon stamp is on the crop (4 corners,
                              0..1 of the crop), marked by hand; null if not marked
                   reference  the card's catalog image in references/ (usually the plain
                              print), or null when it isn't cached on the server;
                              reference_url where to download it otherwise
  photos.jsonl   one JSON object per whole photo: original, width, height, the cards
                 cut from it (id, card_id, quad) and labeled (true when every card
                 has corners)
  crops/         straightened card photos
  originals/     whole photos, stored once per photo
  references/    catalog images of the scanned cards (once per card id)
  crops/<id>.txt Play! stamp box (YOLO "0 cx cy w h") for marked Prize Pack cards;
                 empty for cards without the stamp (negatives). Prize Pack cards
                 without a marked stamp have no file.
  stamp.yaml, stamp_train.txt   Ultralytics dataset for a stamp detector on
                 straightened crops (yolo detect train data=stamp.yaml), one
                 class: play_stamp
  originals/<photo>.txt   card outlines for detector training (YOLO OBB / segmentation
                 polygon format: "0 x1 y1 x2 y2 x3 y3 x4 y4", normalised), only
                 for photos where every saved card has corners
  detect.yaml, detect_train.txt   Ultralytics dataset for those photos
                 (yolo obb train data=detect.yaml), one class: card

Photos with several cards come from "Multiple cards" and binder page scans.
A photo lists only the cards that were saved from it: a card discarded in the
review (or never saved) is in the photo but has no outline, so prefer photos
whose card count matches what you see, or check them before training.

Use: evaluation and fine-tuning of the card matcher (embed_experiment:
`python embed_eval.py import-scans <this zip>`), a learned card detector /
segmentation model (photos.jsonl or the YOLO files), a Prize Pack stamp
classifier (crop + prize_pack). Filter out check.status == "low" to train
only on clean crops.

Foil / reverse-holo patterns: the catalog image shows the card without its
foil, so aligning a crop to its reference and subtracting them leaves mostly
the foil: a reverse holo lights up around the art box, a Poké Ball or Master
Ball pattern shows its repeated motif, a holo only the art. Photos of the
same card with different foiling (pairs by card_id) work the same way.
matcher/tools/holo_diff.py does the alignment and writes the difference
images plus a CSV per card to start training a pattern classifier.
"""


def _quad_ok(q) -> bool:
    return isinstance(q, list) and len(q) == 4 and all(isinstance(p, list) and len(p) == 2 for p in q)


@router.get("/scan-dataset/summary", dependencies=[Depends(require_admin)])
def scan_dataset_summary():
    """Wie viele Kartenfotos in den Export kommen und welche nicht (und warum), je Konto."""
    from app.models.user import User

    def n(cond):
        return func.count(PokemonCard.id).filter(cond)

    with engine.connect() as conn:   # Core: bewusst ohne Benutzer-Filter (Admin-Übersicht)
        rows = conn.execute(
            select(User.username, User.display_name,
                   n(scan_export.exportable()).label("exportable"),
                   n(scan_export.not_exportable()).label("not_exportable"),
                   n(scan_export.no_card()).label("no_card"),
                   n(scan_export.no_original()).label("no_original"))
            .select_from(User)
            .join(PokemonCard, PokemonCard.user_id == User.id, isouter=True)
            .group_by(User.id, User.username, User.display_name)
            .order_by(User.username)
        ).mappings().all()
    users = [dict(r) for r in rows]
    total = {k: sum(u[k] for u in users) for k in ("exportable", "not_exportable", "no_card", "no_original")}
    return {**total, "users": [u for u in users if u["exportable"] or u["not_exportable"]]}


@router.get("/scan-dataset", dependencies=[Depends(require_admin)])
def scan_dataset():
    """
    Scan-Fotos als Trainingsdatensatz (ZIP, alle Benutzer – der Bild-Abgleich
    ist gemeinsam): entzerrte Karten, Originalfotos (je Inhalt einmal) und
    labels.jsonl mit der bestätigten TCGdex-ID, Prize Pack und den Ecken.
    """
    import hashlib

    from PIL import Image

    from app.models.scan_check import ScanCheck
    from app.models.tcgdex_catalog import TcgdexCatalog
    from app.services.catalog_images import safe_catalog_filename
    from app.services.tcgdex import is_allowed_image_url

    c = PokemonCard.__table__.c
    s = ScanCheck.__table__.c
    k = TcgdexCatalog.__table__.c
    with engine.connect() as conn:   # Core: bewusst ohne Benutzer-Filter (Admin-Export)
        rows = conn.execute(
            PokemonCard.__table__.outerjoin(ScanCheck.__table__, s.card_id == c.id)
            .outerjoin(TcgdexCatalog.__table__, k.card_id == c.tcgdex_card_id).select()
            .with_only_columns(c.id, c.tcgdex_card_id, c.kartenname, c.englischer_name, c.set_id,
                               c.set_edition, c.karten_nr, c.sprache, c.prize_pack,
                               c.folierung, c.muster, c.kartenversion, c.seltenheit, c.erste_edition,
                               c.stamp_quad,
                               func.coalesce(c.bild_scan_pfad, c.bild_karte_pfad).label("crop"),
                               c.bild_original_pfad, c.bild_quad,
                               s.status.label("check_status"), s.inliers.label("check_inliers"),
                               s.corner_err.label("check_corner_err"),
                               k.image_pfad.label("ref_pfad"), k.image_url.label("ref_url"))
            .where(scan_export.exportable())
            .order_by(c.id)
        ).mappings().all()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        labels: list[str] = []
        seen: dict[str, str] = {}
        photos: dict[str, dict] = {}   # Originalfoto → alle daraus geschnittenen Karten (Detektor)
        refs: dict[str, str] = {}      # TCGdex-ID → Katalogbild im ZIP (Referenz für die Folien-Differenz)
        stamp_train: list[str] = []    # Zuschnitte mit Stempel-Label (Play!-Stempel-Detektor)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
            for r in rows:
                crop, orig = safe_media_path(r["crop"]), safe_media_path(r["bild_original_pfad"])
                if not (crop and crop.is_file() and orig and orig.is_file()):
                    continue
                crop_name = f"crops/{r['id']}{crop.suffix.lower()}"
                zf.write(crop, arcname=crop_name)
                digest = hashlib.sha1(orig.read_bytes()).hexdigest()[:16]
                if digest not in seen:
                    seen[digest] = f"originals/{digest}{orig.suffix.lower()}"
                    zf.write(orig, arcname=seen[digest])
                    try:
                        with Image.open(orig) as im:
                            size = im.size
                    except OSError:
                        size = (None, None)
                    photos[digest] = {"original": seen[digest], "width": size[0], "height": size[1], "cards": []}
                try:
                    quad = json.loads(r["bild_quad"]) if r["bild_quad"] else None
                except ValueError:
                    quad = None
                # Katalogbild (meist der normale Druck) als Referenz — nur aus dem
                # lokalen Bild-Cache, der Export lädt nichts aus dem Netz
                ref = None
                cid = r["tcgdex_card_id"]
                if cid in refs:
                    ref = refs[cid]
                else:
                    ref_file = safe_media_path(r["ref_pfad"])
                    if ref_file is not None and ref_file.is_file():
                        ref = f"references/{safe_catalog_filename(cid)}{ref_file.suffix.lower()}"
                        zf.write(ref_file, arcname=ref)
                    refs[cid] = ref
                try:
                    stamp = json.loads(r["stamp_quad"]) if r["stamp_quad"] else None
                except ValueError:
                    stamp = None
                stamp = stamp if r["prize_pack"] and _quad_ok(stamp) else None
                # Stempel-Detektor: Box je markiertem Stempel, leere Datei = sicher ohne
                # Stempel (kein Prize Pack); Prize Pack ohne Markierung bleibt draußen
                if stamp or not r["prize_pack"]:
                    if stamp:
                        xs, ys = [min(1.0, max(0.0, p[0])) for p in stamp], [min(1.0, max(0.0, p[1])) for p in stamp]
                        box = f"0 {(min(xs) + max(xs)) / 2:.6f} {(min(ys) + max(ys)) / 2:.6f} " \
                              f"{max(xs) - min(xs):.6f} {max(ys) - min(ys):.6f}\n"
                    else:
                        box = ""
                    zf.writestr(crop_name.rsplit(".", 1)[0] + ".txt", box)
                    stamp_train.append("./" + crop_name)
                check = None if r["check_status"] is None else {
                    "status": r["check_status"], "inliers": r["check_inliers"],
                    "corner_err": r["check_corner_err"]}
                labels.append(json.dumps({
                    "id": r["id"], "card_id": r["tcgdex_card_id"],
                    "name": r["englischer_name"] or r["kartenname"], "set_id": r["set_id"],
                    "set": r["set_edition"], "number": r["karten_nr"], "language": r["sprache"],
                    "prize_pack": bool(r["prize_pack"]), "crop": crop_name, "original": seen[digest],
                    "quad": quad, "check": check,
                    "foiling": r["folierung"], "pattern": r["muster"], "version": r["kartenversion"],
                    "rarity": r["seltenheit"], "first_edition": bool(r["erste_edition"]),
                    "reference": ref, "stamp_quad": stamp,
                    "reference_url": r["ref_url"] if is_allowed_image_url(r["ref_url"]) else None,
                }, ensure_ascii=False))
                photos[digest]["cards"].append({"id": r["id"], "card_id": r["tcgdex_card_id"], "quad": quad})
            zf.writestr("labels.jsonl", "\n".join(labels) + ("\n" if labels else ""))
            # Detektor/Segmentierung: je Originalfoto alle Kartenumrisse
            train: list[str] = []
            lines: list[str] = []
            for digest, ph in photos.items():
                ph["labeled"] = all(_quad_ok(cd["quad"]) for cd in ph["cards"])
                lines.append(json.dumps(ph, ensure_ascii=False))
                if ph["labeled"]:
                    yolo = "\n".join("0 " + " ".join(f"{min(1.0, max(0.0, v)):.6f}" for p in cd["quad"] for v in p)
                                     for cd in ph["cards"])
                    zf.writestr(f"originals/{digest}.txt", yolo + "\n")
                    train.append("./" + ph["original"])
            zf.writestr("photos.jsonl", "\n".join(lines) + ("\n" if lines else ""))
            zf.writestr("detect_train.txt", "\n".join(train) + ("\n" if train else ""))
            zf.writestr("detect.yaml", "path: .\ntrain: detect_train.txt\nval: detect_train.txt\nnames:\n  0: card\n")
            zf.writestr("stamp_train.txt", "\n".join(stamp_train) + ("\n" if stamp_train else ""))
            zf.writestr("stamp.yaml", "path: .\ntrain: stamp_train.txt\nval: stamp_train.txt\nnames:\n  0: play_stamp\n")
            zf.writestr("README.txt", _DATASET_README)
        tmp.close()
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise
    logger.info("Scan-Datensatz: %d Fotos, %d Originale", len(labels), len(seen))
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=f"pokecollect-scans-{date.today().isoformat()}.zip",
        background=BackgroundTask(os.unlink, tmp.name),
    )


@router.post("/restore", dependencies=[Depends(require_admin)])
async def restore(file: UploadFile = File(...), confirm: str = Form("")):
    """
    Backup-ZIP einspielen. DESTRUKTIV: ersetzt ALLE Daten und Bilder.
    Schutz: Pflichtfeld confirm="JA_WIRKLICH" (das UI verlangt zusätzlich
    Checkbox + Bestätigungsdialog).
    """
    if confirm != RESTORE_CONFIRM:
        raise HTTPException(
            status_code=400,
            detail='Restore nicht bestätigt — confirm="JA_WIRKLICH" ist Pflicht (ersetzt ALLE Daten).',
        )

    # Größenlimit 500 MB (Starlette hat den Upload bereits gespoolt).
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    if size > RESTORE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Backup größer als 500 MB")

    # In Temp-Datei umkopieren — ZipFile braucht wahlfreien Zugriff.
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()

        try:
            zf = zipfile.ZipFile(tmp.name)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Datei ist kein gültiges ZIP")
        with zf:
            if "data.json" not in zf.namelist():
                raise HTTPException(status_code=400, detail="ZIP enthält keine data.json — kein PokéCollect-Backup")
            payload = _validate_payload(zf.read("data.json"))

            # Erst NACH kompletter Validierung destruktiv werden.
            counts = _restore_tables(payload)
            images = _restore_images(zf)
    finally:
        os.unlink(tmp.name)

    # Light-Migrations nachziehen (idempotent): ein Backup älterer Versionen
    # bringt Alt-Semantik zurück (z. B. Kombi-Folierungen vor #63, leere
    # EN-Namen) — ohne diesen Lauf gälte sie bis zum nächsten Container-
    # Neustart (Panel-Fund v1.8.0). Lokaler Import: main importiert die Router,
    # ein Top-Level-Import wäre zirkulär.
    from app.main import _run_light_migrations
    _run_light_migrations()

    logger.info("Restore eingespielt: %s Tabellen, %d Bilder (Backup-App-Version %s)",
                len(counts), images, payload.get("app_version"))
    return {
        "detail": "Backup eingespielt — alle Daten wurden ersetzt.",
        "backup_app_version": payload.get("app_version"),
        "restored": counts,
        "images": images,
    }
