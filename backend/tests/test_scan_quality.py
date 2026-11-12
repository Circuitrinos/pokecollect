"""Schwache Scans: Prüfung über den Bild-Abgleich, Liste, Zuschnitt korrigieren, Export."""
import asyncio
import io
import json
import zipfile

import pytest

from app.database import SessionLocal
from app.models.scan_check import ScanCheck
from app.services import scan_quality
from app.services.scan import local_match

from test_scan_dataset import QUAD, _card, _jpeg, _upload

API = "/api/v1"
BETTER = [[0.12, 0.1], [0.88, 0.1], [0.9, 0.9], [0.1, 0.9]]


@pytest.fixture()
def fake_matcher(monkeypatch):
    seen = []

    async def verify(client, row):
        seen.append(row["id"])
        if row["tcgdex_card_id"] == "sv03-125":       # schief zugeschnitten
            return {"templates": 1, "inliers": 31, "art_inliers": 9, "corner_err": 0.11, "geo_ok": True,
                    "suggested_quad": BETTER}
        if row["tcgdex_card_id"] == "zz-none":
            return {"templates": 0}
        return {"templates": 2, "inliers": 120, "art_inliers": 40, "corner_err": 0.008, "geo_ok": True}

    monkeypatch.setattr(scan_quality, "verify_one", verify)
    monkeypatch.setattr(local_match, "poke_learn", lambda url: None)
    return seen


def _cleanup():
    db = SessionLocal()
    db.query(ScanCheck).delete()
    db.commit()
    db.close()


def test_classify():
    assert scan_quality.classify({"templates": 0}) == "no_reference"
    assert scan_quality.classify({"templates": 1, "inliers": 10, "corner_err": 0.01, "geo_ok": True}) == "low"
    assert scan_quality.classify({"templates": 1, "inliers": 90, "corner_err": 0.06, "geo_ok": True}) == "low"
    assert scan_quality.classify({"templates": 1, "inliers": 90, "corner_err": 0.01, "geo_ok": True}) == "ok"


def test_check_list_dismiss_and_recrop(client, fake_matcher):
    _cleanup()
    bad = _card(client, "sv03-125")
    good = _card(client, "sv03-126")
    nref = _card(client, "zz-none")
    try:
        orig = _jpeg((30, 60, 90))
        for cid in (bad, good, nref):
            _upload(client, cid, orig, QUAD)
        asyncio.run(scan_quality.run())
        assert {bad, good, nref} <= set(fake_matcher)
        r = client.get(f"{API}/scan-quality")
        assert r.status_code == 200, r.text
        body = r.json()
        assert [i["card_id"] for i in body["items"] if i["card_id"] in (bad, good, nref)] == [bad]
        item = next(i for i in body["items"] if i["card_id"] == bad)
        assert item["has_suggestion"] and item["own"] and item["corner_err"] == 0.11
        assert item["suggested_quad"] == BETTER and item["quad"] == QUAD and item["original"]
        assert "catalog_image" in item
        assert body["counts"]["low"] >= 1 and body["counts"]["no_reference"] >= 1
        assert client.get(f"{API}/scan-quality", params={"status": "no_reference"}).json()["items"][0]["card_id"] == nref

        # Kartenseite: Vorschlag lesen
        chk = client.get(f"{API}/cards/{bad}/scan-check").json()
        assert chk["status"] == "low" and chk["suggested_quad"] == BETTER
        assert client.get(f"{API}/cards/{bad}").json()["bild_quad"] == QUAD

        # Unverändertes wird nicht noch einmal geprüft
        fake_matcher.clear()
        asyncio.run(scan_quality.run())
        assert bad not in fake_matcher and good not in fake_matcher

        # Zuschnitt korrigieren: neues Foto + neue Ecken, alte Prüfung verworfen
        before = client.get(f"{API}/cards/{bad}").json()
        r = client.post(f"{API}/cards/{bad}/recrop",
                        files={"file": ("crop.jpg", _jpeg((1, 2, 3)), "image/jpeg")},
                        data={"quad": json.dumps(BETTER)})
        assert r.status_code == 200, r.text
        after = r.json()
        assert after["bild_quad"] == BETTER
        assert after["bild_karte_pfad"] != before["bild_karte_pfad"]          # neuer Dateiname → neu lernen
        assert client.get(f"{API}/cards/{bad}/scan-check").json() is None
        r = client.post(f"{API}/scan-quality/{bad}/check")       # gleich neu prüfen (Liste „Schwache Scans")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "low" and r.json()["photo"] == after["bild_karte_pfad"]
        assert bad in fake_matcher
        assert client.post(f"{API}/scan-quality/999999999/check").status_code == 404

        # Als in Ordnung markieren
        assert client.post(f"{API}/scan-quality/{bad}/dismiss").status_code == 200
        assert bad not in [i["card_id"] for i in client.get(f"{API}/scan-quality").json()["items"]]

        # Export: Prüfung je Karte, ein Foto mit drei Umrissen für den Detektor
        z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/data/scan-dataset").content))
        labels = {json.loads(x)["id"]: json.loads(x) for x in z.read("labels.jsonl").decode().splitlines()}
        assert labels[bad]["check"]["status"] == "dismissed" and labels[bad]["quad"] == BETTER
        assert labels[good]["check"] == {"status": "ok", "inliers": 120, "corner_err": 0.008}
        photos = [json.loads(x) for x in z.read("photos.jsonl").decode().splitlines()]
        shared = next(p for p in photos if {c["id"] for c in p["cards"]} >= {bad, good, nref})
        assert shared["labeled"] and shared["width"] == 40 and shared["height"] == 56
        yolo = z.read(shared["original"].rsplit(".", 1)[0] + ".txt").decode().splitlines()
        assert len(yolo) == len(shared["cards"]) and all(len(line.split()) == 9 for line in yolo)
        assert "./" + shared["original"] in z.read("detect_train.txt").decode()
        assert "train: detect_train.txt" in z.read("detect.yaml").decode()
    finally:
        for cid in (bad, good, nref):
            client.delete(f"{API}/cards/{cid}/image")   # Dateien weg (IDs kommen nach einem Restore wieder)
            client.delete(f"{API}/cards/{cid}")
        _cleanup()


def test_recrop_needs_an_original(client, fake_matcher):
    cid = _card(client)
    try:
        _upload(client, cid, None)
        r = client.post(f"{API}/cards/{cid}/recrop", files={"file": ("c.jpg", _jpeg((1, 1, 1)), "image/jpeg")},
                        headers={"X-UI-Lang": "EN"})
        assert r.status_code == 409 and r.json()["detail"] == "There is no original photo to crop again"
    finally:
        client.delete(f"{API}/cards/{cid}/image")
        client.delete(f"{API}/cards/{cid}")


def test_try_crop_checks_without_saving(client, fake_matcher, monkeypatch):
    seen = {}

    async def post(files, form):
        seen.update(files=files, form=form)
        return {"templates": 1, "inliers": 88, "art_inliers": 20, "corner_err": 0.01, "geo_ok": True,
                "suggested_quad": BETTER}

    monkeypatch.setattr(scan_quality, "_post_verify", post)
    cid = _card(client, "sv03-125")
    try:
        _upload(client, cid, _jpeg((30, 60, 90)), QUAD)
        before = client.get(f"{API}/cards/{cid}").json()
        r = client.post(f"{API}/scan-quality/{cid}/try", files={"file": ("t.jpg", _jpeg((5, 5, 5)), "image/jpeg")},
                        data={"quad": json.dumps(BETTER)})
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok", "inliers": 88, "corner_err": 0.01, "suggested_quad": BETTER}
        assert seen["form"] == {"card_id": "sv03-125", "quad": json.dumps(BETTER)} and "original" in seen["files"]
        # nichts gespeichert: Foto, Ecken und Prüfung bleiben
        after = client.get(f"{API}/cards/{cid}").json()
        assert after["bild_karte_pfad"] == before["bild_karte_pfad"] and after["bild_quad"] == QUAD
        assert client.get(f"{API}/cards/{cid}/scan-check").json() is None
        bad = client.post(f"{API}/scan-quality/{cid}/try", files={"file": ("t.jpg", _jpeg((5, 5, 5)), "image/jpeg")},
                          data={"quad": "[[0,0],[1,0]]"}, headers={"X-UI-Lang": "EN"})
        assert bad.status_code == 422 and bad.json()["detail"] == "Invalid corners"
        assert client.post(f"{API}/scan-quality/999999999/try", files={"file": ("t.jpg", b"x", "image/jpeg")},
                           data={"quad": json.dumps(BETTER)}).status_code == 404

        async def down(files, form):
            return None
        monkeypatch.setattr(scan_quality, "_post_verify", down)
        assert client.post(f"{API}/scan-quality/{cid}/try", files={"file": ("t.jpg", b"x", "image/jpeg")},
                           data={"quad": json.dumps(BETTER)}).status_code == 503
    finally:
        client.delete(f"{API}/cards/{cid}/image")
        client.delete(f"{API}/cards/{cid}")


def test_scan_quality_is_admin_only(fake_matcher):
    from test_multiuser import _client_for, _drop_user, _make_user
    bob = _make_user("bob-sq")
    try:
        bc = _client_for(bob)
        assert bc.get(f"{API}/scan-quality").status_code == 403
        assert bc.post(f"{API}/scan-quality/run").status_code == 403
        assert bc.post(f"{API}/scan-quality/1/try", files={"file": ("t.jpg", b"x", "image/jpeg")},
                       data={"quad": json.dumps(BETTER)}).status_code == 403
    finally:
        _drop_user(bob)


def test_crop_suggest_for_own_cards(client, fake_matcher, monkeypatch):
    """Grobe Ecken im Eck-Editor: Vorschlag vom Bild-Abgleich, nur für eigene Karten, nichts gespeichert."""
    from test_multiuser import _client_for, _drop_user, _make_user

    async def post(files, form):
        return {"templates": 1, "inliers": 77, "art_inliers": 20, "corner_err": 0.012, "geo_ok": True,
                "suggested_quad": BETTER}

    monkeypatch.setattr(scan_quality, "_post_verify", post)
    cid = _card(client, "sv03-125")
    nocat = _card(client, None)
    bob = _make_user("bob-cs")
    try:
        _upload(client, cid, _jpeg((30, 60, 90)), QUAD)
        f = {"file": ("t.jpg", _jpeg((5, 5, 5)), "image/jpeg")}
        r = client.post(f"{API}/cards/{cid}/crop-suggest", files=f, data={"quad": json.dumps(QUAD)})
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok", "inliers": 77, "corner_err": 0.012, "suggested_quad": BETTER}
        assert client.get(f"{API}/cards/{cid}").json()["bild_quad"] == QUAD
        assert _client_for(bob).post(f"{API}/cards/{cid}/crop-suggest", files=f,
                                     data={"quad": json.dumps(QUAD)}).status_code == 404
        assert client.post(f"{API}/cards/{cid}/crop-suggest", files=f, data={"quad": "[1,2]"}).status_code == 422
        _upload(client, nocat, _jpeg((30, 60, 90)), QUAD)
        r = client.post(f"{API}/cards/{nocat}/crop-suggest", files=f, data={"quad": json.dumps(QUAD)},
                        headers={"X-UI-Lang": "EN"})
        assert r.status_code == 409 and r.json()["detail"] == "The card isn't linked to a catalog card"

        async def down(files, form):
            return None
        monkeypatch.setattr(scan_quality, "_post_verify", down)
        assert client.post(f"{API}/cards/{cid}/crop-suggest", files=f,
                           data={"quad": json.dumps(QUAD)}).status_code == 503
    finally:
        for c in (cid, nocat):
            client.delete(f"{API}/cards/{c}/image")
            client.delete(f"{API}/cards/{c}")
        _drop_user(bob)


def test_admin_fixes_crops_and_stamps_of_other_users(client, fake_matcher, monkeypatch):
    """Datensätze: der Admin korrigiert Zuschnitt und Stempel auch an fremden Karten; sonst bleibt alles getrennt."""
    from test_multiuser import _client_for, _drop_user, _make_user

    from app.models.card import PokemonCard
    from app.services import stamp_finder

    async def post(files, form):
        return {"templates": 1, "inliers": 77, "art_inliers": 20, "corner_err": 0.012, "geo_ok": True,
                "suggested_quad": BETTER}

    async def find(photo, hint):
        return {"quad": QUAD, "score": 0.9, "ok": True, "template": True}

    monkeypatch.setattr(scan_quality, "_post_verify", post)
    monkeypatch.setattr(stamp_finder, "find", find)
    bob = _make_user("bob-admin-fix")
    carol = _make_user("carol-admin-fix")
    bob_c, carol_c = _client_for(bob), _client_for(carol)
    try:
        cid = _card(bob_c, "sv03-125", prize_pack=True)
        _upload(bob_c, cid, _jpeg((30, 60, 90)), QUAD)
        f = {"file": ("t.jpg", _jpeg((5, 5, 5)), "image/jpeg")}
        stamp = {"quad": [[0.79, 0.37], [0.96, 0.37], [0.96, 0.465], [0.79, 0.465]]}
        # andere Benutzer: weiterhin nichts
        assert carol_c.post(f"{API}/cards/{cid}/crop-suggest", files=f, data={"quad": json.dumps(QUAD)}).status_code == 404
        assert carol_c.post(f"{API}/cards/{cid}/recrop", files=f, data={"quad": json.dumps(BETTER)}).status_code == 404
        assert carol_c.put(f"{API}/cards/{cid}/stamp", json=stamp).status_code == 404
        assert carol_c.post(f"{API}/cards/{cid}/stamp-suggest", json={}).status_code == 404
        # Admin: die Foto-Werkzeuge gehen …
        r = client.post(f"{API}/cards/{cid}/crop-suggest", files=f, data={"quad": json.dumps(QUAD)})
        assert r.status_code == 200, r.text
        r = client.post(f"{API}/cards/{cid}/recrop", files=f, data={"quad": json.dumps(BETTER)})
        assert r.status_code == 200, r.text
        assert r.json()["bild_quad"] == BETTER and r.json()["id"] == cid
        r = client.put(f"{API}/cards/{cid}/stamp", json=stamp)
        assert r.status_code == 200, r.text
        assert r.json()["stamp_quad"] == stamp["quad"]
        assert client.post(f"{API}/cards/{cid}/stamp-suggest", json={}).json()["ok"] is True
        # … die Karte bleibt bei Bob, mit seinen Änderungen sichtbar für ihn
        mine = bob_c.get(f"{API}/cards/{cid}").json()
        assert mine["bild_quad"] == BETTER and mine["stamp_quad"] == stamp["quad"]
        db = SessionLocal()
        try:
            assert db.get(PokemonCard, cid, execution_options={"all_users": True}).user_id == bob
        finally:
            db.close()
        # … aber sonst bleibt fremd fremd: lesen, ändern, löschen
        assert client.get(f"{API}/cards/{cid}").status_code == 404
        assert client.put(f"{API}/cards/{cid}", json={"notizen": "x"}).status_code == 404
        assert client.delete(f"{API}/cards/{cid}/image").status_code == 404
        assert bob_c.get(f"{API}/cards/{cid}").status_code == 200
    finally:
        _drop_user(bob)
        _drop_user(carol)
