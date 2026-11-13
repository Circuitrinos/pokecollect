import json
import logging
import math
from typing import Literal, Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, select, update, or_, and_, false
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.schemas._grenzen import PG_INTEGER_MAX
from app.api.deps import require_auth
from app.auth_user import AuthUser
from app.config import settings
from app.database import get_db
from app.domain.pokedex import GEN_RANGES
from app.services.card_creation import _trigger_image_fetch, create_owned_card
from app.services import picture_finder
from app.services.card_images import (
    ImageValidationError,
    backfill_images_task,
    remove_card_images,
    safe_media_path,
    replace_crop,
    store_card_image,
    store_scan_photo,
)
from app.services.stats import collect_stats
from app.models.card import PokemonCard, PreisHistorie
from app.models.collection import Collection, collection_cards
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services.catalog_lookup import catalog_row_for
from app.services import card_bulk, image_fallback, pokemon_com, regulation, scan_export, sorting
from app.services.tcgdex import is_allowed_image_url
from app.services.scan import local_match
from app.services.pricing import variant_usd
from app.services.provenance import bild_quelle, daten_quelle
from app.schemas.card import (
    CardCreate, CardListResponse, CardResponse, CardUpdate,
    CollectionRef, EnumsResponse, RematchApply, RematchChange, RematchFoil, RematchPrint, RematchProposal,
    StampUpdate, StatsResponse,
    SELTENHEIT_VALUES, KARTENVERSION_VALUES, FOLIERUNG_VALUES, MUSTER_VALUES,
    SPRACHE_VALUES, ZUSTAND_VALUES, PRIORITAET_VALUES,
)
from app.schemas.collection import CollectionResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/cards", tags=["cards"])

# Sortierschlüssel der Kartenliste: (Spalte, absteigend?) in Grundrichtung
_CARD_SORT_KEYS = {
    "pokedex_nr": [(PokemonCard.pokedex_nr, False)],
    # Set, dann aufgedruckte Kartennummer (Format NNN/… ist nullgepolstert)
    "set": [(PokemonCard.set_edition, False), (PokemonCard.karten_nr, False)],
    "wert": [(PokemonCard.wert_eur, False)],
    "hinzugefuegt_am": [(PokemonCard.hinzugefuegt_am, False)],
    "regulation": [(PokemonCard.regulation_mark, True)],   # neuestes Zeichen zuerst
    # Sammlung (alphabetisch erste), darin der Binder-Platz; Karten ohne Sammlung am Ende
    "collection": [(select(Collection.name).select_from(collection_cards.join(Collection, Collection.id == collection_cards.c.collection_id))
                    .where(collection_cards.c.card_id == PokemonCard.id)
                    .order_by(Collection.name, collection_cards.c.position).limit(1)
                    .correlate(PokemonCard).scalar_subquery(), False),
                   (select(collection_cards.c.position).select_from(collection_cards.join(Collection, Collection.id == collection_cards.c.collection_id))
                    .where(collection_cards.c.card_id == PokemonCard.id)
                    .order_by(Collection.name, collection_cards.c.position).limit(1)
                    .correlate(PokemonCard).scalar_subquery(), False)],
}
_CARD_SORT_THEN = {"regulation": ["set"], "collection": ["set"]}   # gleicher Rang → Set & Nummer
_SORT_NAMES = ("pokedex_nr", "wert", "hinzugefuegt_am", "set", "regulation", "collection")


def _card_or_404(card_id: int, db: Session) -> PokemonCard:
    card = db.get(PokemonCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Karte nicht gefunden")
    return card


def _photo_card_or_404(card_id: int, db: Session, user: AuthUser) -> PokemonCard:
    """
    Wie _card_or_404 — als Admin aber auch die Karte eines anderen Kontos:
    Zuschnitt korrigieren und Play!-Stempel markieren gehen über alle Konten
    (Admin-Werkzeuge „Schwache Scans" / „Play!-Stempel", Trainingsdaten).
    Nur diese Foto-Werkzeuge; sonst bleibt jede Karte bei ihrem Besitzer.
    Der Rest der Anfrage läuft dann im Kontext des Besitzers (Neuladen der
    Karte, Antwort), damit die Mandanten-Trennung ihn nicht wegfiltert.
    """
    card = db.get(PokemonCard, card_id)
    if card is None and user.is_admin:
        card = db.execute(select(PokemonCard).where(PokemonCard.id == card_id)
                          .execution_options(all_users=True)).scalar_one_or_none()
        if card is not None and card.user_id is not None:
            from app.tenancy import USER_KEY
            db.info[USER_KEY] = card.user_id
            log.info("Admin %s: Foto-Werkzeug an Karte %s von Benutzer %s", user.username, card_id, card.user_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Karte nicht gefunden")
    return card


@router.get("", response_model=CardListResponse)
def list_cards(
    besessen: Optional[bool] = None,
    wunschliste: Optional[bool] = None,
    im_pokedex: Optional[bool] = None,
    erste_edition: Optional[bool] = None,
    prize_pack: Optional[bool] = None,
    # Turnier-Legalität (services/regulation.py): Regulation Mark(s), kommagetrennt
    # ("H,I"), bzw. Standard-Status gegen das eingestellte niedrigste Zeichen.
    regulation_mark: Optional[str] = Query(None, max_length=60),
    standard: Optional[str] = Query(None, pattern="^(legal|not_legal|unknown)$"),
    pokedex_view: bool = False,
    prioritaet: Optional[str] = None,
    set: Optional[str] = None,
    seltenheit: Optional[str] = None,
    sprache: Optional[str] = None,
    illustrator: Optional[str] = None,
    generation: Optional[int] = None,
    search: Optional[str] = None,
    pokedex_nr: Optional[int] = None,
    bild_status: Optional[str] = Query(None, pattern="^(eigenes_foto|externe_url|platzhalter|kein_export)$"),
    # in einem Deck / in keinem; in irgendeiner Sammlung / in keiner / in dieser (ID)
    deck: Optional[str] = Query(None, pattern="^(in|none)$"),
    collection: Optional[str] = Query(None, pattern=r"^(any|none|\d{1,9})$"),
    sort: str = Query("pokedex_nr", pattern="^(" + "|".join(_SORT_NAMES) + ")$"),
    reverse: bool = False,   # Reihenfolge umkehren; fehlende Werte bleiben am Ende
    # Untersortierungen: zuvor gewählte Sortierungen, z.B. "set,-wert"
    then: str = Query("", max_length=60, pattern=sorting.then_pattern(_SORT_NAMES)),
    # #117: ohne Obergrenze sprengte (page-1)*limit den OFFSET (bigint) → 500
    page: int = Query(1, ge=1, le=PG_INTEGER_MAX),
    limit: int = Query(50, ge=1, le=5000),  # Binder-Ansicht lädt den ganzen Pokédex auf einmal
    db: Session = Depends(get_db),
):
    q = select(PokemonCard)

    # Pokédex-Ansicht: je pokedex_nr genau eine Karte (im_pokedex=True bevorzugt,
    # dann erste besessene Karte als Fallback, dann Platzhalter für nicht gesammelte)
    if pokedex_view:
        flagged = select(PokemonCard.pokedex_nr).where(
            PokemonCard.im_pokedex == True,
            PokemonCard.pokedex_nr.isnot(None),
        )
        owned_nrs = select(PokemonCard.pokedex_nr).where(
            PokemonCard.besessen == True,
            PokemonCard.pokedex_nr.isnot(None),
        )
        fallback = (
            select(func.min(PokemonCard.id))
            .where(
                PokemonCard.pokedex_nr.isnot(None),
                PokemonCard.besessen == True,
                ~PokemonCard.pokedex_nr.in_(flagged),
            )
            .group_by(PokemonCard.pokedex_nr)
        )
        q = q.where(
            PokemonCard.pokedex_nr.isnot(None),
            or_(
                PokemonCard.im_pokedex == True,
                PokemonCard.id.in_(fallback),
                # Platzhalter nur, wenn die Spezies weder geflaggt noch besessen ist
                and_(
                    PokemonCard.besessen == False,
                    ~PokemonCard.pokedex_nr.in_(flagged),
                    ~PokemonCard.pokedex_nr.in_(owned_nrs),
                ),
            ),
        )

    if besessen is not None:
        q = q.where(PokemonCard.besessen == besessen)
    if wunschliste is not None:
        q = q.where(PokemonCard.wunschliste == wunschliste)
    if im_pokedex is not None:
        q = q.where(PokemonCard.im_pokedex == im_pokedex)
    if erste_edition is not None:
        q = q.where(PokemonCard.erste_edition == erste_edition)
    if prize_pack is not None:
        q = q.where(PokemonCard.prize_pack == prize_pack)
    if regulation_mark:
        marks = {m for m in (regulation.norm_mark(x) for x in regulation_mark.split(",")) if m}
        want_none = any(x.strip() == regulation.NO_MARK for x in regulation_mark.split(","))
        cond = PokemonCard.regulation_mark.in_(marks) if marks else None
        if want_none:
            # „ohne Zeichen" = geprüft, aber keins aufgedruckt (unbekannt zählt nicht)
            none_cond = PokemonCard.regulation_mark.is_(None) & PokemonCard.legal_mark.isnot(None)
            cond = none_cond if cond is None else (cond | none_cond)
        q = q.where(cond if cond is not None else false())
    if standard:
        legal = regulation.standard_legal_clause(regulation.standard_min_mark(db))
        if standard == "legal":
            q = q.where(legal)
        elif standard == "not_legal":
            q = q.where(PokemonCard.legal_mark.isnot(None), ~legal)
        else:
            q = q.where(PokemonCard.legal_mark.is_(None), PokemonCard.basic_energy == False)  # noqa: E712
    if prioritaet:
        q = q.where(PokemonCard.prioritaet == prioritaet)
    if set:
        q = q.where(PokemonCard.set_edition.ilike(f"%{set}%"))
    if seltenheit:
        q = q.where(PokemonCard.seltenheit == seltenheit)
    if sprache:
        q = q.where(PokemonCard.sprache == sprache)
    if illustrator:
        q = q.where(PokemonCard.illustrator.ilike(f"%{illustrator}%"))
    if pokedex_nr is not None:
        q = q.where(PokemonCard.pokedex_nr == pokedex_nr)
    search_warnings: list = []
    search_sort = None
    if search:
        # Suchsyntax wie bei Limitless TCG (services/card_search.py); „PFL 001" bleibt
        # Set-Kürzel + Kartennummer, reiner Text sucht im Namen und in der Pokédex-Nr.
        from app.services import card_search

        conds, parsed = card_search.conditions(db, search)
        q = q.where(*conds) if conds else q
        search_warnings, search_sort = parsed.warnings, parsed.sort
        if search_sort:
            sort, reverse, then = search_sort, False, ""
    if deck == "in":
        q = q.where(PokemonCard.deck_id.isnot(None))
    elif deck == "none":
        q = q.where(PokemonCard.deck_id.is_(None))
    if collection == "any":
        q = q.where(PokemonCard.id.in_(select(collection_cards.c.card_id)))
    elif collection == "none":
        q = q.where(~PokemonCard.id.in_(select(collection_cards.c.card_id)))
    elif collection:
        q = q.where(PokemonCard.id.in_(select(collection_cards.c.card_id)
                                       .where(collection_cards.c.collection_id == int(collection))))
    if bild_status == "eigenes_foto":
        q = q.where(PokemonCard.bild_karte_pfad.isnot(None))
    elif bild_status == "externe_url":
        q = q.where(PokemonCard.bild_karte_pfad.is_(None))
        q = q.where(PokemonCard.bild_pokedex_url.isnot(None))
    elif bild_status == "platzhalter":
        q = q.where(PokemonCard.bild_karte_pfad.is_(None))
        q = q.where(PokemonCard.bild_pokedex_url.is_(None))
    elif bild_status == "kein_export":     # Foto, das nicht in den Scan-Datensatz kommt
        q = q.where(scan_export.not_exportable())
    if generation:
        r = GEN_RANGES.get(generation)
        if r:
            q = q.where(PokemonCard.pokedex_nr.between(r[0], r[1]))

    q = q.order_by(*sorting.order_clauses(_CARD_SORT_KEYS, sort, reverse, then, _CARD_SORT_THEN, last=PokemonCard.id))

    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.offset((page - 1) * limit).limit(limit)).all()

    return CardListResponse(
        items=_with_collections(db, items),
        total=total,
        page=page,
        limit=limit,
        pages=math.ceil(total / limit) if total else 1,
        search_warnings=search_warnings,
        search_sort=search_sort,
    )


def _with_collections(db: Session, cards) -> list[CardResponse]:
    """Kartenliste mit den Sammlungen jeder Karte (eine Abfrage für die ganze Seite)."""
    out = [CardResponse.model_validate(c) for c in cards]
    ids = [c.id for c in cards]
    by_card: dict[int, list] = {i: [] for i in ids}
    if ids:
        rows = db.execute(select(collection_cards.c.card_id, Collection.id, Collection.name)
                          .join(Collection, Collection.id == collection_cards.c.collection_id)
                          .where(collection_cards.c.card_id.in_(ids))
                          .order_by(Collection.name)).all()
        for card_id, cid, name in rows:
            by_card[card_id].append(CollectionRef(id=cid, name=name))
    for r in out:
        r.sammlungen = by_card.get(r.id, [])
    return out


def catalog_variants(row: TcgdexCatalog) -> dict:
    """Welche Drucke es von der Karte gibt: TCGdex-Varianten + TCGplayer-Muster-Produkte."""
    from app.services.tcgcsv import PATTERN_TO_MUSTER

    keys = [k for k in (row.patterns or "").split(",") if k]
    if row.price_usd_pokeball is not None and "pokeball" not in keys:
        keys.append("pokeball")
    if row.price_usd_masterball is not None and "masterball" not in keys:
        keys.append("masterball")
    order = list(PATTERN_TO_MUSTER)
    keys.sort(key=lambda k: order.index(k) if k in order else len(order))
    return {"normal": row.variants_normal, "reverse": row.variants_reverse, "holo": row.variants_holo,
            "first_edition": row.variants_firstedition,
            "patterns": [PATTERN_TO_MUSTER.get(k, k) for k in keys]}


def _card_response(db: Session, card: PokemonCard) -> CardResponse:
    """
    CardResponse + TCGplayer-$-Beilage aus dem Katalog-Cache (Epic #41; case-
    toleranter Lookup, v1.7.3). Gemeinsame Routine ALLER Einzelkarten-Antworten
    (GET/POST/PUT/Bild), damit die $-Zeile im UI nach Speichern/Toggle nicht
    verschwindet — das Frontend ersetzt seinen Kartenzustand aus jeder dieser
    Antworten.
    """
    resp = CardResponse.model_validate(card)
    row = catalog_row_for(db, card.tcgdex_card_id)
    resp.katalog_bild = bool(
        (card.bild_karte_url and (is_allowed_image_url(card.bild_karte_url)
                                  or picture_finder.katalog_id(card.bild_karte_url)))
        or (row is not None and (is_allowed_image_url(row.image_url) or row.image_pfad)))
    if row is not None:
        # NUR den $-Preis der tatsächlichen Variante zeigen (v1.8.2) — sonst
        # stünde bei einer Pokéball-Karte der Basispreis, aus dem sich ihr
        # €-Wert nicht nachrechnen lässt. KEIN Ersatz-Fallback auf den
        # Basispreis (Panel-Fund): eine Holo-Karte ohne Holo-Preis bekäme sonst
        # eine Variante angezeigt, die die Bewertung nie verwendet. Lieber
        # keinen $-Preis als den einer fremden Variante.
        val, variante = variant_usd(row, card.folierung, card.muster)
        if val is not None:
            resp.katalog_preis_usd = val
            resp.katalog_preis_usd_variante = variante
            resp.katalog_preis_usd_stand = row.price_usd_updated
        if row.regulation_checked:
            resp.legal_expanded = row.legal_expanded
        resp.katalog_varianten = catalog_variants(row)
    # Herkunft des zuletzt gesetzten Werts (jüngster Verlaufseintrag) — das UI
    # beschriftet den Wert damit statt pauschal „Cardmarket" zu behaupten.
    resp.wert_quelle = db.scalar(
        select(PreisHistorie.quelle)
        .where(PreisHistorie.karte_id == card.id)
        # nulls_last: erfasst_am ist nullable — in Postgres stünde NULL bei
        # DESC sonst VORNE und gewönne die Abfrage (Panel-Fund).
        .order_by(PreisHistorie.erfasst_am.desc().nulls_last(),
                  PreisHistorie.id.desc())
        .limit(1)
    )
    # Herkunfts-Zeile "Daten: … · Bild: … · Preis: …" in der Detailansicht
    # (#47) — Preis kommt aus wert_quelle oben, Daten/Bild rein abgeleitet.
    resp.bild_quelle = bild_quelle(card)
    resp.daten_quelle = daten_quelle(card)
    return resp


@router.get("/{card_id}", response_model=CardResponse)
def get_card(card_id: int, db: Session = Depends(get_db)):
    return _card_response(db, _card_or_404(card_id, db))


@router.get("/{card_id}/collections", response_model=list[CollectionResponse])
def get_card_collections(card_id: int, db: Session = Depends(get_db)):
    _card_or_404(card_id, db)
    colls = db.scalars(
        select(Collection)
        .join(collection_cards, collection_cards.c.collection_id == Collection.id)
        .where(collection_cards.c.card_id == card_id)
        .order_by(Collection.name)
    ).all()
    return [
        CollectionResponse(
            id=c.id, name=c.name, beschreibung=c.beschreibung,
            binder_layout=c.binder_layout or "3x3", erstellt_am=c.erstellt_am,
        )
        for c in colls
    ]


@router.post("", response_model=CardResponse, status_code=201)
def create_card(data: CardCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    # Besessene Karten laufen über den Domain-Service (Adoption + Auto-Flag
    # + Bild-Fetch — eine Routine für alle Anlege-Pfade, Issue #4).
    if data.besessen:
        card = create_owned_card(db, data.model_dump(), background_tasks=background_tasks)
        return _card_response(db, card)

    card = PokemonCard(**data.model_dump())
    db.add(card)
    db.flush()
    regulation.apply_to_card(db, card)
    db.commit()
    db.refresh(card)
    return _card_response(db, card)


@router.put("/{card_id}", response_model=CardResponse)
def update_card(card_id: int, data: CardUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    card = _card_or_404(card_id, db)
    updated = data.model_dump(exclude_unset=True)
    mark_changed = "regulation_mark" in updated and updated["regulation_mark"] != card.regulation_mark
    # Andere Karte (Set/Nummer wirklich geändert) → Zeichen neu aus TCGdex. Die
    # Detailseite schickt das ganze Formular, also Werte vergleichen.
    other_card = any(k in updated and updated[k] != getattr(card, k) for k in ("set_edition", "karten_nr"))
    # Pokédex-Exklusivität: wenn im_pokedex=True gesetzt wird, alle anderen Karten
    # mit gleicher pokedex_nr auf False zurücksetzen (1 Pokédex-Slot pro Pokémon)
    if updated.get("im_pokedex") is True and card.pokedex_nr:
        db.execute(
            update(PokemonCard)
            .where(PokemonCard.pokedex_nr == card.pokedex_nr)
            .where(PokemonCard.id != card.id)
            .values(im_pokedex=False)
        )
    for field, value in updated.items():
        setattr(card, field, value)
    # Invariante (#63): Muster nur an folierten Grundformen (vgl. create_owned_card)
    if card.muster and "Holo" not in (card.folierung or ""):
        card.muster = None
    # Bild + TCGdex-Referenz neu abrufen wenn relevante Felder geändert wurden
    if any(k in updated for k in ("set_edition", "karten_nr", "sprache")):
        card.bild_karte_url = None  # wird neu gesetzt
        card.set_id = None          # erzwingt Neuauflösung aus set_edition
        if other_card and not mark_changed:
            card.regulation_mark = None   # andere Karte → Zeichen kommt neu aus TCGdex
        background_tasks.add_task(_trigger_image_fetch, card.id)
    regulation.apply_to_card(db, card)
    db.commit()
    db.refresh(card)
    return _card_response(db, card)


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: int, db: Session = Depends(get_db)):
    card = _card_or_404(card_id, db)
    if not card.besessen:
        raise HTTPException(
            status_code=400,
            detail="Nicht-besessene Karten (Pokédex-Platzhalter) können nicht gelöscht werden.",
        )
    _delete_owned(db, card)
    db.commit()


def _delete_owned(db: Session, card: PokemonCard) -> None:
    """Besessene Karte löschen; letzte ihrer Spezies → Pokédex-Platzhalter zurück (ohne Commit)."""
    # Pokédex-Daten vor dem Löschen sichern
    pokedex_nr     = card.pokedex_nr
    kartenname     = card.kartenname
    englischer_name = card.englischer_name
    bild_pokedex_url = card.bild_pokedex_url

    db.delete(card)
    db.flush()  # Löschung sichtbar machen, Transaktion noch offen

    # Wenn keine weitere Karte für diese Pokédex-Nr. existiert → Platzhalter neu anlegen
    if pokedex_nr:
        remaining = db.scalar(
            select(func.count(PokemonCard.id))
            .where(PokemonCard.pokedex_nr == pokedex_nr)
        )
        if remaining == 0:
            db.add(PokemonCard(
                pokedex_nr=pokedex_nr,
                kartenname=kartenname,
                englischer_name=englischer_name,
                bild_pokedex_url=bild_pokedex_url,
                besessen=False,
            ))


# ── Sammel-Aktionen (Auswahl-Modus) ─────────────────────────────────────────

class BulkRequest(BaseModel):
    action: Literal["catalog_image", "own_photo", "prize_pack", "zustand",
                    "add_to_collection", "remove_from_collection", "delete"]
    ids: list[int] = Field(default_factory=list, max_length=10000)
    value: Optional[Union[bool, int, str]] = None   # prize_pack: bool, zustand: str, *_collection: id
    # nur catalog_image: alle eigenen Karten mit Scan-Foto als Kartenbild
    all_scanned: bool = False


@router.post("/meta/bulk")
def bulk_cards(req: BulkRequest, db: Session = Depends(get_db)):
    """Eine Aktion auf viele Karten; Antwort zählt geändert / unverändert / nicht gefunden."""
    if req.all_scanned and req.action != "catalog_image":
        raise HTTPException(status_code=400, detail="all_scanned gibt es nur für catalog_image")
    if req.action in ("add_to_collection", "remove_from_collection"):
        if not isinstance(req.value, int) or isinstance(req.value, bool) or db.get(Collection, req.value) is None:
            raise HTTPException(status_code=404, detail="Sammlung nicht gefunden")
    if req.action == "zustand" and req.value not in ZUSTAND_VALUES:
        raise HTTPException(status_code=422, detail="Unbekannter Zustand")
    ids = card_bulk.owned_scanned_card_ids(db) if req.all_scanned else list(dict.fromkeys(req.ids))
    done = unchanged = missing = 0
    for cid in ids:
        card = db.get(PokemonCard, cid)
        # Pokédex-Platzhalter (nicht besessen) kann man nicht löschen und haben kein Foto
        if card is None or (not card.besessen and req.action in ("delete", "catalog_image", "own_photo")):
            missing += 1
            continue
        if req.action == "delete":
            _delete_owned(db, card)
            changed = True
        elif req.action == "catalog_image":
            changed = card_bulk.use_catalog_image(db, card)
        elif req.action == "own_photo":
            changed = card_bulk.use_own_photo(db, card)
        elif req.action == "prize_pack":
            changed = card_bulk.set_prize_pack(db, card, req.value)
        elif req.action == "zustand":
            changed = card_bulk.set_condition(db, card, req.value)
        elif req.action == "add_to_collection":
            changed = card_bulk.add_to_collection(db, card, req.value)
        else:
            changed = card_bulk.remove_from_collection(db, card, req.value)
        done += changed
        unchanged += not changed
    db.commit()
    return {"done": done, "unchanged": unchanged, "missing": missing}


# ── Bilder ──────────────────────────────────────────────────────────────────
# Verarbeitung (Validierung, EXIF-Aufrichtung, Ablage) lebt in
# services/card_images.py — der Router mappt nur Fehler auf HTTP (Issue #14).

def _parse_quad(raw: Optional[str]) -> Optional[list[list[float]]]:
    """Ecken des Zuschnitts (JSON, 4×[x,y] normiert); Unplausibles → None."""
    if not raw:
        return None
    try:
        q = json.loads(raw)
        pts = [[round(float(x), 5), round(float(y), 5)] for x, y in q]
    except (ValueError, TypeError):
        return None
    if len(pts) != 4 or not all(-0.5 <= v <= 1.5 for p in pts for v in p):
        return None
    return pts


@router.post("/{card_id}/image", response_model=CardResponse)
async def upload_image(
    card_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    original: Optional[UploadFile] = File(None),
    quad: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    card = _card_or_404(card_id, db)
    try:
        card = store_card_image(db, card, file, original, quad=_parse_quad(quad))
    except ImageValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    # Scan-Foto mit Original: der Bild-Abgleich nimmt es als zusätzliche
    # Referenz für diese Karte auf (matcher/app/learn.py).
    if card.bild_original_pfad and card.tcgdex_card_id:
        background_tasks.add_task(local_match.poke_learn, settings.matcher_url)
    return _card_response(db, card)


@router.post("/{card_id}/catalog-image", response_model=CardResponse)
async def show_catalog_image(card_id: int, db: Session = Depends(get_db)):
    """
    Umschalter auf der Kartenseite: Katalogbild statt eigenem Foto zeigen. Ist
    noch keins bekannt, wird es gesucht (TCGdex, offizielle Datenbank). Das
    Foto bleibt als Scan-Foto erhalten (wie die Sammel-Aktion).
    """
    card = _card_or_404(card_id, db)
    if card.bild_karte_pfad:
        url = await image_fallback.find_catalog_image(db, card)
        if not url:
            db.commit()                     # gefundene Verknüpfung behalten
            raise HTTPException(status_code=404, detail="Kein Katalogbild für diese Karte gefunden")
        card.bild_karte_url = url
        card_bulk.use_catalog_image(db, card)
        regulation.apply_to_card(db, card)
        db.commit()
        db.refresh(card)
    return _card_response(db, card)


class ImageLanguageRequest(BaseModel):
    lang: str = Field(..., pattern=r"^[A-Za-z]{2}$")


@router.post("/{card_id}/image-language", response_model=CardResponse)
async def set_image_language(card_id: int, req: ImageLanguageRequest, db: Session = Depends(get_db)):
    """
    Katalogbild in einer anderen Sprache zeigen (z. B. das deutsche Bild einer
    deutschen Karte, deren Katalogbild englisch ist). Nur für westliche
    TCGdex-Bilder; das Bild wird vorher geholt — gibt es die Sprachfassung
    nicht, bleibt alles, wie es war (404). Ein eigenes Foto bleibt angezeigt;
    das neue Katalogbild gilt für den Umschalter.
    """
    from app.services import bild_spiegel, image_lang

    card = _card_or_404(card_id, db)
    base = card.bild_karte_url
    if not image_lang.image_lang(base) and card.tcgdex_card_id:
        row = catalog_row_for(db, card.tcgdex_card_id)
        base = row.image_url if row is not None else None
    url = image_lang.with_lang(base, req.lang)
    if not url:
        raise HTTPException(status_code=422, detail="Für dieses Bild gibt es keine anderen Sprachen")
    if await bild_spiegel.datei_fuer(url) is None:
        raise HTTPException(status_code=404, detail="Kein Kartenbild in dieser Sprache")
    card.bild_karte_url = url
    db.commit()
    db.refresh(card)
    return _card_response(db, card)


class LinkCatalogRequest(BaseModel):
    card_id: str = Field(..., min_length=1, max_length=64)       # TCGdex-ID der gewählten Katalogkarte
    image_url: Optional[str] = Field(None, max_length=500)       # das im Auswähler gezeigte Bild


@router.post("/{card_id}/link-catalog", response_model=CardResponse)
async def link_catalog(card_id: int, req: LinkCatalogRequest, db: Session = Depends(get_db)):
    """
    „Richtige Karte finden": die Karte mit einer Katalogkarte verknüpfen
    (Set, Nummer, Seltenheit, Namen, TCGdex-ID) und deren Bild anzeigen. Ein
    eigenes Foto bleibt als Scan-Foto erhalten (Umschalter „Mein Foto").
    """
    card = _card_or_404(card_id, db)
    row = catalog_row_for(db, req.card_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Karte nicht im Katalog")
    await _link_to_row(db, card, row, req.image_url)
    db.commit()
    db.refresh(card)
    return _card_response(db, card)


async def _link_to_row(db: Session, card: PokemonCard, row, image_url: Optional[str]) -> None:
    """Karte auf eine Katalogkarte umstellen (Set, Nummer mit Nenner, Namen, Seltenheit,
    Illustrator, TCGdex-ID) und deren Bild zeigen; ein eigenes Foto wird zum Scan-Foto."""
    from app.services import rematch

    await rematch.apply(db, card, row, image_url=image_url, show_image=True)


def _show_image(db: Session, card: PokemonCard, url: str) -> None:
    card.bild_karte_url = url
    if card.bild_karte_pfad:
        card_bulk.use_catalog_image(db, card)


class PokemonComLinkRequest(BaseModel):
    url: str = Field(..., min_length=10, max_length=2000)    # Kartenseite/Bildadresse oder geteilter Text


@router.post("/{card_id}/link-pokemon-com", response_model=CardResponse)
async def link_pokemon_com(card_id: int, req: PokemonComLinkRequest, db: Session = Depends(get_db)):
    """
    Link von pokemon.com, den der Nutzer in seinem Browser gefunden hat
    (Kartenseite oder Bildadresse): Bild übernehmen und — wenn die Karte im
    Katalog steht — die Karte damit verknüpfen. Kein Abruf der Seite selbst:
    Set und Nummer stehen im Link, das Bild liegt unter fester Adresse.
    """
    card = _card_or_404(card_id, db)
    link = pokemon_com.parse_link(req.url)
    if link is None:
        raise HTTPException(status_code=422, detail="Kein Kartenlink von pokemon.com")
    url = await pokemon_com.image_for_link(link)
    if url is None:
        raise HTTPException(status_code=404, detail="Zu diesem Link gibt es kein Kartenbild")
    sets = pokemon_com.tcgdex_set_candidates(link["code"])
    num = link["number"]
    variants = {v.upper() for v in (num, num.lstrip("0"), num.zfill(3))}
    row = db.scalars(
        select(TcgdexCatalog)
        .where(TcgdexCatalog.set_id.in_(sets), TcgdexCatalog.region == "west")
        .where(or_(func.upper(TcgdexCatalog.local_id).in_(variants),
                   func.ltrim(TcgdexCatalog.local_id, "0") == (num.lstrip("0") or "0")))
        .order_by(TcgdexCatalog.card_id)
    ).first()
    if row is not None:
        await _link_to_row(db, card, row, url)
    else:
        _show_image(db, card, url)                   # nur das Bild — die Karte kennt der Katalog nicht
    db.commit()
    db.refresh(card)
    return _card_response(db, card)


def _rematch_print(row, base_url: str, current: Optional[str], photo_ids: set[str]) -> RematchPrint:
    from app.services import catalog_images

    return RematchPrint(
        tcgdex_card_id=row.card_id, name=row.name_en or row.name or row.card_id, set_code=row.set_code,
        set_name=row.set_name, local_id=row.local_id, rarity=row.rarity,
        image_url=catalog_images.resolve_catalog_image_url(row.image_pfad, row.image_url, base_url),
        current=bool(current) and row.card_id.upper() == current.upper(), photo=row.card_id in photo_ids)


def _shown(v) -> Optional[str]:
    return None if v is None else str(v)


@router.get("/{card_id}/rematch", response_model=RematchProposal)
async def rematch_proposal(
    card_id: int,
    request: Request,
    print_id: Optional[str] = Query(None, max_length=64, alias="print"),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_auth),
):
    """
    Neu abgleichen, Vorschau: welcher Druck es ist (eigenes Foto durch den Bild-Abgleich,
    sonst die Verknüpfung, sonst Set + Nummer) und was das an der Karte ergänzt oder
    richtigstellt. print: einen anderen Druck ansehen (aus „Andere Drucke").
    Admins auch an Karten anderer Konten (Werkzeug „Karten mit Lücken").
    """
    from app.services import decks as deck_svc, rematch
    from app.services.catalog import catalog_fields, enrich

    card = _photo_card_or_404(card_id, db, user)
    found = await rematch.find(db, card)
    row, how, sure = found.row, found.how, found.sure
    if print_id:
        picked = catalog_row_for(db, print_id)
        if picked is None:
            raise HTTPException(status_code=404, detail="Karte nicht im Katalog")
        row, how, sure = picked, "picked", True
    base_url = str(request.base_url)
    photo_ids = {r.card_id for r in found.candidates}
    out = RematchProposal(how=how, sure=sure, photo=found.photo)
    if row is not None:
        await enrich(db, row)
        out.found = _rematch_print(row, base_url, card.tcgdex_card_id, photo_ids)
        out.changes = [RematchChange(field=c.field, old=_shown(c.old), new=_shown(c.new), kind=c.kind)
                       for c in rematch.changes(card, catalog_fields(db, row, card.sprache), row)]
        current = catalog_row_for(db, card.tcgdex_card_id)
        same_name = current is None or deck_svc.rule_name(current.name_en or current.name or "") \
            == deck_svc.rule_name(row.name_en or row.name or "")
        out.safe = sure and how != "picked" and same_name
        options = rematch.foil_options(row)
        hint = await rematch.photo_foil_hint(row.card_id, found.crop)
        suggested, why = rematch.foil_suggestion(card.folierung, options, hint)
        out.foil = RematchFoil(current=card.folierung, options=options, suggested=suggested, why=why, photo_hint=hint)
    name = (row.name_en or row.name) if row is not None else (card.englischer_name or card.kartenname or "")
    seen: set[str] = set()
    alts = []
    for r in [*found.candidates, *deck_svc.prints_for(db, name)]:
        if r.card_id in seen or (row is not None and r.card_id == row.card_id):
            continue
        seen.add(r.card_id)
        alts.append(_rematch_print(r, base_url, card.tcgdex_card_id, photo_ids))
    out.alternatives = alts[:60]
    return out


@router.post("/{card_id}/rematch", response_model=CardResponse)
async def rematch_apply(card_id: int, req: RematchApply, db: Session = Depends(get_db),
                        user: AuthUser = Depends(require_auth)):
    """Neu abgleichen, übernehmen: den Druck setzen und die Felder aus dem Katalog füllen
    (fields: nur diese; folierung: fehlende Foliierung mitsetzen)."""
    from app.services import rematch

    card = _photo_card_or_404(card_id, db, user)
    row = catalog_row_for(db, req.tcgdex_card_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Karte nicht im Katalog")
    if req.folierung is not None and req.folierung not in rematch.FOILS:
        raise HTTPException(status_code=422, detail="Unbekannte Folierung")
    done = await rematch.apply(db, card, row, only=set(req.fields) if req.fields is not None else None,
                               folierung=req.folierung)
    log.info("Re-match Karte %s → %s: %s%s", card.id, row.card_id, [c.field for c in done],
             f", Folierung {req.folierung}" if req.folierung else "")
    db.commit()
    db.refresh(card)
    return _card_response(db, card)


@router.post("/{card_id}/recrop", response_model=CardResponse)
async def recrop(
    card_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    quad: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_auth),
):
    """
    Zuschnitt korrigieren: neues Kartenfoto aus dem gespeicherten Original mit
    neuen Ecken (Kartenseite, Werkzeug „Schwache Scans"). Ersetzt das gerade
    gespeicherte Foto — angezeigt oder nur zum Lernen — und die Ecken.
    Admins auch an Karten anderer Konten.
    """
    card = _photo_card_or_404(card_id, db, user)
    if not card.bild_original_pfad or not (card.bild_karte_pfad or card.bild_scan_pfad):
        raise HTTPException(status_code=409, detail="Kein Originalfoto zum Neu-Zuschneiden")
    try:
        card = replace_crop(db, card, file, _parse_quad(quad))
    except ImageValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    if card.tcgdex_card_id:
        background_tasks.add_task(local_match.poke_learn, settings.matcher_url)
    return _card_response(db, card)


MAX_SUGGEST_BYTES = 15 * 1024 * 1024


@router.post("/{card_id}/crop-suggest")
async def crop_suggest(card_id: int, file: UploadFile = File(...), quad: str = Form(...),
                       db: Session = Depends(get_db), user: AuthUser = Depends(require_auth)):
    """
    „Zuschnitt korrigieren" mit groben Ecken: der Browser schneidet entlang der
    Ecken aus dem Original, der Bild-Abgleich prüft das (ohne zu speichern) und
    schlägt eingeschwungene Ecken vor. → status, inliers, corner_err, suggested_quad.
    """
    import httpx

    from app.services import scan_quality

    card = _photo_card_or_404(card_id, db, user)
    if not card.bild_original_pfad or not (card.bild_karte_pfad or card.bild_scan_pfad):
        raise HTTPException(status_code=409, detail="Kein Originalfoto zum Neu-Zuschneiden")
    if not card.tcgdex_card_id:
        raise HTTPException(status_code=409, detail="Karte ohne Katalog-Zuordnung")
    q = _parse_quad(quad)
    if q is None:
        raise HTTPException(status_code=422, detail="Ungültige Ecken")
    data = await file.read(MAX_SUGGEST_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Leere Datei")
    if len(data) > MAX_SUGGEST_BYTES:
        raise HTTPException(status_code=413, detail="Bild zu groß (max. 15 MB)")
    try:
        res = await scan_quality.try_crop(card.id, data, q)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")
    if res is None:
        raise HTTPException(status_code=409, detail="Kein Originalfoto zum Neu-Zuschneiden")
    return res


@router.put("/{card_id}/stamp", response_model=CardResponse)
def set_stamp(card_id: int, payload: StampUpdate, db: Session = Depends(get_db),
              user: AuthUser = Depends(require_auth)):
    """Play!-Stempel im Kartenfoto markieren (Trainingsdaten für einen Stempel-Detektor; Admins auch fremde)."""
    card = _photo_card_or_404(card_id, db, user)
    if payload.quad is not None and not (card.bild_karte_pfad or card.bild_scan_pfad):
        raise HTTPException(status_code=409, detail="Kein eigenes Foto zum Markieren")
    card.stamp_quad = json.dumps(payload.quad) if payload.quad else None
    db.commit()
    db.refresh(card)
    return _card_response(db, card)


@router.post("/{card_id}/stamp-suggest")
async def stamp_suggest(card_id: int, payload: StampUpdate, db: Session = Depends(get_db),
                        user: AuthUser = Depends(require_auth)):
    """Play!-Stempel im eigenen Foto vorschlagen (gelernte Vorlage; um `quad` herum, wenn gegeben).
    → {"quad" oder null, "score", "ok", "template"}."""
    import httpx

    from app.services import stamp_finder

    card = _photo_card_or_404(card_id, db, user)
    photo = safe_media_path(card.bild_scan_pfad or card.bild_karte_pfad)
    if photo is None or not photo.is_file():
        raise HTTPException(status_code=409, detail="Kein eigenes Foto zum Markieren")
    db.commit()     # Lesetransaktion nicht über den Abgleich offen halten
    try:
        return await stamp_finder.find(photo, payload.quad)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Bild-Abgleich nicht erreichbar")


@router.get("/{card_id}/scan-check")
def scan_check(card_id: int, db: Session = Depends(get_db)):
    """Letzte Prüfung des Scan-Fotos dieser Karte (Werkzeug „Schwache Scans"), sonst null."""
    from app.models.scan_check import ScanCheck

    card = _card_or_404(card_id, db)
    row = db.get(ScanCheck, card.id)
    if row is None:
        return None
    return {
        "status": row.status, "inliers": row.inliers, "art_inliers": row.art_inliers,
        "corner_err": row.corner_err, "templates": row.templates,
        "suggested_quad": json.loads(row.suggested_quad) if row.suggested_quad else None,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
    }


class SharePhotoRequest(BaseModel):
    card_ids: list[int] = Field(..., min_length=1, max_length=60)   # die anderen Exemplare


@router.post("/{card_id}/share-photo")
def share_photo(card_id: int, req: SharePhotoRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Die übrigen Exemplare einer Aufnahme bekommen das Scan-Foto dieser Karte (nur die
    ohne eigenes Foto; das Originalfoto bleibt hier). → {"shared": Anzahl}."""
    from app.services import card_photos

    card = _card_or_404(card_id, db)
    targets = db.scalars(select(PokemonCard).where(PokemonCard.id.in_(req.card_ids))).all()
    n = card_photos.share(db, card, targets)
    db.commit()
    if n and card.tcgdex_card_id:
        background_tasks.add_task(local_match.poke_learn, settings.matcher_url)
    return {"shared": n}


@router.post("/{card_id}/scan-photo", response_model=CardResponse)
async def upload_scan_photo(
    card_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    original: UploadFile = File(...),
    quad: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Scan-Foto nur für die Bilderkennung (die Karte zeigt weiter das
    Katalogbild): Zuschnitt + Original + Ecken; der Bild-Abgleich lernt es.
    """
    card = _card_or_404(card_id, db)
    try:
        card = store_scan_photo(db, card, file, original, quad=_parse_quad(quad))
    except ImageValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    if card.tcgdex_card_id:
        background_tasks.add_task(local_match.poke_learn, settings.matcher_url)
    return _card_response(db, card)


@router.delete("/{card_id}/image", response_model=CardResponse)
def delete_image(card_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    card = _card_or_404(card_id, db)
    card = remove_card_images(db, card)
    # Kein eigenes Foto / keine manuelle URL mehr → TCGdex-Bild (neu) abrufen,
    # damit nicht der Platzhalter stehen bleibt.
    if card.besessen and not card.bild_karte_url and not card.bild_pokedex_url:
        background_tasks.add_task(_trigger_image_fetch, card.id)
    return _card_response(db, card)


# ── Statistiken ──────────────────────────────────────────────────────────────

@router.get("/meta/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)):
    return collect_stats(db)


@router.get("/meta/enums", response_model=EnumsResponse)
def get_enums():
    return EnumsResponse(
        seltenheit=SELTENHEIT_VALUES,
        kartenversion=KARTENVERSION_VALUES,
        folierung=FOLIERUNG_VALUES,
        muster=MUSTER_VALUES,
        sprache=SPRACHE_VALUES,
        zustand=ZUSTAND_VALUES,
        prioritaet=PRIORITAET_VALUES,
    )


# ── Bilder Backfill ───────────────────────────────────────────────────────────

@router.post("/meta/fill-pokedex")
async def fill_pokedex(db: Session = Depends(get_db)):
    """
    Legt für jede Spezies 1–1025 ohne eigene Zeile einen Pokédex-Platzhalter
    an (services/pokedex_fill.py). Idempotent; bestehende Karten bleiben unberührt.
    """
    from app.services import pokedex_fill
    try:
        names = await pokedex_fill.fetch_species_names()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pokémon-Namen nicht ladbar: {exc}") from exc
    return pokedex_fill.fill_pokedex(db, names)


@router.post("/meta/backfill-images")
def backfill_images(
    background_tasks: BackgroundTasks,
    force: bool = False,
    user: AuthUser = Depends(require_auth),
):
    """
    Startet einen Hintergrund-Job, der TCGdex-Bilder + Metadaten für alle
    besessenen Karten des Benutzers abruft (services/card_images.py).
    force=True: auch Karten mit vorhandener bild_karte_url neu abrufen.
    """
    background_tasks.add_task(backfill_images_task, force, user.id)
    return {"detail": "Backfill gestartet – läuft im Hintergrund."}
