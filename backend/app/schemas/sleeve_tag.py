from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas._grenzen import PG_INTEGER_MAX
from app.schemas.card import CardResponse


class TagSettings(BaseModel):
    # Adresse der Web-App für den Link im QR-Code, z. B. "https://tcg.example.com"
    base_url: str = Field("", max_length=200)
    # Druckversatz aus dem Kalibrierbogen: rechts +, unten +
    offset_x_mm: float = Field(0.0, ge=-10, le=10)
    offset_y_mm: float = Field(0.0, ge=-10, le=10)


class TagSettingsResponse(TagSettings):
    sheet_name: str
    per_sheet: int


class NewSheets(BaseModel):
    count: int = Field(1, ge=1, le=10)
    # erste freie Stelle auf dem ersten Bogen (0-basiert, zeilenweise) — für
    # angebrochene Bögen
    start_slot: int = Field(0, ge=0, le=999)


class SheetSummary(BaseModel):
    id: int
    nr: int
    first: int
    last: int
    count: int
    linked: int
    start_slot: int
    created_at: Optional[datetime] = None


class TagResponse(BaseModel):
    serial: int
    code: str
    sheet_nr: Optional[int] = None
    slot: int
    card_id: Optional[int] = None
    linked_at: Optional[datetime] = None
    card: Optional[CardResponse] = None


class LinkRequest(BaseModel):
    card_id: int = Field(..., ge=1, le=PG_INTEGER_MAX)
    # True: Karte hat schon einen anderen Aufkleber → der alte wird frei
    replace: bool = False
