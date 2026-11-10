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

    async def fake_resolve(db, reads, default_lang="DE"):
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


def _scan(client, png_bytes):
    r = client.post("/api/v1/scan", files={"file": ("scan.png", png_bytes, "image/png")})
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
