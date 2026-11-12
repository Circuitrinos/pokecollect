"""Scan-Entwürfe: Prüfseite speichern, an einem anderen Gerät fortsetzen, nichts doppelt anlegen."""
import io
import json
import zipfile
from datetime import datetime, timedelta

from sqlalchemy import update

from app.database import SessionLocal
from app.models.scan_draft import ScanDraft
from app.services import scan_drafts

from test_scan_dataset import _jpeg

API = "/api/v1"


def _state(*keys, target="pokedex"):
    return {"v": 1, "target": target, "mode": "multi",
            "candidates": [{"key": k, "suggested": {"kartenname": f"Karte {k}"}, "raw": {"name": k}} for k in keys]}


def _create(c, state):
    r = c.post(f"{API}/scan/drafts", files={"photo": ("p.jpg", _jpeg((40, 80, 120)), "image/jpeg")},
               data={"state": json.dumps(state)})
    assert r.status_code == 200, r.text
    return r.json()


def test_draft_roundtrip_and_privacy(client):
    from test_multiuser import _client_for, _drop_user, _make_user

    bob = _make_user("bob-drafts")
    bc = _client_for(bob)
    d = _create(client, _state("a", "b", "c"))
    try:
        assert d["summary"] == {"open": 3, "saved": 0, "names": ["Karte a", "Karte b", "Karte c"],
                                "target": "pokedex", "collection_id": None, "deck_id": None, "mode": "multi",
                                "outlines": []}
        assert d["id"] in [x["id"] for x in client.get(f"{API}/scan/drafts").json()]
        got = client.get(f"{API}/scan/drafts/{d['id']}").json()
        assert [c["key"] for c in got["state"]["candidates"]] == ["a", "b", "c"]
        photo = client.get(f"{API}/scan/drafts/{d['id']}/photo")
        assert photo.status_code == 200 and photo.content == _jpeg((40, 80, 120))
        thumb = client.get(f"{API}/scan/drafts/{d['id']}/thumb")
        assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
        # nur der Besitzer
        assert bc.get(f"{API}/scan/drafts").json() == []
        for path in ("", "/photo", "/thumb"):
            assert bc.get(f"{API}/scan/drafts/{d['id']}{path}").status_code == 404
        assert bc.put(f"{API}/scan/drafts/{d['id']}", json={"state": _state("x")}).status_code == 404
        assert bc.delete(f"{API}/scan/drafts/{d['id']}").status_code == 404
        # ungültiger Zustand
        bad = client.post(f"{API}/scan/drafts", files={"photo": ("p.jpg", _jpeg((1, 1, 1)), "image/jpeg")},
                          data={"state": json.dumps({"candidates": [{"no_key": 1}]})}, headers={"X-UI-Lang": "EN"})
        assert bad.status_code == 422 and bad.json()["detail"] == "Draft without a card list"
        assert client.post(f"{API}/scan/drafts", files={"photo": ("p.jpg", b"", "image/jpeg")},
                           data={"state": json.dumps(_state("a"))}).status_code == 400
    finally:
        client.delete(f"{API}/scan/drafts/{d['id']}")
        _drop_user(bob)
    assert client.get(f"{API}/scan/drafts/{d['id']}").status_code == 404


def test_saved_cards_stay_saved_and_are_never_committed_twice(client):
    d = _create(client, _state("a", "b"))
    did = d["id"]
    items = lambda key: [{"kartenname": f"Entwurf {key}", "englischer_name": f"Draft {key}", "draft_key": key}]  # noqa: E731
    try:
        r = client.post(f"{API}/scan/commit", json={"target": "pokedex", "draft_id": did, "items": items("a")})
        assert r.status_code == 200, r.text
        card_a = r.json()["card_ids"][0]
        st = client.get(f"{API}/scan/drafts/{did}").json()
        a = st["state"]["candidates"][0]
        assert a["saved"] == {"ids": [card_a], "name": "Entwurf a", "name_en": "Draft a"}
        assert st["summary"]["open"] == 1 and st["summary"]["saved"] == 1
        # das andere Gerät weiß davon noch nichts: sein Abgleich macht „a" nicht wieder offen …
        r = client.put(f"{API}/scan/drafts/{did}", json={"state": _state("a", "b")})
        assert r.status_code == 200 and r.json()["saved"] == {"a": a["saved"]}
        assert client.get(f"{API}/scan/drafts/{did}").json()["state"]["candidates"][0]["saved"] == a["saved"]
        # … und „a" noch einmal zu speichern wird abgelehnt (nichts angelegt)
        n_before = len(client.get(f"{API}/cards", params={"search": "Entwurf a"}).json().get("items", []))
        dup = client.post(f"{API}/scan/commit", json={"target": "pokedex", "draft_id": did, "items": items("a")},
                          headers={"X-UI-Lang": "EN"})
        assert dup.status_code == 409 and dup.json()["detail"] == "Already saved — probably on another device"
        assert len(client.get(f"{API}/cards", params={"search": "Entwurf a"}).json().get("items", [])) == n_before
        # „b" geht; ohne Entwurf (gelöscht/abgelaufen) wird trotzdem gespeichert
        r = client.post(f"{API}/scan/commit", json={"target": "pokedex", "draft_id": did, "items": items("b")})
        assert r.status_code == 200
        card_b = r.json()["card_ids"][0]
        assert client.get(f"{API}/scan/drafts/{did}").json()["summary"]["open"] == 0
        client.delete(f"{API}/scan/drafts/{did}")
        r = client.post(f"{API}/scan/commit", json={"target": "pokedex", "draft_id": did, "items": items("c")})
        assert r.status_code == 200
        card_c = r.json()["card_ids"][0]
    finally:
        client.delete(f"{API}/scan/drafts/{did}")
        for cid in (locals().get("card_a"), locals().get("card_b"), locals().get("card_c")):
            if cid:
                client.delete(f"{API}/cards/{cid}")


def test_old_and_surplus_drafts_are_pruned(client, monkeypatch):
    monkeypatch.setattr(scan_drafts, "MAX_PER_USER", 3)
    ids = [_create(client, _state(str(i)))["id"] for i in range(4)]
    try:
        mine = [x["id"] for x in client.get(f"{API}/scan/drafts").json()]
        assert ids[0] not in mine and set(ids[1:]) <= set(mine)
        db = SessionLocal()
        try:
            db.execute(update(ScanDraft).where(ScanDraft.id == ids[1])
                       .values(aktualisiert_am=datetime.utcnow() - timedelta(days=scan_drafts.KEEP_DAYS + 1)))
            db.commit()
            assert scan_drafts.prune_all(db) >= 1
        finally:
            db.close()
        mine = [x["id"] for x in client.get(f"{API}/scan/drafts").json()]
        assert ids[1] not in mine and ids[2] in mine
    finally:
        for i in ids:
            client.delete(f"{API}/scan/drafts/{i}")


def test_drafts_are_left_out_of_backups(client):
    """Fotos als bytea gingen nicht ins JSON-Backup — Entwürfe sind ohnehin nur Zwischenstände."""
    d = _create(client, _state("a"))
    try:
        r = client.get(f"{API}/data/backup")
        assert r.status_code == 200, r.text
        tables = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("data.json"))["tables"]
        assert "scan_drafts" not in tables and "pokemon_cards" in tables
    finally:
        client.delete(f"{API}/scan/drafts/{d['id']}")


def test_summary_names_the_destination():
    st = {**_state("a"), "target": "collection", "collectionId": 7, "deckId": 3}
    assert scan_drafts.summary(st)["collection_id"] == 7 and scan_drafts.summary(st)["deck_id"] is None
    st = {**_state("a"), "target": "deck", "collectionId": 7, "deckId": 3}
    assert scan_drafts.summary(st)["deck_id"] == 3 and scan_drafts.summary(st)["collection_id"] is None
    st = {**_state("a"), "target": "collection", "collectionId": "7"}
    assert scan_drafts.summary(st)["collection_id"] is None


def test_summary_outlines_the_cards_in_the_photo():
    """Fürs ganze Foto auf „Offene Scans": wo jede Karte liegt und ob sie schon gespeichert ist."""
    q = [[0.1, 0.1], [0.4, 0.1], [0.4, 0.5], [0.1, 0.5]]
    st = {"candidates": [
        {"key": "a", "raw": {"quad": q}},
        {"key": "b", "raw": {"quad": q}, "editedQuad": [[0.5, 0.1], [0.9, 0.1], [0.9, 0.5], [0.5, 0.5]],
         "saved": {"ids": [1], "name": "B"}},
        {"key": "c", "raw": {"bbox": [0.2, 0.6, 0.3, 0.35]}},
        {"key": "d", "raw": {"quad": q}, "variantOf": "a"},       # weiteres Exemplar: keine eigene Stelle
        {"key": "e", "raw": {"quad": "kaputt"}},
    ]}
    out = scan_drafts.summary(st)["outlines"]
    assert out == [
        {"quad": q, "saved": False},
        {"quad": [[0.5, 0.1], [0.9, 0.1], [0.9, 0.5], [0.5, 0.5]], "saved": True},
        {"quad": [[0.2, 0.6], [0.5, 0.6], [0.5, 0.95], [0.2, 0.95]], "saved": False},
    ]
