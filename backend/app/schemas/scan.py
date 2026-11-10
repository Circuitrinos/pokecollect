"""Pydantic-Schemas für den Karten-Scan."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator, Field

from app.schemas._grenzen import PgNichtNegativ  # #117
from app.schemas._validators import reject_control_chars

# "card" = bereits entzerrte Karte (Ecken im App-Editor gesetzt) → nur neu abgleichen
ScanMode = Literal["single", "multi", "binder", "card"]


class ScanRawRead(BaseModel):
    """Rohe Erkennung (vor TCGdex-Auflösung)."""
    name: Optional[str] = None
    set_code: Optional[str] = None      # aufgedrucktes Kürzel, z.B. "PAF"
    number: Optional[str] = None        # aufgedruckte Nummer, z.B. "007/091"
    language: Optional[str] = None       # DE/EN/CN/JP …
    position: Optional[int] = None       # Slot-Index bei Binder-Scan (0-basiert)
    confidence: Optional[float] = None   # Roh-Sicherheit der Engine (0..1)
    bbox: Optional[list[float]] = None   # [x,y,w,h] als Anteil 0..1 des Bildes
    quad: Optional[list[list[float]]] = None  # 4 Eckpunkte [[x,y]…] (0..1) TL,TR,BR,BL
    # Bereits bekannte TCGdex-Karte (Bild-Abgleich „local" bzw. Auswahl im
    # Picker): der Resolver nimmt sie direkt, statt Set/Nummer/Name zu deuten.
    tcgdex_card_id: Optional[str] = None

    # POST /scan/resolve nimmt diese Struktur direkt vom Client entgegen
    _v_ctrl = field_validator("*", mode="before")(reject_control_chars)


class ScanMatch(BaseModel):
    """Aufgelöste TCGdex-Karte."""
    tcgdex_card_id: Optional[str] = None
    name: Optional[str] = None
    englischer_name: Optional[str] = None
    set_id: Optional[str] = None
    set_code: Optional[str] = None
    set_name: Optional[str] = None
    local_id: Optional[str] = None
    rarity: Optional[str] = None
    dex_id: Optional[int] = None
    image_url: Optional[str] = None
    variants_normal: Optional[bool] = None
    variants_reverse: Optional[bool] = None
    variants_holo: Optional[bool] = None
    variants_firstedition: Optional[bool] = None


class ScanAlternative(BaseModel):
    """Weiterer möglicher Treffer des Bild-Abgleichs (für die Auswahl im UI)."""
    tcgdex_card_id: str
    name: Optional[str] = None
    set_name: Optional[str] = None
    local_id: Optional[str] = None
    image_url: Optional[str] = None
    art_inliers: int = 0


class ScanCandidate(BaseModel):
    """
    Ein Scan-Treffer inkl. Vorbefüllung für den Bestätigungs-Dialog.
    `suggested` ist direkt als Karten-Anlage verwendbar (editierbar im UI).
    """
    position: Optional[int] = None
    confidence: float = 0.0
    uncertain_fields: list[str] = []
    raw: ScanRawRead
    match: Optional[ScanMatch] = None
    suggested: dict = {}
    foil_options: list[str] = []          # erlaubte Folierungen laut variants
    # Bild-Abgleich („local"): weitere Kandidaten und ob der Nutzer wählen muss
    # (kein eindeutiger Treffer). Leer/False bei allen anderen Engines.
    alternatives: list[ScanAlternative] = []
    needs_choice: bool = False


class ScanResponse(BaseModel):
    engine: str                            # "gemini" | "openai" | "openrouter" | "local" | "ocr"
    mode: ScanMode
    candidates: list[ScanCandidate]
    limit_erreicht: bool = False           # ein LIMIT (Tages- ODER Rate-Limit) griff → OCR-Fallback
    hinweis: Optional[str] = None          # menschenlesbarer Hinweis (DE)
    # Maschinenlesbare Fallback-Ursache (Issue #21): erlaubt dem UI, „Rate-Limit
    # erreicht" von „Key ungültig" zu unterscheiden. None = kein Hinweis.
    # "gemini_fehler" = harter Gemini-Fehler; "reader_fehler" = harter Fehler
    # eines OpenAI-/OpenRouter-Lesemodells (Issue #57) oder ein nicht
    # erreichbarer Bild-Abgleich; "bild_abgleich_startet" = der matcher-Dienst
    # lädt noch (kurz nach einem Neustart).
    hinweis_art: Optional[Literal[
        "tageslimit", "rate_limit", "key_ungueltig", "gemini_fehler", "reader_fehler",
        "bild_abgleich_startet",
    ]] = None


# ── Commit (bestätigte Karten ablegen) ───────────────────────────────────────

class ScanCommitItem(BaseModel):
    kartenname: str
    pokedex_nr: Optional[PgNichtNegativ] = None
    englischer_name: Optional[str] = None
    set_edition: Optional[str] = None
    karten_nr: Optional[str] = None
    seltenheit: Optional[str] = None
    kartenversion: Optional[str] = None
    folierung: Optional[str] = None
    muster: Optional[str] = None           # Folierungs-Muster (#63)
    prize_pack: bool = False               # Play! Pokémon-Stempel (Prize Pack), im Scan angehakt
    sprache: Optional[str] = "DE"
    zustand: Optional[str] = None
    notizen: Optional[str] = None
    tcgdex_card_id: Optional[str] = None
    set_id: Optional[str] = None
    dex_id: Optional[PgNichtNegativ] = None
    bild_karte_url: Optional[str] = None   # TCGdex high.webp (wird host-validiert)
    # Mappenseiten-Scan: Fach auf der fotografierten Seite (0-basiert); landet
    # auf der Seite ab start_position (ohne: auf einer neuen Seite am Ende).
    position: Optional[PgNichtNegativ] = None
    im_pokedex: bool = False               # je Karte: als Pokédex-Vertreter setzen
    prioritaet: Optional[str] = None       # nur für Wunschliste
    # Gleiche Karte mehrfach besessen: so viele Exemplare anlegen (je eine Zeile,
    # alle mit demselben Bild).
    copies: int = Field(1, ge=1, le=50)

    # Scan-Commit legt Karten an → dieselbe Steuerzeichen-Sperre wie CardBase
    # (ein NUL im erkannten Namen endete sonst als 500, #55).
    _v_ctrl = field_validator("*", mode="before")(reject_control_chars)


class ScanCommitRequest(BaseModel):
    # Ziel: Pokédex (besessen), Sammlung, oder Wunschliste (nicht besessen)
    target: Literal["pokedex", "collection", "wishlist"] = "pokedex"
    collection_id: Optional[int] = None
    # Gewähltes Fach (0-basiert, über alle Seiten gezählt): Karten ohne
    # position füllen freie Fächer ab hier; beim Mappenseiten-Scan der Anfang
    # der Zielseite. Ohne: erstes freies Fach bzw. neue Seite am Ende.
    start_position: Optional[PgNichtNegativ] = None
    items: list[ScanCommitItem]


class ScanCommitResponse(BaseModel):
    created: int
    card_ids: list[int]                    # je Item das erste Exemplar (Reihenfolge = items)
    all_card_ids: list[int] = []           # alle angelegten Exemplare
    copy_ids: list[list[int]] = []         # je Item alle Exemplare (Foto für jedes Exemplar)
    # Sammlung: belegte Fächer dieses Commits + nächstes freies Fach danach
    # (Weiterscannen ins nächste Fach / auf die nächste Seite)
    positions: list[int] = []
    next_position: Optional[int] = None
