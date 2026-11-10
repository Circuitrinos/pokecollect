"""Live-Vorschau der Kamera: /scan/detect reicht an den matcher weiter (oder meldet „nicht verfügbar")."""
import httpx
import pytest

from app.config import settings
from app.services.scan import local_match

API = "/api/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture()
def matcher(monkeypatch):
    monkeypatch.setattr(settings, "matcher_url", "http://matcher.test")
    calls = []

    def install(status, body=None):
        def handler(req: httpx.Request):
            calls.append(req.url.path)
            return httpx.Response(status, json=body or {})
        real = httpx.AsyncClient
        monkeypatch.setattr(local_match.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    install.calls = calls
    return install


def _detect(client):
    r = client.post(f"{API}/scan/detect", files={"file": ("f.jpg", PNG, "image/jpeg")})
    assert r.status_code == 200, r.text
    return r.json()


def test_umrisse_vom_matcher(client, matcher):
    q = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.5], [0.1, 0.5]]
    matcher(200, {"cards": [{"quad": q, "area": 0.1}], "ms": 300})
    body = _detect(client)
    assert body == {"available": True, "cards": [{"quad": q}]}
    assert matcher.calls == ["/detect"]


def test_matcher_beschaeftigt_heisst_leer_aber_verfuegbar(client, matcher):
    matcher(429)
    assert _detect(client) == {"available": True, "cards": []}


@pytest.mark.parametrize("status", [503, 500, 404])
def test_matcher_laedt_oder_fehlt(client, matcher, status):
    matcher(status)
    assert _detect(client) == {"available": False, "cards": []}


def test_ohne_matcher_url(client, monkeypatch):
    monkeypatch.setattr(settings, "matcher_url", "")
    assert _detect(client) == {"available": False, "cards": []}


def test_leeres_bild(client):
    assert client.post(f"{API}/scan/detect", files={"file": ("f.jpg", b"", "image/jpeg")}).status_code == 400
