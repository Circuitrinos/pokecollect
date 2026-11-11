"""
Scan-Fotos als Lern- und Trainingsdaten: Ecken des Zuschnitts speichern, den
matcher nach einem Scan-Foto anstoßen, Admin-Export als ZIP (alle Benutzer,
Originale je Inhalt einmal).
"""
import io
import json
import zipfile

import pytest
from PIL import Image

from app.api.v1 import cards as cards_module
from app.services.scan import local_match

from test_multiuser import _client_for, _drop_user, _make_user

API = "/api/v1"
QUAD = [[0.1, 0.12], [0.9, 0.1], [0.92, 0.88], [0.08, 0.9]]


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 56), color=color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def pokes(monkeypatch):
    calls = []
    monkeypatch.setattr(local_match, "poke_learn", lambda url: calls.append(url))
    return calls


def _card(c, tcgdex="sv03-125", **kw):
    """Karte wie aus einem Scan: mit bestätigter TCGdex-ID (setzt nur /scan/commit)."""
    from app.database import SessionLocal
    from app.models.card import PokemonCard
    r = c.post(f"{API}/cards", json={"kartenname": "Glurak ex", "englischer_name": "Charizard ex",
                                      "besessen": True, "karten_nr": "125",
                                      "prize_pack": kw.get("prize_pack", False)})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    db = SessionLocal()
    try:
        db.get(PokemonCard, cid, execution_options={"all_users": True}).tcgdex_card_id = tcgdex
        db.commit()
    finally:
        db.close()
    return cid


def _upload(c, cid, original: bytes | None, quad=None):
    files = {"file": ("crop.jpg", _jpeg((200, 40, 40)), "image/jpeg")}
    if original is not None:
        files["original"] = ("orig.jpg", original, "image/jpeg")
    data = {"quad": json.dumps(quad)} if quad is not None else {}
    r = c.post(f"{API}/cards/{cid}/image", files=files, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def _quad_of(cid):
    from app.database import SessionLocal
    from app.models.card import PokemonCard
    db = SessionLocal()
    try:
        return db.get(PokemonCard, cid, execution_options={"all_users": True}).bild_quad
    finally:
        db.close()


def test_scanfoto_speichert_ecken_und_stoesst_matcher_an(client, pokes):
    cid = _card(client)
    try:
        _upload(client, cid, _jpeg((10, 10, 10)), QUAD)
        assert json.loads(_quad_of(cid)) == QUAD
        assert len(pokes) == 1
    finally:
        client.delete(f"{API}/cards/{cid}")


def test_foto_ohne_original_ist_kein_scanfoto(client, pokes):
    cid = _card(client)
    try:
        _upload(client, cid, None, QUAD)
        assert _quad_of(cid) is None
        assert pokes == []
    finally:
        client.delete(f"{API}/cards/{cid}")


@pytest.mark.parametrize("bad", ["nonsense", json.dumps([[0, 0], [1, 0]]), json.dumps([[0, 0], [9, 0], [1, 1], [0, 1]])])
def test_unplausible_ecken_werden_verworfen(bad):
    assert cards_module._parse_quad(bad) is None


def test_bild_loeschen_leert_ecken(client, pokes):
    cid = _card(client)
    try:
        _upload(client, cid, _jpeg((10, 10, 10)), QUAD)
        assert client.delete(f"{API}/cards/{cid}/image").status_code == 200
        assert _quad_of(cid) is None
    finally:
        client.delete(f"{API}/cards/{cid}")


def test_export_admin_alle_benutzer_originale_einmal(client, pokes):
    bob = _make_user("bob-ds")
    bc = _client_for(bob)
    shared_original = _jpeg((30, 60, 90))          # Mappenseite: zwei Karten aus EINEM Foto
    a1 = _card(client, "sv03-125")
    a2 = _card(client, "sv03-126", prize_pack=True)
    b1 = _card(bc, "base1-4")
    try:
        _upload(client, a1, shared_original, QUAD)
        _upload(client, a2, shared_original, QUAD)
        _upload(bc, b1, _jpeg((90, 60, 30)))         # ältere App: keine Ecken
        r = client.get(f"{API}/data/scan-dataset")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(r.content))
        labels = [json.loads(line) for line in z.read("labels.jsonl").decode().splitlines()]
        mine = {l["id"]: l for l in labels if l["id"] in (a1, a2, b1)}
        assert set(mine) == {a1, a2, b1}               # auch Bobs Scan
        assert mine[a1]["original"] == mine[a2]["original"] != mine[b1]["original"]
        assert mine[a2]["prize_pack"] is True and mine[a1]["card_id"] == "sv03-125"
        assert mine[a1]["quad"] == QUAD and mine[b1]["quad"] is None
        names = set(z.namelist())
        assert {mine[a1]["crop"], mine[a1]["original"], mine[b1]["original"], "README.txt"} <= names
        assert bc.get(f"{API}/data/scan-dataset").status_code == 403
    finally:
        for cid in (a1, a2):
            client.delete(f"{API}/cards/{cid}")
        _drop_user(bob)


def test_scanfoto_nur_zum_lernen_aendert_anzeige_nicht(client, pokes):
    """Erkannte Karte zeigt das Katalogbild; das Scan-Foto liegt nur für den Bild-Abgleich bereit."""
    from app.database import SessionLocal
    from app.models.card import PokemonCard
    cid = _card(client, "sv03-130")
    try:
        r = client.post(f"{API}/cards/{cid}/scan-photo",
                        files={"file": ("crop.jpg", _jpeg((5, 5, 200)), "image/jpeg"),
                               "original": ("orig.jpg", _jpeg((5, 200, 5)), "image/jpeg")},
                        data={"quad": json.dumps(QUAD)})
        assert r.status_code == 200, r.text
        assert r.json()["bild_karte_pfad"] is None          # Anzeige unverändert (Katalogbild)
        db = SessionLocal()
        try:
            card = db.get(PokemonCard, cid, execution_options={"all_users": True})
            assert card.bild_scan_pfad and card.bild_original_pfad and json.loads(card.bild_quad) == QUAD
        finally:
            db.close()
        assert len(pokes) == 1
        z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/data/scan-dataset").content))
        mine = [json.loads(x) for x in z.read("labels.jsonl").decode().splitlines() if json.loads(x)["id"] == cid]
        assert mine and mine[0]["card_id"] == "sv03-130" and mine[0]["crop"] in z.namelist()
        assert client.delete(f"{API}/cards/{cid}/image").status_code == 200
        db = SessionLocal()
        try:
            card = db.get(PokemonCard, cid, execution_options={"all_users": True})
            assert card.bild_scan_pfad is None and card.bild_original_pfad is None
        finally:
            db.close()
    finally:
        client.delete(f"{API}/cards/{cid}")


def test_scanfoto_braucht_original(client, pokes):
    cid = _card(client, "sv03-131")
    try:
        r = client.post(f"{API}/cards/{cid}/scan-photo", files={"file": ("crop.jpg", _jpeg((1, 2, 3)), "image/jpeg")})
        assert r.status_code == 422
        assert pokes == []
    finally:
        client.delete(f"{API}/cards/{cid}")


def test_nicht_exportierbare_fotos_zaehlen_und_finden(client, pokes):
    """Admin-Übersicht + Kartenfilter: Fotos ohne bestätigte Karte oder ohne Original."""
    from app.database import SessionLocal
    from app.models.card import PokemonCard
    bob = _make_user("bob-sum")
    bc = _client_for(bob)
    ok = _card(client, "sv03-140")
    ohne_original = _card(client, "sv03-141")
    ohne_karte = _card(client, "sv03-142")
    bob_ohne = _card(bc, "base1-5")
    try:
        _upload(client, ok, _jpeg((1, 2, 3)), QUAD)
        _upload(client, ohne_original, None)                     # ältere App / hochgeladenes Foto
        _upload(client, ohne_karte, _jpeg((4, 5, 6)), QUAD)
        _upload(bc, bob_ohne, None)
        db = SessionLocal()
        try:
            db.get(PokemonCard, ohne_karte, execution_options={"all_users": True}).tcgdex_card_id = None
            db.commit()
        finally:
            db.close()
        before = client.get(f"{API}/data/scan-dataset/summary")
        assert before.status_code == 200, before.text
        s = before.json()
        users = {u["username"]: u for u in s["users"]}
        assert users["bob-sum"]["not_exportable"] == 1 and users["bob-sum"]["exportable"] == 0
        assert s["not_exportable"] >= 3 and s["no_card"] >= 1 and s["no_original"] >= 2
        assert bc.get(f"{API}/data/scan-dataset/summary").status_code == 403
        # Filter in der eigenen Kartenliste
        r = client.get(f"{API}/cards", params={"bild_status": "kein_export", "limit": 5000})
        ids = {c["id"] for c in r.json()["items"]}
        assert {ohne_original, ohne_karte} <= ids and ok not in ids and bob_ohne not in ids
        # Export und Übersicht stimmen überein
        z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/data/scan-dataset").content))
        exported = {json.loads(x)["id"] for x in z.read("labels.jsonl").decode().splitlines()}
        assert ok in exported and not ({ohne_original, ohne_karte, bob_ohne} & exported)
    finally:
        for cid in (ok, ohne_original, ohne_karte):
            client.delete(f"{API}/cards/{cid}")
        _drop_user(bob)


def test_export_foil_labels_and_catalog_reference(client, pokes):
    """Folien-Muster: Folierung/Muster je Foto und das Katalogbild als Referenz (einmal je Karte)."""
    from pathlib import Path

    from app.config import settings
    from app.database import SessionLocal
    from app.models.tcgdex_catalog import TcgdexCatalog
    from app.services.catalog_images import catalog_image_disk_path

    cid_cat = "tfoil-001"
    ref = catalog_image_disk_path(cid_cat, ".jpg")
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(_jpeg((1, 2, 3)))
    db = SessionLocal()
    db.add(TcgdexCatalog(card_id=cid_cat, region="west", set_id="tfoil", name="Foilmon", name_en="Foilmon",
                         image_url="https://assets.tcgdex.net/en/sv/tfoil/001/high.webp",
                         image_pfad=str(ref.relative_to(Path(settings.images_dir).parent))))
    db.commit()
    db.close()
    rev = _card(client, cid_cat)
    plain = _card(client, cid_cat)
    try:
        client.put(f"{API}/cards/{rev}", json={"folierung": "Reverse Holo", "muster": "Pokéball", "seltenheit": "Common"})
        _upload(client, rev, _jpeg((10, 20, 30)), QUAD)
        _upload(client, plain, _jpeg((11, 21, 31)), QUAD)
        z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/data/scan-dataset").content))
        labels = {l["id"]: l for l in map(json.loads, z.read("labels.jsonl").decode().splitlines())}
        a, b = labels[rev], labels[plain]
        assert (a["foiling"], a["pattern"], a["rarity"], a["first_edition"]) == ("Reverse Holo", "Pokéball", "Common", False)
        assert b["pattern"] is None
        assert a["reference"] == b["reference"] == "references/tfoil-001.jpg"
        assert z.namelist().count("references/tfoil-001.jpg") == 1
        assert a["reference_url"] == "https://assets.tcgdex.net/en/sv/tfoil/001/high.webp"
        assert "matcher/tools/holo_diff.py" in z.read("README.txt").decode()
    finally:
        for cid in (rev, plain):
            client.delete(f"{API}/cards/{cid}/image")
            client.delete(f"{API}/cards/{cid}")
        db = SessionLocal()
        db.query(TcgdexCatalog).filter(TcgdexCatalog.card_id == cid_cat).delete()
        db.commit()
        db.close()
        ref.unlink(missing_ok=True)


def test_play_stamp_marking_and_export(client, pokes):
    """Play!-Stempel markieren: nur mit eigenem Foto, Liste im Admin-Werkzeug, YOLO-Boxen im Export."""
    stamped = _card(client, "sv03-125", prize_pack=True)
    plain = _card(client, "sv03-126")
    open_one = _card(client, "sv03-127", prize_pack=True)
    box = [[0.1, 0.4], [0.3, 0.4], [0.3, 0.5], [0.1, 0.5]]
    try:
        assert client.put(f"{API}/cards/{stamped}/stamp", json={"quad": box}).status_code == 409   # noch kein Foto
        for cid in (stamped, plain, open_one):
            _upload(client, cid, _jpeg((20, 30, 40)), QUAD)
        lst = client.get(f"{API}/scan-quality/stamps").json()
        assert {stamped, open_one} <= {i["card_id"] for i in lst["items"]} and plain not in {i["card_id"] for i in lst["items"]}
        r = client.put(f"{API}/cards/{stamped}/stamp", json={"quad": box})
        assert r.status_code == 200, r.text
        assert r.json()["stamp_quad"] == box
        bad = client.put(f"{API}/cards/{stamped}/stamp", json={"quad": [[0, 0], [1, 0], [1, 1]]}, headers={"X-UI-Lang": "EN"})
        assert bad.status_code == 422 and "Expected 4 corners" in bad.json()["detail"][0]["msg"]
        assert client.put(f"{API}/cards/{stamped}/stamp", json={"quad": [[2, 0], [1, 0], [1, 1], [0, 1]]}).status_code == 422
        marked = client.get(f"{API}/scan-quality/stamps", params={"marked": True}).json()
        assert stamped in {i["card_id"] for i in marked["items"]} and marked["counts"]["marked"] >= 1
        # Neu zuschneiden lässt die Markierung stehen (Kartenkoordinaten)
        assert client.post(f"{API}/cards/{stamped}/recrop", files={"file": ("c.jpg", _jpeg((1, 2, 3)), "image/jpeg")},
                           data={"quad": json.dumps(QUAD)}).json()["stamp_quad"] == box
        z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/data/scan-dataset").content))
        labels = {l["id"]: l for l in map(json.loads, z.read("labels.jsonl").decode().splitlines())}
        assert labels[stamped]["stamp_quad"] == box and labels[open_one]["stamp_quad"] is None
        assert z.read(f"crops/{stamped}.txt").decode() == "0 0.200000 0.450000 0.200000 0.100000\n"
        assert z.read(f"crops/{plain}.txt").decode() == ""                 # Gegenbeispiel
        assert f"crops/{open_one}.txt" not in z.namelist()                  # Prize Pack, nicht markiert
        train = z.read("stamp_train.txt").decode().splitlines()
        assert f"./crops/{stamped}.jpg" in train and f"./crops/{plain}.jpg" in train
        assert f"./crops/{open_one}.jpg" not in train
        assert "0: play_stamp" in z.read("stamp.yaml").decode()
        # Markierung entfernen, Bild löschen räumt sie ebenfalls
        assert client.put(f"{API}/cards/{stamped}/stamp", json={"quad": None}).json()["stamp_quad"] is None
        client.put(f"{API}/cards/{stamped}/stamp", json={"quad": box})
        client.delete(f"{API}/cards/{stamped}/image")
        assert client.get(f"{API}/cards/{stamped}").json()["stamp_quad"] is None
    finally:
        for cid in (stamped, plain, open_one):
            client.delete(f"{API}/cards/{cid}/image")
            client.delete(f"{API}/cards/{cid}")
