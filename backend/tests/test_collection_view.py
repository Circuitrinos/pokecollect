"""Ansicht einer Sammlung (Raster/Binder) wird serverseitig gemerkt – gilt auf allen Geräten."""
API = "/api/v1"


def test_ansicht_wird_gemerkt(client):
    col = client.post(f"{API}/collections", json={"name": "Ansicht-Test"}).json()
    try:
        assert col["view_mode"] == "grid"                                  # Standard
        r = client.put(f"{API}/collections/{col['id']}", json={"view_mode": "binder"})
        assert r.status_code == 200, r.text
        assert client.get(f"{API}/collections/{col['id']}").json()["view_mode"] == "binder"
        assert next(c for c in client.get(f"{API}/collections").json() if c["id"] == col["id"])["view_mode"] == "binder"
        # andere Felder bleiben unberührt
        assert client.get(f"{API}/collections/{col['id']}").json()["binder_layout"] == col["binder_layout"]
        assert client.put(f"{API}/collections/{col['id']}", json={"view_mode": "liste"}).status_code == 422
        client.put(f"{API}/collections/{col['id']}", json={"view_mode": "grid"})
        assert client.get(f"{API}/collections/{col['id']}").json()["view_mode"] == "grid"
    finally:
        client.delete(f"{API}/collections/{col['id']}")


def test_sortierung_wird_gemerkt(client):
    col = client.post(f"{API}/collections", json={"name": "Sortier-Test"}).json()
    try:
        assert col["grid_sort"] == "manual"
        for s in ("name", "pokedex", "set", "value", "regulation", "newest", "manual"):
            assert client.put(f"{API}/collections/{col['id']}", json={"grid_sort": s}).status_code == 200
            assert client.get(f"{API}/collections/{col['id']}").json()["grid_sort"] == s
        assert client.put(f"{API}/collections/{col['id']}", json={"grid_sort": "random"}).status_code == 422
        assert col["grid_sort_reverse"] is False
        assert client.put(f"{API}/collections/{col['id']}", json={"grid_sort_reverse": True}).status_code == 200
        assert client.get(f"{API}/collections/{col['id']}").json()["grid_sort_reverse"] is True
        assert col["grid_sort_then"] == ""
        assert client.put(f"{API}/collections/{col['id']}", json={"grid_sort_then": "name,-set"}).status_code == 200
        assert client.get(f"{API}/collections/{col['id']}").json()["grid_sort_then"] == "name,-set"
        assert client.put(f"{API}/collections/{col['id']}", json={"grid_sort_then": "manual"}).status_code == 422
        client.put(f"{API}/collections/{col['id']}", json={"grid_sort": "value"})
        assert client.get(f"{API}/collections/{col['id']}").json()["view_mode"] == "grid"   # unabhängig von der Ansicht
    finally:
        client.delete(f"{API}/collections/{col['id']}")
