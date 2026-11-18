"""
TCGdex antwortet nicht (Brian 17.11.: über Stunden ~5 % 503) ≠ „Karte gibt es nicht".

Vorher machte der Scan aus jedem 503 „unbekannte Karte": die vom Bild-Abgleich
sicher erkannte Karte ging ohne Bild, Set, Nummer und Folien-Varianten in die
Prüfung (21 Karten bekamen das Foto als Kartenbild, 4 gar keine Verknüpfung).
Jetzt: Daten aus dem lokalen Katalog, und der Fehlschlag gilt nur kurz.

Kein Netz/DB: TCGdex gemockt, die Session ein Double (wie test_scan_resolver).
"""

import asyncio
import time

import httpx

from app.models.tcgdex_catalog import TcgdexCatalog
from app.schemas.scan import ScanRawRead
from app.services import tcgdex
from app.services.scan import resolver
from app.services.scan.resolver import resolve_one


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _CatalogDB:
    """Session-Double: nur Katalogzeilen per ID (get); Abfragen liefern `found`."""

    def __init__(self, rows: dict, found: list | None = None):
        self.rows = rows
        self.found = found or []

    def get(self, model, key):
        return self.rows.get(key) if model is TcgdexCatalog else None

    def scalars(self, stmt):
        return _Scalars(self.found if "tcgdex_catalog" in str(stmt) else [])


def _row():
    return TcgdexCatalog(
        card_id="me02-091", set_id="me02", local_id="091", local_id_num=91,
        name="Riesen-Eisbecher", name_en="Jumbo Ice Cream", set_name="Fatale Flammen",
        image="https://assets.tcgdex.net/en/me/me02/091", category="Trainer",
        trainer_type="Item", rarity="Uncommon",
        variants_normal=True, variants_reverse=True, variants_holo=False,
    )


def _boom(msg):
    async def f(*a, **k):
        raise AssertionError(msg)
    return f


def _outage(calls):
    async def get_card(cid, lang):
        calls.append((cid, lang))
        tcgdex.UNREACHABLE.set(True)   # wie _get_json bei 503
        return None
    return get_card


def test_get_json_tells_outage_from_missing_card():
    async def ask(status):
        transport = httpx.MockTransport(lambda req: httpx.Response(status))
        async with httpx.AsyncClient(transport=transport, base_url="https://api.tcgdex.net/v2/en") as c:
            token = tcgdex.UNREACHABLE.set(False)
            try:
                return await tcgdex._get_json(c, "/cards/me02-091"), tcgdex.UNREACHABLE.get()
            finally:
                tcgdex.UNREACHABLE.reset(token)

    assert asyncio.run(ask(503)) == (None, True)
    assert asyncio.run(ask(429)) == (None, True)
    assert asyncio.run(ask(404)) == (None, False)


def test_known_card_comes_from_the_catalog_when_tcgdex_is_down(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tcgdex, "get_card", _outage(calls))
    monkeypatch.setattr(tcgdex, "fetch_card_by_set_multilang", _boom("Karte steht fest — kein Set+Nummer"))
    monkeypatch.setattr(tcgdex, "search_cards", _boom("Karte steht fest — keine Namenssuche"))
    read = ScanRawRead(name="Jumbo Ice Cream", tcgdex_card_id="me02-091", language="EN", confidence=0.9)

    cand = asyncio.run(resolve_one(_CatalogDB({"me02-091": _row()}), read))

    assert cand.match is not None
    assert cand.match.tcgdex_card_id == "me02-091"
    assert cand.match.name == "Jumbo Ice Cream"
    assert cand.match.image_url == "https://assets.tcgdex.net/en/me/me02/091/high.webp"
    assert cand.foil_options == ["Normal", "Reverse Holo"]   # die Kipp-Prüfung kennt die Varianten
    assert cand.suggested["tcgdex_card_id"] == "me02-091"
    assert "match" not in cand.uncertain_fields
    assert calls == [("me02-091", "en")]


def test_outage_skips_the_english_retry(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tcgdex, "get_card", _outage(calls))
    monkeypatch.setattr(tcgdex, "search_cards", _boom("keine Namenssuche"))
    read = ScanRawRead(name="Riesen-Eisbecher", tcgdex_card_id="me02-091", language="DE", confidence=0.9)

    cand = asyncio.run(resolve_one(_CatalogDB({"me02-091": _row()}), read))

    assert cand.match is not None and cand.match.name == "Riesen-Eisbecher"
    # de (Ausfall) → nicht gleich noch einmal auf Englisch; das eine "en" holt den EN-Namen
    assert calls.count(("me02-091", "de")) == 1
    assert calls.count(("me02-091", "en")) == 1


def test_set_and_number_fall_back_to_the_catalog(monkeypatch):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(tcgdex, "fetch_card_by_set_multilang", none)
    monkeypatch.setattr(tcgdex, "get_card", none)
    monkeypatch.setattr(tcgdex, "search_cards", _boom("Set+Nummer reichen"))
    monkeypatch.setattr(resolver, "_resolve_set_id", lambda db, code: ("me02", None))
    read = ScanRawRead(name="Jumbo Ice Cream", set_code="PFL", number="091/094", language="EN", confidence=0.8)

    cand = asyncio.run(resolve_one(_CatalogDB({}, found=[_row()]), read))

    assert cand.match is not None and cand.match.tcgdex_card_id == "me02-091"


def test_live_cache_forgets_an_outage_quickly(monkeypatch):
    async def run(get_card):
        monkeypatch.setattr(tcgdex, "get_card", get_card)
        resolver._LIVE_CACHE.clear()
        token = resolver._cached.set(True)
        try:
            await resolver._get_card("me02-091", "en")
        finally:
            resolver._cached.reset(token)
        return resolver._LIVE_CACHE[("me02-091", "en")][0] - time.monotonic()

    async def missing(cid, lang):
        return None

    assert asyncio.run(run(_outage([]))) <= resolver._DOWN_TTL
    assert asyncio.run(run(missing)) > resolver._DOWN_TTL * 2   # „gibt es nicht" bleibt wie bisher länger
    resolver._LIVE_CACHE.clear()
