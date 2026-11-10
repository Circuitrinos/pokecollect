"""Play!-Pokémon-Stempel (Prize Pack): im Scan angehakt, editierbar, filterbar."""

API = "/api/v1"


def test_scan_commit_stores_prize_pack_and_card_can_be_edited_and_filtered(client):
    r = client.post(f"{API}/scan/commit", json={"target": "pokedex", "items": [
        {"kartenname": "PP-Gengar VMAX", "karten_nr": "157", "prize_pack": True},
        {"kartenname": "PP-Gengar VMAX", "karten_nr": "157"},
    ]})
    assert r.status_code == 200, r.text
    stamped, plain = r.json()["card_ids"]
    assert stamped != plain, "Prize-Pack- und normale Karte bleiben getrennte Einträge"
    assert client.get(f"{API}/cards/{stamped}").json()["prize_pack"] is True
    assert client.get(f"{API}/cards/{plain}").json()["prize_pack"] is False

    only = client.get(f"{API}/cards", params={"prize_pack": "true", "search": "PP-Gengar", "limit": 50}).json()
    assert [c["id"] for c in only["items"]] == [stamped]

    assert client.put(f"{API}/cards/{plain}", json={"prize_pack": True}).json()["prize_pack"] is True
    assert client.put(f"{API}/cards/{plain}", json={"prize_pack": None}).status_code == 422
    assert client.put(f"{API}/cards/{plain}", json={"prize_pack": False}).json()["prize_pack"] is False

    created = client.post(f"{API}/cards", json={"kartenname": "PP-Manuell", "besessen": True, "prize_pack": True}).json()
    assert created["prize_pack"] is True

    for cid in (stamped, plain, created["id"]):
        client.delete(f"{API}/cards/{cid}")
