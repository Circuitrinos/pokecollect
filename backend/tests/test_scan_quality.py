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


def test_scan_quality_is_admin_only(fake_matcher):
    from test_multiuser import _client_for, _drop_user, _make_user
    bob = _make_user("bob-sq")
    try:
        bc = _client_for(bob)
        assert bc.get(f"{API}/scan-quality").status_code == 403
        assert bc.post(f"{API}/scan-quality/run").status_code == 403
    finally:
        _drop_user(bob)
