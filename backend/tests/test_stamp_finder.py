"""Play!-Stempel vorschlagen: Vorlage lernen (Proben an den matcher) und im eigenen Foto suchen."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete

from app.config import settings
from app.database import SessionLocal
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import stamp_finder

from test_scan_dataset import QUAD, _card, _jpeg, _upload

API = "/api/v1"
REF_ID = "teststamp-ref-1"
BOX = [[0.79, 0.37], [0.96, 0.37], [0.96, 0.465], [0.79, 0.465]]


@pytest.fixture()
def catalog_ref():
    """Katalogbild (ohne Stempel) im lokalen Bild-Cache."""
    folder = Path(settings.images_dir) / "teststamp"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ref.jpg").write_bytes(_jpeg((90, 90, 90)))
    db = SessionLocal()
    db.add(TcgdexCatalog(card_id=REF_ID, region="west", set_id="teststamp", name_en="Charizard ex",
                         image_pfad=f"{Path(settings.images_dir).name}/teststamp/ref.jpg"))
    db.commit()
    db.close()
    yield
    db = SessionLocal()
    db.execute(delete(TcgdexCatalog).where(TcgdexCatalog.card_id == REF_ID))
    db.commit()
    db.close()
    (folder / "ref.jpg").unlink(missing_ok=True)


def _mock_matcher(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_stamp_suggest_for_own_cards(client, monkeypatch):
    """Vorschlag im eigenen Foto: Hinweis-Ecken weitergereicht, fremde Karten 404, ohne Foto 409, ohne Dienst 503."""
    from test_multiuser import _client_for, _drop_user, _make_user

    seen = {}

    async def find(photo, hint):
        seen.update(photo=photo, hint=hint)
        return {"quad": BOX, "score": 0.93, "ok": True, "template": True}

    monkeypatch.setattr(stamp_finder, "find", find)
    cid = _card(client, "sv03-125", prize_pack=True)
    bob = _make_user("bob-stamp")
    try:
        r = client.post(f"{API}/cards/{cid}/stamp-suggest", json={}, headers={"X-UI-Lang": "EN"})
        assert r.status_code == 409 and r.json()["detail"] == "There is no photo of your own to mark"
        _upload(client, cid, _jpeg((30, 60, 90)), QUAD)
        r = client.post(f"{API}/cards/{cid}/stamp-suggest", json={})
        assert r.status_code == 200, r.text
        assert r.json() == {"quad": BOX, "score": 0.93, "ok": True, "template": True}
        assert seen["hint"] is None and seen["photo"].is_file()
        r = client.post(f"{API}/cards/{cid}/stamp-suggest", json={"quad": BOX})
        assert r.status_code == 200 and seen["hint"] == BOX
        assert client.post(f"{API}/cards/{cid}/stamp-suggest", json={"quad": [[0, 0], [1, 0]]}).status_code == 422
        assert _client_for(bob).post(f"{API}/cards/{cid}/stamp-suggest", json={}).status_code == 404

        async def down(photo, hint):
            raise httpx.HTTPError("down")
        monkeypatch.setattr(stamp_finder, "find", down)
        assert client.post(f"{API}/cards/{cid}/stamp-suggest", json={}).status_code == 503
    finally:
        client.delete(f"{API}/cards/{cid}/image")
        client.delete(f"{API}/cards/{cid}")
        _drop_user(bob)


def test_find_reads_the_matcher_answer(monkeypatch, tmp_path):
    photo = tmp_path / "card.jpg"
    photo.write_bytes(_jpeg((1, 2, 3)))
    answers = iter([{"quad": BOX, "score": 0.91, "ok": True, "template": True},
                    {"quad": None, "score": None, "ok": False, "template": False}])
    sent = []

    def handler(request):
        sent.append(request.content)
        return httpx.Response(200, json=next(answers))

    _mock_matcher(monkeypatch, handler)
    assert asyncio.run(stamp_finder.find(photo, BOX)) == {"quad": BOX, "score": 0.91, "ok": True, "template": True}
    assert b'"quad"' in sent[0] and b"0.79" in sent[0]
    assert asyncio.run(stamp_finder.find(photo, None)) == {"quad": None, "score": None, "ok": False,
                                                           "template": False}
    assert b'name="quad"' not in sent[1]


def test_learn_stamp_template_sends_marked_and_catalog_samples(client, catalog_ref, monkeypatch):
    """Lernen: markierte Stempel mit Box, unmarkierte Prize-Pack-Fotos mit Katalogbild, der Rest bleibt draußen."""
    from test_multiuser import _client_for, _drop_user, _make_user

    marked = _card(client, "sv03-125", prize_pack=True)
    with_ref = _card(client, REF_ID, prize_pack=True)
    no_ref = _card(client, "zz-no-catalog", prize_pack=True)
    plain = _card(client, REF_ID)
    bob = _make_user("bob-stamp-learn")
    captured = {}

    def handler(request):
        if request.url.path == "/stamp/template":
            return httpx.Response(200, json={"exists": True, "samples": 2, "marked": 1, "built_at": "x"})
        body = request.content
        start = body.index(b'name="samples"')
        chunk = body[start:].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
        captured["samples"] = json.loads(chunk)
        captured["photos"] = body.count(b'name="photos"')
        captured["refs"] = body.count(b'name="refs"')
        return httpx.Response(200, json={"samples": 3, "marked": 1, "guessed": 2, "w": 0.17, "h": 0.09,
                                         "built_at": "2026-11-11T00:00:00Z"})

    try:
        for cid in (marked, with_ref, no_ref, plain):
            _upload(client, cid, _jpeg((20, 30, 40)), QUAD)
        assert client.put(f"{API}/cards/{marked}/stamp", json={"quad": BOX}).status_code == 200
        found = {s["card_id"]: s for s in stamp_finder.samples()}
        assert found[marked]["box"] == [0.79, 0.37, 0.96, 0.465] and found[marked]["ref"] is None
        assert found[with_ref]["box"] is None and found[with_ref]["ref"].is_file()
        assert no_ref not in found and plain not in found
        mine = [cid for cid in found if cid in (marked, with_ref)]
        assert list(found).index(marked) < list(found).index(with_ref)        # markierte zuerst
        assert len(mine) == 2

        monkeypatch.setattr(stamp_finder, "samples", lambda: [found[marked], found[with_ref], found[with_ref]])
        _mock_matcher(monkeypatch, handler)
        r = client.post(f"{API}/scan-quality/stamps/template")
        assert r.status_code == 200, r.text
        assert r.json()["guessed"] == 2
        assert captured["photos"] == 3 and captured["refs"] == 2
        assert captured["samples"][0] == {"photo": 0, "box": [0.79, 0.37, 0.96, 0.465], "ref": None}
        assert captured["samples"][1] == {"photo": 1, "box": None, "ref": 0}
        assert client.get(f"{API}/scan-quality/stamps/template").json()["exists"] is True
        assert _client_for(bob).post(f"{API}/scan-quality/stamps/template").status_code == 403

        monkeypatch.setattr(stamp_finder, "samples", lambda: [found[marked]])
        r = client.post(f"{API}/scan-quality/stamps/template", headers={"X-UI-Lang": "EN"})
        assert r.status_code == 422 and r.json()["detail"] == "Too few stamps to learn from — mark at least 3 by hand"
    finally:
        for cid in (marked, with_ref, no_ref, plain):
            client.delete(f"{API}/cards/{cid}/image")
            client.delete(f"{API}/cards/{cid}")
        _drop_user(bob)
