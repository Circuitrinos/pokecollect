"""Fehlermeldungen auf Englisch für die englische Oberfläche (app/messages.py)."""
import re
from pathlib import Path

from app import messages

API = "/api/v1"


def test_http_error_follows_ui_language(client):
    r = client.get(f"{API}/cards/999999999")
    assert r.status_code == 404 and r.json()["detail"] == "Karte nicht gefunden"
    r = client.get(f"{API}/cards/999999999", headers={"X-UI-Lang": "EN"})
    assert r.json()["detail"] == "Card not found"
    r = client.get(f"{API}/cards/999999999", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert r.json()["detail"] == "Card not found"
    r = client.get(f"{API}/cards/999999999", headers={"Accept-Language": "en-US", "X-UI-Lang": "DE"})
    assert r.json()["detail"] == "Karte nicht gefunden"


def test_password_change_error_in_english(client):
    r = client.post(f"{API}/settings/change-password", headers={"X-UI-Lang": "EN"},
                    json={"current_password": "wrong-password", "new_password": "something-long-enough"})
    assert r.status_code == 400 and r.json()["detail"] == "Current password is wrong"


def test_validation_messages_in_english(client):
    r = client.post(f"{API}/sealed", headers={"X-UI-Lang": "EN"}, json={"name": "   "})
    assert r.status_code == 422
    assert any("Name can't be empty" in e["msg"] for e in r.json()["detail"])


def test_patterns():
    assert messages.to_english("Set 'ABC' existiert bereits") == "Set 'ABC' already exists"
    assert messages.to_english("Dateityp .svg nicht erlaubt (nur .jpg, .jpeg, .png, .webp)") \
        == "File type .svg not allowed (only .jpg, .jpeg, .png, .webp)"
    assert messages.to_english("something unknown") == "something unknown"


def test_every_fixed_detail_has_a_translation():
    """Neue deutsche Fehlermeldungen brauchen einen Eintrag in app/messages.py."""
    root = Path(messages.__file__).parent
    missing = set()
    for p in root.rglob("*.py"):
        text = p.read_text()
        for m in re.finditer(r'detail=(["\'])([^"\'{}]+)\1', text):
            if m.group(2) not in messages.EN:
                missing.add(m.group(2))
    assert not missing, missing


def test_csv_export_in_english(client):
    import csv
    import io
    made = client.post(f"{API}/cards", json={"kartenname": "CSV-Test", "englischer_name": "CSV Test",
                                              "besessen": True, "kaufpreis_eur": "12.50",
                                              "prioritaet": "Hoch"}).json()
    try:
        r = client.get(f"{API}/data/export.csv", params={"lang": "EN"})
        assert r.status_code == 200
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=","))
        header = rows[0]
        assert "card_name" in header and "purchase_price" in header and "kartenname" not in header
        row = next(x for x in rows[1:] if x[header.index("id")] == str(made["id"]))
        assert row[header.index("purchase_price")] == "12.50"
        assert row[header.index("owned")] == "yes"
        assert row[header.index("priority")] == "High"
        de = client.get(f"{API}/data/export.csv").content.decode("utf-8-sig")
        assert de.splitlines()[0].startswith("id;")
    finally:
        client.delete(f"{API}/cards/{made['id']}")
