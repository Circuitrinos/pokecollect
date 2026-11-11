from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas._grenzen import PgNichtNegativ
from app.schemas._validators import reject_control_chars, reject_explicit_null
from app.schemas.card import CardResponse

DeckFormat = Literal["standard", "expanded", "unlimited"]
DeckSection = Literal["pokemon", "trainer", "energy"]


class DeckCreate(BaseModel):
    _v_ctrl = field_validator("*", mode="before")(reject_control_chars)

    name: str = Field(min_length=1, max_length=120)
    format: DeckFormat = "standard"
    notizen: Optional[str] = Field(None, max_length=4000)
    text: Optional[str] = Field(None, max_length=20000)   # Deckliste im PTCGL-Format
    fill: bool = False                                    # gleich mit eigenen Karten auffüllen


class DeckUpdate(BaseModel):
    _v_ctrl = field_validator("*", mode="before")(reject_control_chars)
    _v_null = field_validator("name", "format", mode="before")(reject_explicit_null)

    name: Optional[str] = Field(None, min_length=1, max_length=120)
    format: Optional[DeckFormat] = None
    notizen: Optional[str] = Field(None, max_length=4000)


class DeckImport(BaseModel):
    _v_ctrl = field_validator("*", mode="before")(reject_control_chars)

    text: str = Field(min_length=1, max_length=20000)
    replace: bool = False      # Liste ersetzen statt ergänzen


class DeckSlotAdd(BaseModel):
    _v_ctrl = field_validator("*", mode="before")(reject_control_chars)

    tcgdex_card_id: str = Field(min_length=1, max_length=64)
    count: int = Field(1, ge=1, le=60)


class DeckSlotUpdate(BaseModel):
    _v_null = field_validator("count", "section", mode="before")(reject_explicit_null)

    count: Optional[int] = Field(None, ge=0, le=60)    # 0 = aus der Liste nehmen
    section: Optional[DeckSection] = None


class DeckCardsAdd(BaseModel):
    card_ids: list[PgNichtNegativ] = Field(min_length=1, max_length=500)
    move: bool = False         # Karten aus einem anderen Deck herübernehmen


class DeckIssue(BaseModel):
    level: Literal["error", "warning"]
    code: str
    count: int = 0
    name: Optional[str] = None
    slots: list[int] = []


class DeckLegality(BaseModel):
    status: Literal["legal", "check", "illegal"]
    format: str
    min_mark: str
    total: int
    sections: dict[str, int]
    issues: list[DeckIssue]
    need_data: bool = False


class DeckAssigned(BaseModel):
    match: Literal["exact", "same_name", "other_print"]
    card: CardResponse


class DeckSlotResponse(BaseModel):
    id: int
    tcgdex_card_id: Optional[str] = None
    name: str
    set_code: Optional[str] = None
    number: Optional[str] = None
    count: int
    section: str
    match: Optional[str] = None
    image_url: Optional[str] = None
    rarity: Optional[str] = None
    stage: Optional[str] = None
    trainer_type: Optional[str] = None
    status: str                 # ok | basic | not_legal | unknown | unmatched
    legal_mark: Optional[str] = None
    ace_spec: bool = False
    cards: list[DeckAssigned] = []
    available: int = 0          # passende eigene Karten in keinem Deck
    available_other: int = 0    # … davon Pokémon aus anderem Set (andere Attacken möglich)


class DeckSummary(BaseModel):
    id: int
    name: str
    format: str
    notizen: Optional[str] = None
    erstellt_am: Optional[datetime] = None
    aktualisiert_am: Optional[datetime] = None
    total: int = 0
    in_deck: int = 0            # eigene Karten, die einen Platz der Liste füllen
    status: str = "check"
    cover_url: Optional[str] = None


class DeckDetail(DeckSummary):
    legality: DeckLegality
    slots: list[DeckSlotResponse]
    extra: list[CardResponse] = []   # Karten im Deck, die auf keinen Platz passen


class DeckImportResult(BaseModel):
    deck: DeckDetail
    lines: int
    matched: int
    unmatched: int
    not_found: list[str] = []
    unreadable: list[str] = []


class DeckFillResult(BaseModel):
    deck: DeckDetail
    added: int


class DeckCandidate(BaseModel):
    match: Literal["exact", "same_name", "other_print"]
    card: CardResponse
