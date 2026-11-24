"""
Hüllen-Aufkleber (services/sleeve_tags.py): Bögen drucken, Drucker kalibrieren,
gescannten Aufkleber nachschlagen und mit einer Karte verknüpfen.

Jeder Benutzer sieht nur seine eigenen Aufkleber (tenancy.py) — ein fremder
Aufkleber ist hier schlicht „nicht gefunden".
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.card import PokemonCard
from app.models.sleeve_tag import SleeveTag, TagSheet
from app.schemas._grenzen import PG_INTEGER_MAX
from app.schemas.card import CardResponse
from app.schemas.sleeve_tag import (LinkRequest, NewSheets, SheetSummary, TagResponse, TagSettings,
                                    TagSettingsResponse)
from app.services import sleeve_tags as st
from app.services import tag_pdf
from app.services.card_collections import with_collections

router = APIRouter(prefix="/tags", tags=["tags"])

LANG = Query("EN", pattern="^(EN|DE)$")


def _settings_response(s: st.DruckEinstellung) -> TagSettingsResponse:
    bogen = tag_pdf.BOEGEN[s.bogen]
    return TagSettingsResponse(base_url=s.base_url, offset_x_mm=s.offset_x_mm, offset_y_mm=s.offset_y_mm,
                               sheet_name=bogen.name, per_sheet=bogen.per_sheet)


def _tag_response(db: Session, tag: SleeveTag, with_card: bool = True) -> TagResponse:
    sheet = db.get(TagSheet, tag.sheet_id)
    card = None
    if with_card and tag.card_id is not None:
        row = db.get(PokemonCard, tag.card_id)
        if row is not None:
            card = with_collections(db, [row])[0]
    return TagResponse(serial=tag.serial, code=st.code_for(tag.serial), sheet_nr=sheet.nr if sheet else None,
                       slot=tag.slot, card_id=tag.card_id, linked_at=tag.verknuepft_am, card=card)


def _tag_or_error(db: Session, code: str) -> SleeveTag:
    try:
        serial = st.parse(code)
    except st.CodeError as e:
        raise HTTPException(status_code=400, detail={"reason": e.reason})
    tag = db.get(SleeveTag, serial)
    if tag is None:
        raise HTTPException(status_code=404, detail={"reason": "unknown", "serial": serial})
    return tag


def _pdf(data: bytes, name: str) -> Response:
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store"})


# ── Einstellungen ────────────────────────────────────────────────────────────

@router.get("/settings", response_model=TagSettingsResponse)
def get_settings(db: Session = Depends(get_db)):
    return _settings_response(st.get_settings(db))


@router.put("/settings", response_model=TagSettingsResponse)
def put_settings(body: TagSettings, db: Session = Depends(get_db)):
    base = body.base_url.strip().rstrip("/")
    if base and not st.valid_base_url(base):
        raise HTTPException(status_code=422, detail={"reason": "base_url"})
    s = st.get_settings(db)
    s.base_url = base
    s.offset_x_mm, s.offset_y_mm = round(body.offset_x_mm, 2), round(body.offset_y_mm, 2)
    st.save_settings(db, s)
    return _settings_response(s)


# ── Bögen ────────────────────────────────────────────────────────────────────

@router.get("/sheets", response_model=list[SheetSummary])
def list_sheets(db: Session = Depends(get_db)):
    return st.sheet_summaries(db)


@router.post("/sheets", response_model=list[SheetSummary], status_code=201)
def create_sheets(body: NewSheets, db: Session = Depends(get_db)):
    s = st.get_settings(db)
    if body.start_slot >= tag_pdf.BOEGEN[s.bogen].per_sheet:
        raise HTTPException(status_code=422, detail={"reason": "start_slot"})
    sheets = st.new_sheets(db, body.count, body.start_slot, s.bogen)
    ids = {sh.id for sh in sheets}
    return [x for x in st.sheet_summaries(db) if x["id"] in ids]


@router.get("/sheets/pdf")
def sheets_pdf(ids: str = Query(..., pattern=r"^\d{1,9}(,\d{1,9}){0,19}$"), lang: str = LANG,
               db: Session = Depends(get_db)):
    """Ein PDF für einen oder mehrere Bögen (auch zum Nachdrucken)."""
    s = st.get_settings(db)
    if not s.base_url:
        raise HTTPException(status_code=409, detail={"reason": "base_url"})
    wanted = [int(i) for i in ids.split(",")]
    found = {sh.id: sh for sh in db.scalars(select(TagSheet).where(TagSheet.id.in_(wanted)))}
    sheets = [found[i] for i in wanted if i in found]
    if not sheets:
        raise HTTPException(status_code=404, detail={"reason": "unknown"})
    nrs = "-".join(str(sh.nr) for sh in sheets)
    return _pdf(st.sheets_pdf(db, sheets, s.base_url, lang), f"sleeve-tags-sheet-{nrs}.pdf")


@router.delete("/sheets/{sheet_id}", status_code=204)
def delete_sheet(sheet_id: int = Path(..., ge=1, le=PG_INTEGER_MAX), db: Session = Depends(get_db)):
    """Fehldruck verwerfen — nur solange keiner seiner Aufkleber verknüpft ist."""
    sheet = db.get(TagSheet, sheet_id)
    if sheet is None:
        raise HTTPException(status_code=404, detail={"reason": "unknown"})
    if any(t.card_id is not None for t in st.sheet_tags(db, sheet.id)):
        raise HTTPException(status_code=409, detail={"reason": "linked"})
    for t in st.sheet_tags(db, sheet.id):
        db.delete(t)
    db.delete(sheet)
    db.commit()


@router.get("/calibration.pdf")
def calibration_pdf(lang: str = LANG, db: Session = Depends(get_db)):
    return _pdf(st.calibration_pdf(db, lang), "sleeve-tags-calibration.pdf")


# ── Verknüpfen ───────────────────────────────────────────────────────────────

@router.get("/candidates", response_model=list[CardResponse])
def candidates(search: Optional[str] = Query(None, max_length=200), limit: int = Query(30, ge=1, le=100),
               db: Session = Depends(get_db)):
    """Eigene Karten ohne Aufkleber, die zuletzt erfassten zuerst."""
    return with_collections(db, st.candidates(db, search, limit))


@router.get("/by-card/{card_id}", response_model=Optional[TagResponse])
def tag_of_card(card_id: int = Path(..., ge=1, le=PG_INTEGER_MAX), db: Session = Depends(get_db)):
    tag = st.tag_for_card(db, card_id)
    return _tag_response(db, tag, with_card=False) if tag else None


@router.get("/{code}", response_model=TagResponse)
def get_tag(code: str = Path(..., max_length=300), db: Session = Depends(get_db)):
    return _tag_response(db, _tag_or_error(db, code))


@router.put("/{code}", response_model=TagResponse)
def link_tag(body: LinkRequest, code: str = Path(..., max_length=300), db: Session = Depends(get_db)):
    tag = _tag_or_error(db, code)
    card = db.get(PokemonCard, body.card_id)
    if card is None:
        raise HTTPException(status_code=404, detail={"reason": "card"})
    if not card.besessen:
        raise HTTPException(status_code=422, detail={"reason": "not_owned"})
    try:
        st.link(db, tag, card, replace=body.replace)
    except st.TagConflict as e:
        db.rollback()
        other = st.code_for(e.other) if e.reason == "card_tagged" else e.other
        raise HTTPException(status_code=409, detail={"reason": e.reason, "other": other})
    return _tag_response(db, tag)


@router.delete("/{code}/card", status_code=204)
def unlink_tag(code: str = Path(..., max_length=300), db: Session = Depends(get_db)):
    st.unlink(db, _tag_or_error(db, code))
