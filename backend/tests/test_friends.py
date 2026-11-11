"""Freunde + geteilte Sammlungen: Anfrage per genauem Benutzernamen, annehmen,
nur ansehen, versteckte Felder, Entfreunden entzieht die Freigaben."""
import pytest

from tests.test_multiuser import API, _client_for, _drop_user, _make_user


@pytest.fixture()
def drei(client):
    ids = [_make_user(n) for n in ("fr-ash", "fr-misty", "fr-brock")]
    yield [(uid, _client_for(uid)) for uid in ids]
    for uid in ids:
        _drop_user(uid)


def _befreunden(a, b, name_b):
    r = a.post(f"{API}/friends/requests", json={"username": name_b})
    assert r.status_code == 201, r.text
    req = r.json()["outgoing"][0]["id"]
    assert b.post(f"{API}/friends/requests/{req}/accept").status_code == 200


def test_anfrage_annehmen_ablehnen(drei):
    (ash_id, ash), (misty_id, misty), (brock_id, brock) = drei
    assert ash.post(f"{API}/friends/requests", json={"username": "fr-nobody"}).status_code == 404
    assert ash.post(f"{API}/friends/requests", json={"username": "fr-ash"}).status_code == 422
    r = ash.post(f"{API}/friends/requests", json={"username": " FR-Misty "})   # Groß/klein, Leerzeichen egal
    assert r.status_code == 201 and [o["user"]["username"] for o in r.json()["outgoing"]] == ["fr-misty"]
    assert ash.post(f"{API}/friends/requests", json={"username": "fr-misty"}).status_code == 409
    inc = misty.get(f"{API}/friends").json()["incoming"]
    assert [i["user"]["id"] for i in inc] == [ash_id]
    # Nur der Angefragte nimmt an
    assert ash.post(f"{API}/friends/requests/{inc[0]['id']}/accept").status_code == 404
    r = misty.post(f"{API}/friends/requests/{inc[0]['id']}/accept")
    assert [f["username"] for f in r.json()["friends"]] == ["fr-ash"]
    assert [f["username"] for f in ash.get(f"{API}/friends").json()["friends"]] == ["fr-misty"]
    assert ash.post(f"{API}/friends/requests", json={"username": "fr-misty"}).status_code == 409
    # Gegenseitige Anfrage = angenommen
    brock.post(f"{API}/friends/requests", json={"username": "fr-ash"})
    r = ash.post(f"{API}/friends/requests", json={"username": "fr-brock"})
    assert sorted(f["username"] for f in r.json()["friends"]) == ["fr-brock", "fr-misty"]
    # Ablehnen / zurückziehen
    _drop = misty.post(f"{API}/friends/requests", json={"username": "fr-brock"}).json()["outgoing"][0]["id"]
    assert ash.delete(f"{API}/friends/requests/{_drop}").status_code == 404     # Fremde dürfen nicht
    assert brock.delete(f"{API}/friends/requests/{_drop}").json()["incoming"] == []
    assert misty.get(f"{API}/friends").json()["outgoing"] == []


def test_geteilte_sammlung_nur_ansehen(drei):
    (ash_id, ash), (misty_id, misty), (brock_id, brock) = drei
    _befreunden(ash, misty, "fr-misty")
    col = ash.post(f"{API}/collections", json={"name": "Ash Binder"}).json()
    card = ash.post(f"{API}/cards", json={"kartenname": "Pikachu", "besessen": True, "zustand": "Near Mint", "sprache": "EN",
                                         "wert_eur": "12.50", "kaufpreis_eur": "3.00", "notizen": "aus Kindheit"}).json()
    ash.post(f"{API}/collections/{col['id']}/cards", json={"card_id": card["id"]})
    # Nur mit Freunden teilbar
    assert ash.put(f"{API}/collections/{col['id']}/shares", json={"friend_ids": [brock_id]}).status_code == 422
    r = ash.put(f"{API}/collections/{col['id']}/shares", json={"friend_ids": [misty_id]})
    assert r.status_code == 200 and [u["id"] for u in r.json()] == [misty_id]
    assert ash.get(f"{API}/collections/{col['id']}").json()["geteilt_mit"] == 1
    # Fremde Sammlung über die Eigentümer-Routen: weiterhin unsichtbar und unveränderbar
    assert misty.get(f"{API}/collections/{col['id']}").status_code == 404
    assert misty.put(f"{API}/collections/{col['id']}/shares", json={"friend_ids": []}).status_code == 404
    assert misty.post(f"{API}/collections/{col['id']}/cards", json={"card_id": card["id"]}).status_code in (403, 404)
    # Freundin sieht sie unter /shared
    lst = misty.get(f"{API}/shared").json()
    assert [(s["id"], s["owner"]["username"], s["karten_anzahl"]) for s in lst] == [(col["id"], "fr-ash", 1)]
    d = misty.get(f"{API}/shared/{col['id']}").json()
    c = d["cards"][0]
    assert (c["kartenname"], c["zustand"], c["sprache"], c["wert_eur"]) == ("Pikachu", "Near Mint", "EN", "12.50")
    assert c["kaufpreis_eur"] is None and c["notizen"] is None and c["kaufdatum"] is None
    # Nicht geteilt / kein Freund → nichts
    assert brock.get(f"{API}/shared").json() == []
    assert brock.get(f"{API}/shared/{col['id']}").status_code == 404
    other = ash.post(f"{API}/collections", json={"name": "Privat"}).json()
    assert misty.get(f"{API}/shared/{other['id']}").status_code == 404


def test_entfreunden_entzieht_freigaben(drei):
    (ash_id, ash), (misty_id, misty), _ = drei
    _befreunden(ash, misty, "fr-misty")
    a = ash.post(f"{API}/collections", json={"name": "Von Ash"}).json()
    m = misty.post(f"{API}/collections", json={"name": "Von Misty"}).json()
    ash.put(f"{API}/collections/{a['id']}/shares", json={"friend_ids": [misty_id]})
    misty.put(f"{API}/collections/{m['id']}/shares", json={"friend_ids": [ash_id]})
    assert len(ash.get(f"{API}/shared").json()) == 1 and len(misty.get(f"{API}/shared").json()) == 1
    r = ash.delete(f"{API}/friends/{misty_id}")
    assert r.status_code == 200 and r.json()["friends"] == []
    assert ash.get(f"{API}/shared").json() == [] and misty.get(f"{API}/shared").json() == []
    assert ash.get(f"{API}/collections/{a['id']}/shares").json() == []
    # Wieder befreundet: alte Freigaben kommen nicht von selbst zurück
    _befreunden(misty, ash, "fr-ash")
    assert misty.get(f"{API}/shared").json() == []
    assert ash.delete(f"{API}/friends/999999").status_code == 404
