"""Scan-Bestätigung: 1st Edition und Zustand je Karte landen an der angelegten Karte."""

API = "/api/v1"


def test_scan_commit_stores_first_edition_and_condition(client):
    r = client.post(f"{API}/scan/commit", json={"target": "pokedex", "items": [
        {"kartenname": "FE-Glurak", "karten_nr": "4/102", "erste_edition": True, "zustand": "Near Mint"},
        {"kartenname": "FE-Glurak", "karten_nr": "4/102", "zustand": ""},
    ]})
    assert r.status_code == 200, r.text
    first, plain = r.json()["card_ids"]
    try:
        a, b = client.get(f"{API}/cards/{first}").json(), client.get(f"{API}/cards/{plain}").json()
        assert (a["erste_edition"], a["zustand"]) == (True, "Near Mint")
        assert (b["erste_edition"], b["zustand"]) == (False, None)
        bad = client.post(f"{API}/scan/commit", json={"target": "pokedex", "items": [
            {"kartenname": "FE-Glurak", "zustand": "Like new"}]})
        assert bad.status_code == 422
        wish = client.post(f"{API}/scan/commit", json={"target": "wishlist", "items": [
            {"kartenname": "FE-Glurak", "erste_edition": True}]})
        assert wish.status_code == 200
        w = wish.json()["card_ids"][0]
        assert client.get(f"{API}/cards/{w}").json()["erste_edition"] is True
        client.delete(f"{API}/cards/{w}")
    finally:
        for cid in (first, plain):
            client.delete(f"{API}/cards/{cid}")
