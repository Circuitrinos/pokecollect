"""
Bild-Abgleich (provider "local") nach einem Neustart: /scan/status meldet den
Ladezustand des matcher-Dienstes, und ein Scan während des Ladens nennt die
Ursache ("bild_abgleich_startet") statt eines allgemeinen Lesefehlers.
"""
import httpx
import pytest

import app.api.v1.scan as scan_module
from app.config import settings
from app.schemas.scan import ScanCandidate
from app.services.scan import local_match


@pytest.fixture()
def provider_local(client, monkeypatch):
    client.put("/api/v1/settings", json={"scan_reader_provider": "local"})
    monkeypatch.setattr(settings, "matcher_url", "http://matcher.test")

    async def fake_resolve(db, reads, default_lang="DE", **kw):
        return [ScanCandidate(position=r.position or 0, confidence=0.9, raw=r,
                              suggested={"kartenname": r.name or ""}) for r in reads]
    monkeypatch.setattr(scan_module, "resolve_reads", fake_resolve)
    monkeypatch.setattr(scan_module.ocr, "extract", lambda *a, **k: [])

    def matcher_replies(status: int, body):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/match"
            return httpx.Response(status, json=body) if not isinstance(body, str) \
                else httpx.Response(status, text=body)
        monkeypatch.setattr(local_match, "_client",
                            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield matcher_replies
    client.put("/api/v1/settings", json={"scan_reader_provider": "gemini"})


def _status(client, monkeypatch, health: dict):
    monkeypatch.setattr(local_match, "status", lambda base_url: health)
    r = client.get("/api/v1/scan/status")
    assert r.status_code == 200, r.text
    return r.json()


def test_status_meldet_ladezustand(client, provider_local, monkeypatch):
    body = _status(client, monkeypatch, {"status": "loading", "phase": "index 12/81"})
    assert body["provider"] == "local"
    assert body["local"] == {"status": "loading", "phase": "index 12/81"}
    assert body["active"] != "local"


def test_status_bereit(client, provider_local, monkeypatch):
    body = _status(client, monkeypatch, {"status": "ready", "phase": None})
    assert body["local"]["status"] == "ready"
    assert body["active"] == "local"


def test_status_nicht_erreichbar(client, provider_local, monkeypatch):
    body = _status(client, monkeypatch, {})
    assert body["local"] == {"status": "unreachable", "phase": None}


def test_status_ohne_bild_abgleich_hat_kein_local(client):
    assert client.get("/api/v1/scan/status").json()["local"] is None


def _scan(client, png_bytes, mode="single"):
    r = client.post("/api/v1/scan", files={"file": ("scan.png", png_bytes, "image/png")}, data={"mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def test_scan_waehrend_start_nennt_ursache(client, provider_local, png_bytes):
    provider_local(503, {"detail": "matcher not ready: loading"})
    body = _scan(client, png_bytes)
    assert body["engine"] == "ocr"
    assert body["hinweis_art"] == "bild_abgleich_startet"
    assert "startet" in body["hinweis"]


def test_scan_matcherfehler_bleibt_reader_fehler(client, provider_local, png_bytes):
    provider_local(500, "boom")
    body = _scan(client, png_bytes)
    assert body["hinweis_art"] == "reader_fehler"


def test_scan_treffer_und_zeitlog(client, provider_local, png_bytes, caplog):
    provider_local(200, {"mode": "single", "ms": 640, "timing": {"detect": 300, "embed": 90},
                         "cards": [{"position": None, "quad": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
                                    "auto": True, "confidence": 0.95,
                                    "candidates": [{"card_id": "sv03-125", "name": "Charizard ex",
                                                    "local_id": "125", "set_name": "Obsidian Flames"}]}]})
    caplog.set_level("INFO")
    body = _scan(client, png_bytes)
    assert body["engine"] == "local" and body["hinweis_art"] is None
    assert body["candidates"][0]["raw"]["tcgdex_card_id"] == "sv03-125"
    logs = caplog.text
    assert "davon Matcher 640 ms" in logs and "detect" in logs
    assert "Erkennung" in logs and "Auflösung/TCGdex" in logs


def test_zuschnitt_neu_abgleichen_nutzt_modus_card(client, provider_local, png_bytes, monkeypatch):
    """Nach dem Ecken-Editor schickt das UI den Zuschnitt mit mode=card: der matcher erkennt nicht neu."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"mode": "card", "ms": 90, "cards": [{
            "position": None, "quad": [[0, 0], [1, 0], [1, 1], [0, 1]], "auto": True, "confidence": 0.95,
            "candidates": [{"card_id": "sv03-125", "name": "Charizard ex", "local_id": "125"}]}]})
    monkeypatch.setattr(local_match, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = client.post("/api/v1/scan", files={"file": ("crop.png", png_bytes, "image/png")}, data={"mode": "card"})
    assert r.status_code == 200, r.text
    assert b'name="mode"\r\n\r\ncard' in seen["body"]
    body = r.json()
    assert body["mode"] == "card" and body["engine"] == "local"
    assert body["candidates"][0]["raw"]["tcgdex_card_id"] == "sv03-125"


def test_modus_card_ohne_bild_abgleich_liest_als_einzelkarte(client, png_bytes, monkeypatch):
    calls = {}

    def fake_ocr(data, mode="single", rows=0, cols=0):
        calls["mode"] = mode
        return []
    import app.api.v1.scan as scan_module
    monkeypatch.setattr(scan_module.ocr, "extract", fake_ocr)
    client.put("/api/v1/settings", json={"scan_reader_provider": "ocr"})
    try:
        r = client.post("/api/v1/scan", files={"file": ("crop.png", png_bytes, "image/png")}, data={"mode": "card"})
        assert r.status_code == 200, r.text
        assert calls["mode"] == "single"
    finally:
        client.put("/api/v1/settings", json={"scan_reader_provider": "gemini"})


def test_scan_gibt_stempel_befund_weiter(client, provider_local, png_bytes):
    """Play!-Stempel: der Befund des matchers kommt beim Kandidaten an (sure → das UI hakt Prize Pack an)."""
    card = lambda cid, stamp: {"position": None, "quad": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],  # noqa: E731
                               "auto": True, "confidence": 0.95, "stamp": stamp,
                               "candidates": [{"card_id": cid, "name": "X", "local_id": "1"}]}
    provider_local(200, {"mode": "multi", "ms": 10, "cards": [
        card("sv03-1", {"score": 0.93, "quad": [[0.7, 0.4], [0.9, 0.4], [0.9, 0.5], [0.7, 0.5]], "ok": True, "sure": True}),
        card("sv03-2", {"score": 0.4, "quad": [[0.7, 0.4], [0.9, 0.4], [0.9, 0.5], [0.7, 0.5]], "ok": False, "sure": False}),
        card("sv03-3", None),                     # ohne Vorlage / älterer matcher
        card("sv03-4", {"score": "kaputt"}),
    ]})
    body = _scan(client, png_bytes, mode="multi")
    stamps = {c["raw"]["tcgdex_card_id"]: c["stamp"] for c in body["candidates"]}
    assert stamps["sv03-1"]["sure"] is True and stamps["sv03-1"]["score"] == 0.93
    assert stamps["sv03-2"]["ok"] is False
    assert stamps["sv03-3"] is None and stamps["sv03-4"] is None


def test_schraege_mappenseite_schickt_ecken_an_den_matcher(client, provider_local, png_bytes, monkeypatch):
    """Geneigtes Raster: die Ecken gehen (gerundet, geprüft) an den matcher — bei Mappe und losen Karten."""
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"mode": "binder", "ms": 50, "page": "straightened", "cards": [{
            "position": 4, "quad": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]], "auto": True, "confidence": 0.95,
            "candidates": [{"card_id": "sv03-125", "name": "Charizard ex", "local_id": "125"}]}]})
    monkeypatch.setattr(local_match, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    page = "[[0.12345678, 0.1], [0.9, 0.05], [0.95, 0.97], [0.08, 0.9]]"

    def post(mode: str, page: str):
        r = client.post("/api/v1/scan", files={"file": ("p.png", png_bytes, "image/png")},
                        data={"mode": mode, "rows": "3", "cols": "3", "page": page})
        assert r.status_code == 200, r.text
        return r.json()

    body = post("binder", page)
    assert b'name="page"\r\n\r\n[[0.1235, 0.1], [0.9, 0.05], [0.95, 0.97], [0.08, 0.9]]' in seen[-1]
    assert body["candidates"][0]["raw"]["tcgdex_card_id"] == "sv03-125"
    post("multi", page)                                        # lose Karten mit Raster-Hilfe: auch
    assert b'name="page"\r\n\r\n[[0.1235' in seen[-1]
    post("single", page)                                       # eine Karte hat kein Raster
    assert b'name="page"' not in seen[-1]
    for bad in ("[[0, 0], [1, 0], [1, 1]]", "kaputt", "[[0, 0], [9, 0], [1, 1], [0, 1]]", "x" * 500):
        post("binder", bad)                                    # unbrauchbar → wie bisher ohne Ecken
        assert b'name="page"' not in seen[-1]


def test_live_scan_laesst_sichere_faecher_aus(client, provider_local, png_bytes, monkeypatch):
    """Live-Scan: die schon sicheren Fächer/Felder gehen als skip an den matcher — Mappe und lose Karten im Raster."""
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"mode": "binder", "ms": 5, "cards": []})
    monkeypatch.setattr(local_match, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    def post(mode: str, skip: str):
        r = client.post("/api/v1/scan", files={"file": ("p.png", png_bytes, "image/png")},
                        data={"mode": mode, "rows": "3", "cols": "3", "skip": skip})
        assert r.status_code == 200, r.text

    post("binder", "5, 0,5,x,9,²,12345," + "9" * 5000)
    assert b'name="skip"\r\n\r\n0,5\r\n' in seen[-1]
    post("binder", "")
    assert b'name="skip"' not in seen[-1]
    post("multi", "1,0")                                       # lose Karten im Raster: Felder wie Fächer
    assert b'name="skip"\r\n\r\n0,1\r\n' in seen[-1]
    post("single", "0,1")                                      # eine Karte hat keine Felder
    assert b'name="skip"' not in seen[-1]


def test_live_scan_seitenkarte(client, provider_local, png_bytes, monkeypatch):
    """Live-Scan: „new" legt die Seitenkarte an, eine ID ordnet das Bild ein — map_quad je Karte kommt durch."""
    seen: list[bytes] = []
    reply: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=reply)
    monkeypatch.setattr(local_match, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    def post(mode="binder", **data):
        r = client.post("/api/v1/scan", files={"file": ("p.png", png_bytes, "image/png")},
                        data={"mode": mode, "rows": "3", "cols": "3", **data})
        assert r.status_code == 200, r.text
        return r.json()

    q = [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]
    reply.update({"mode": "binder", "ms": 5, "cards": [{
        "position": None, "quad": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], "map_quad": q, "auto": True,
        "confidence": 0.95, "candidates": [{"card_id": "sv03-125", "name": "Charizard ex", "local_id": "125"}]}],
        "map": {"id": "ab12cd", "located": True, "whole": False, "area": 0.08, "inliers": 72,
                "h": [[0.33, 0, 0.36], [0, 0.24, 0.38], [0, 0, 1]]}})
    body = post(live="1", map="new")
    assert b'name="map"\r\n\r\nnew\r\n' in seen[-1]
    assert body["live_map"]["id"] == "ab12cd" and body["live_map"]["whole"] is False
    assert body["candidates"][0]["map_quad"] == q
    post(live="1", map="ab12cd")
    assert b'name="map"\r\n\r\nab12cd\r\n' in seen[-1]
    for extra in ({"map": "ab12cd"}, {"live": "1", "map": "../etc"}):   # ohne live / keine gültige ID
        post(**extra)
        assert b'name="map"' not in seen[-1]
    post("single", live="1", map="new")                                  # eine Karte braucht keine Seitenkarte
    assert b'name="map"' not in seen[-1]
    reply["map"] = {"located": True}                                     # kaputt → einfach keine Angabe
    assert post(live="1", map="ab12cd")["live_map"] is None


def test_live_bild_ohne_bild_abgleich_ohne_ocr(client, provider_local, png_bytes, monkeypatch):
    """Live-Bild, matcher lädt noch: keine OCR je Bild, nur der Hinweis — die App wartet."""
    provider_local(503, {"detail": "matcher not ready: loading"})
    monkeypatch.setattr(scan_module.ocr, "extract", lambda *a, **k: pytest.fail("OCR bei einem Live-Bild"))
    r = client.post("/api/v1/scan", files={"file": ("p.png", png_bytes, "image/png")}, data={"mode": "binder", "live": "1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "none" and body["candidates"] == [] and body["hinweis_art"] == "bild_abgleich_startet"


def test_viele_karten_gleichzeitig_geladen_trainer_ohne_dex_suche(client, provider_local, png_bytes, monkeypatch):
    """16 erkannte Karten: TCGdex-Abrufe laufen gleichzeitig; Trainer (keine Pokédex-Nr.)
    lösen keine Suche über Schwesterkarten aus — vorher 7 Abrufe je Trainer, nacheinander."""
    import asyncio
    import time as _time

    from app.services import tcgdex
    from app.services.scan import resolver

    calls: list[str] = []

    async def fake_get_card(card_id, lang="en"):
        calls.append(card_id)
        await asyncio.sleep(0.15)
        trainer = card_id.startswith("tr")
        return tcgdex.TcgdexCard.model_validate({
            "id": card_id, "localId": card_id.rsplit("-", 1)[1], "name": card_id,
            "category": "Trainer" if trainer else "Pokemon", "trainerType": "Item" if trainer else None,
            "stage": None if trainer else "Basic", "dexId": None if trainer else [25]})

    async def no_search(*a, **k):
        raise AssertionError("Schwesterkarten-Suche für einen Trainer")
    monkeypatch.setattr(tcgdex, "get_card", fake_get_card)
    monkeypatch.setattr(tcgdex, "search_cards", no_search)
    monkeypatch.setattr(scan_module, "resolve_reads", resolver.resolve_reads)
    cards = [{"position": i, "quad": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]], "auto": True, "confidence": 0.95,
              "candidates": [{"card_id": f"{'tr' if i % 2 else 'pk'}{i}-{i + 1}", "name": "x", "local_id": str(i + 1)}]}
             for i in range(12)]
    provider_local(200, {"mode": "binder", "ms": 5, "cards": cards})
    t0 = _time.perf_counter()
    r = client.post("/api/v1/scan", files={"file": ("p.png", png_bytes, "image/png")},
                    data={"mode": "binder", "rows": "4", "cols": "3", "default_language": "EN"})
    took = _time.perf_counter() - t0
    assert r.status_code == 200, r.text
    got = r.json()["candidates"]
    assert len(got) == 12 and all(c["match"] for c in got)
    assert len(set(calls)) == 12 and len(calls) == 12                              # jede Karte genau einmal
    assert took < 1.2, took                                                        # nacheinander wären es ≥ 1,8 s


def test_dexid_mit_nachkomma():
    """TCGdex liefert für japanische Promos (PCG*) dexId 384.1 — das kippte die ganze Karte."""
    from app.services import tcgdex
    assert tcgdex.TcgdexCard.model_validate({"id": "PCG2-067", "dexId": [384.1]}).dex_id == 384
    assert tcgdex.TcgdexCard.model_validate({"id": "x", "dexId": ["a", None]}).dex_id is None
    assert tcgdex.TcgdexCard.model_validate({"id": "x", "dexId": 7}).dex_id is None


def test_live_bilder_laden_dieselbe_karte_nicht_jedes_mal(client, provider_local, png_bytes, monkeypatch):
    """Live-Scan: dieselben Karten alle paar Sekunden — TCGdex nur beim ersten Bild; normale Scans immer frisch."""
    from app.services import tcgdex
    from app.services.scan import resolver

    calls: list[tuple[str, str]] = []

    async def fake_get_card(card_id, lang="en"):
        calls.append((card_id, lang))
        return tcgdex.TcgdexCard.model_validate({"id": card_id, "localId": "125", "name": "Glurak-ex"})
    monkeypatch.setattr(tcgdex, "get_card", fake_get_card)
    monkeypatch.setattr(resolver, "_LIVE_CACHE", {})
    monkeypatch.setattr(scan_module, "resolve_reads", resolver.resolve_reads)   # der echte Resolver
    provider_local(200, {"mode": "single", "ms": 5, "cards": [{
        "position": None, "quad": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], "auto": True, "confidence": 0.95,
        "candidates": [{"card_id": "sv03-125", "name": "Charizard ex", "local_id": "125"}]}]})

    def post(**extra):
        r = client.post("/api/v1/scan", files={"file": ("p.png", png_bytes, "image/png")},
                        data={"mode": "single", **extra})
        assert r.status_code == 200, r.text
        return r.json()["candidates"][0]

    first = post(live="1")
    n = len(calls)
    assert n >= 1 and first["match"]["tcgdex_card_id"] == "sv03-125"
    again = post(live="1")
    assert len(calls) == n and again["match"] == first["match"]       # aus dem Zwischenspeicher
    post()
    assert len(calls) == 2 * n                                         # ohne live: frisch geladen


def test_nur_der_druck_offen_sperrt_nicht(client, provider_local, png_bytes, monkeypatch):
    """Unsicher nur beim Druck (alle starken Treffer heißen gleich, z. B. Ultra Ball-
    Nachdrucke mit derselben Grafik): bester vorgewählt, nicht „bitte wählen" —
    sonst endet es mit „Keine davon" und die Karte hat keinen Katalog-Druck."""
    from app.schemas.scan import ScanMatch

    async def resolved(db, reads, default_lang="DE", **kw):     # wie ein Scan mit erreichbarem TCGdex
        return [ScanCandidate(position=r.position or 0, confidence=0.9, raw=r, suggested={"kartenname": r.name or ""},
                              match=ScanMatch(tcgdex_card_id=r.tcgdex_card_id, name=r.name)) for r in reads]

    monkeypatch.setattr(scan_module, "resolve_reads", resolved)

    def matcher(cands):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"mode": "single", "ms": 5, "cards": [{
                "position": None, "quad": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], "auto": False,
                "confidence": 0.45, "candidates": cands}]})
        monkeypatch.setattr(local_match, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    def scan():
        r = client.post("/api/v1/scan", files={"file": ("p.png", png_bytes, "image/png")}, data={"mode": "single"})
        assert r.status_code == 200, r.text
        return r.json()["candidates"][0]

    ball = lambda cid, inl: {"card_id": cid, "name": "Ultra Ball", "local_id": cid.rsplit("-", 1)[1], "art_inliers": inl}  # noqa: E731
    matcher([ball("me01-131", 78), ball("sv01-196", 75), ball("me02.5-213", 70)])
    c = scan()
    assert c["print_choice"] is True and c["needs_choice"] is False
    assert c["match"]["tcgdex_card_id"] == "me01-131" and len(c["alternatives"]) == 3
    # ein starker Treffer mit anderem Namen: wirklich unklar → weiter „bitte wählen"
    matcher([ball("me01-131", 78), {"card_id": "me01-119", "name": "Lillie's Determination", "local_id": "119",
                                     "art_inliers": 60}])
    c = scan()
    assert c["needs_choice"] is True and c["print_choice"] is False
    # schwacher andersnamiger Treffer zählt nicht
    matcher([ball("me01-131", 78), ball("sv01-196", 70), {"card_id": "dp3-124", "name": "Other", "local_id": "124",
                                                           "art_inliers": 20}])
    assert scan()["print_choice"] is True


def test_scan_benennt_basis_energie_wie_tcg_live(client, provider_local, png_bytes, monkeypatch):
    """Erkannte Basis-Energie: „Basic Psychic Energy" (TCGdex führt sie oft als „Psychic Energy")."""
    from app.services import tcgdex
    from app.services.scan import resolver

    async def fake_get_card(card_id, lang="en"):
        return tcgdex.TcgdexCard.model_validate({"id": card_id, "localId": "5", "name": "Psychic Energy",
                                                 "category": "Energy", "energyType": "Basic"})
    monkeypatch.setattr(tcgdex, "get_card", fake_get_card)
    monkeypatch.setattr(scan_module, "resolve_reads", resolver.resolve_reads)
    provider_local(200, {"mode": "single", "ms": 5, "cards": [{
        "position": None, "quad": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], "auto": True, "confidence": 0.95,
        "candidates": [{"card_id": "mee-005", "name": "Psychic Energy", "local_id": "5"}]}]})
    r = client.post("/api/v1/scan", files={"file": ("p.png", png_bytes, "image/png")},
                    data={"mode": "single", "default_language": "EN"})
    c = r.json()["candidates"][0]
    assert c["suggested"]["kartenname"] == "Basic Psychic Energy" and c["match"]["name"] == "Basic Psychic Energy"
