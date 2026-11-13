"""Reverse holo? — Scan-Zuschnitt gegen das Katalogbild über matcher /foil; Modell im Admin-Bereich."""
import json

import httpx
import pytest

from app.config import settings
from app.database import SessionLocal
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import catalog_images, foil_check

API = "/api/v1"
CID = "zzfoil-007"


@pytest.fixture()
def catalog_row():
    db = SessionLocal()
    db.merge(TcgdexCatalog(card_id=CID, region="west", set_id="zzfoil", local_id="007", name="Foilmon",
                           image_url="https://assets.tcgdex.net/en/zz/zzfoil/007/high.webp"))
    db.commit()
    yield CID
    db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == CID).delete()
    db.commit()
    db.close()


@pytest.fixture()
def matcher(monkeypatch):
    """matcher mit /foil und /foil/model als MockTransport; gibt die gesehenen Anfragen zurück."""
    seen: list[httpx.Request] = []
    state = {"model": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/foil":
            if state["model"] is None:
                return httpx.Response(200, json={"model": False})
            return httpx.Response(200, json={"model": True, "aligned": True, "art": 9.0, "frame": 30.0,
                                             "inliers": 300, "score": 2.1, "hint": True})
        if request.url.path == "/foil/model":
            if request.method == "PUT":
                body = request.content.decode("latin1")
                if '"features"' not in body:
                    return httpx.Response(422, json={"detail": "not a foil model"})
                state["model"] = {"exists": True, "precision": 0.8}
            if request.method == "DELETE":
                state["model"] = None
            return httpx.Response(200, json=state["model"] or {"exists": False})
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(foil_check.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings, "matcher_url", "http://matcher.test")
    return seen, state


def test_foil_check_without_a_model_says_so(client, catalog_row, matcher, monkeypatch):
    async def fetch(url):
        return b"REF"
    monkeypatch.setattr(catalog_images, "_fetch_image_bytes", fetch)
    r = client.post(f"{API}/scan/foil", files={"file": ("c.jpg", b"CROP", "image/jpeg")}, data={"card_id": CID})
    assert r.status_code == 200 and r.json() == {"model": False}
    seen, _ = matcher
    sent = seen[-1].content
    assert b"CROP" in sent and b"REF" in sent        # Zuschnitt und Katalogbild gehen an den matcher


def test_admin_uploads_model_then_scan_gets_hints(client, catalog_row, matcher, monkeypatch):
    async def fetch(url):
        return b"REF"
    monkeypatch.setattr(catalog_images, "_fetch_image_bytes", fetch)
    assert client.get(f"{API}/scan-quality/foil/model").json() == {"exists": False}
    bad = client.put(f"{API}/scan-quality/foil/model", files={"file": ("m.json", b"{}", "application/json")})
    assert bad.status_code == 422
    model = json.dumps({"features": ["log_art", "log_frame", "log_frame_art"]}).encode()
    ok = client.put(f"{API}/scan-quality/foil/model", files={"file": ("m.json", model, "application/json")})
    assert ok.status_code == 200 and ok.json()["exists"] is True
    r = client.post(f"{API}/scan/foil", files={"file": ("c.jpg", b"CROP", "image/jpeg")}, data={"card_id": CID})
    assert r.json()["hint"] is True and r.json()["score"] == 2.1
    assert client.delete(f"{API}/scan-quality/foil/model").json() == {"exists": False}


def test_foil_check_is_quiet_without_matcher_or_reference(client, catalog_row, monkeypatch):
    monkeypatch.setattr(settings, "matcher_url", "")
    r = client.post(f"{API}/scan/foil", files={"file": ("c.jpg", b"CROP", "image/jpeg")}, data={"card_id": CID})
    assert r.json() == {"model": False}
    monkeypatch.setattr(settings, "matcher_url", "http://127.0.0.1:9")      # niemand da

    async def fetch(url):
        return b"REF"
    monkeypatch.setattr(catalog_images, "_fetch_image_bytes", fetch)
    r = client.post(f"{API}/scan/foil", files={"file": ("c.jpg", b"CROP", "image/jpeg")}, data={"card_id": CID})
    assert r.status_code == 200 and r.json() == {"model": False}
    r = client.post(f"{API}/scan/foil", files={"file": ("c.jpg", b"CROP", "image/jpeg")}, data={"card_id": "../x"})
    assert r.status_code == 422


def test_model_admin_only(client):
    from test_multiuser import _client_for, _drop_user, _make_user

    bob = _make_user("bob-foil")
    try:
        assert _client_for(bob).get(f"{API}/scan-quality/foil/model").status_code == 403
    finally:
        _drop_user(bob)
