"""
Mehrbenutzer: getrennte Sammlungen + Admin-Grenzen + SSO-Login (OIDC).

Zwei echte Benutzer gegen dieselbe Datenbank: alles, was einer anlegt, ist für
den anderen unsichtbar und unveränderbar — über JEDEN Lese-/Schreibpfad, den
die Fach-Router anbieten. Der OIDC-Ablauf läuft gegen einen Provider-Stub
(httpx.MockTransport) mit echt signierten ID-Tokens (RS256 und HS256).
"""

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth_user import issue_token
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models.card import PokemonCard
from app.models.tcgdex_catalog import TcgdexCatalog
from app.models.user import User
from app.services import oidc, pokedex_fill

API = "/api/v1"


def _make_user(name: str, admin: bool = False) -> int:
    db = SessionLocal()
    try:
        old = db.scalar(select(User).where(User.username == name))
        if old is not None:
            db.delete(old)          # CASCADE: Karten/Sammlungen/Sealed eines Vorlaufs
            db.commit()
        u = User(username=name, is_admin=admin, is_active=True)
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _drop_user(uid: int):
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        if u is not None:
            db.delete(u)
            db.commit()
    finally:
        db.close()


def _client_for(uid: int) -> TestClient:
    db = SessionLocal()
    try:
        token, _ = issue_token(db.get(User, uid))
    finally:
        db.close()
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.fixture()
def bob(client):
    uid = _make_user("bob-mu")
    yield uid, _client_for(uid)
    _drop_user(uid)


# ── Getrennte Bestände ───────────────────────────────────────────────────────

def test_collections_are_separate(client, bob):
    bob_id, bc = bob
    # Bob legt Karte, Sammlung, Sealed an
    card = bc.post(f"{API}/cards", json={"kartenname": "Bob-Pikachu", "pokedex_nr": 25, "besessen": True,
                                         "kaufpreis_eur": "5.00", "wert_eur": "7.00"}).json()
    coll = bc.post(f"{API}/collections", json={"name": "Bobs Binder"}).json()
    assert bc.post(f"{API}/collections/{coll['id']}/cards", json={"card_id": card["id"]}).status_code == 201
    sealed = bc.post(f"{API}/sealed", json={"name": "Bobs ETB"}).json()

    # Admin sieht nichts davon …
    items = client.get(f"{API}/cards", params={"limit": 500, "search": "Bob-Pikachu"}).json()["items"]
    assert items == []
    assert client.get(f"{API}/cards/{card['id']}").status_code == 404
    assert client.get(f"{API}/cards/{card['id']}/collections").status_code == 404
    assert client.get(f"{API}/prices/history/{card['id']}").status_code == 404
    assert all(c["id"] != coll["id"] for c in client.get(f"{API}/collections").json())
    assert client.get(f"{API}/collections/{coll['id']}").status_code == 404
    assert client.get(f"{API}/collections/{coll['id']}/cards").status_code == 404
    assert all(s["id"] != sealed["id"] for s in client.get(f"{API}/sealed").json())
    assert client.get(f"{API}/sealed/{sealed['id']}").status_code == 404
    csv = client.get(f"{API}/data/export.csv").content.decode("utf-8-sig")
    assert "Bob-Pikachu" not in csv

    # … und kann nichts davon ändern oder löschen
    assert client.put(f"{API}/cards/{card['id']}", json={"notizen": "x"}).status_code == 404
    assert client.delete(f"{API}/cards/{card['id']}").status_code == 404
    assert client.put(f"{API}/collections/{coll['id']}", json={"name": "gekapert"}).status_code == 404
    assert client.delete(f"{API}/collections/{coll['id']}").status_code == 404
    assert client.put(f"{API}/sealed/{sealed['id']}", json={"name": "gekapert"}).status_code == 404
    assert client.delete(f"{API}/sealed/{sealed['id']}").status_code == 404

    # Admin kann Bobs Karte nicht in die eigene Sammlung ziehen und umgekehrt
    mine = client.post(f"{API}/collections", json={"name": "Admin-Binder MU"}).json()
    assert client.post(f"{API}/collections/{mine['id']}/cards", json={"card_id": card["id"]}).status_code == 404
    admin_card = client.post(f"{API}/cards", json={"kartenname": "Admin-Glurak", "pokedex_nr": 6, "besessen": True}).json()
    assert bc.post(f"{API}/collections/{coll['id']}/cards", json={"card_id": admin_card["id"]}).status_code == 404
    assert bc.post(f"{API}/collections/{mine['id']}/cards", json={"card_id": card["id"]}).status_code == 404

    # Bob sieht weiterhin alles von sich, nichts vom Admin
    bob_items = bc.get(f"{API}/cards", params={"limit": 500}).json()["items"]
    assert {c["kartenname"] for c in bob_items if c["besessen"]} == {"Bob-Pikachu"}
    assert [c["id"] for c in bc.get(f"{API}/collections/{coll['id']}/cards").json()] == [card["id"]]
    bob_csv = bc.get(f"{API}/data/export.csv").content.decode("utf-8-sig")
    assert "Bob-Pikachu" in bob_csv and "Admin-Glurak" not in bob_csv

    # Statistik getrennt
    st = bc.get(f"{API}/cards/meta/stats").json()
    assert st["besessen"] == 1 and st["sealed_anzahl"] == 1

    client.delete(f"{API}/cards/{admin_card['id']}")
    client.delete(f"{API}/collections/{mine['id']}")


def test_pokedex_rules_are_per_user(client, bob):
    _, bc = bob
    # Pokédex-Exklusivität (1 Vertreter je Nr.) gilt je Benutzer
    a = client.post(f"{API}/cards", json={"kartenname": "MU-Bisasam A", "pokedex_nr": 1, "besessen": True}).json()
    client.put(f"{API}/cards/{a['id']}", json={"im_pokedex": True})
    b = bc.post(f"{API}/cards", json={"kartenname": "MU-Bisasam B", "pokedex_nr": 1, "besessen": True}).json()
    bc.put(f"{API}/cards/{b['id']}", json={"im_pokedex": True})
    assert client.get(f"{API}/cards/{a['id']}").json()["im_pokedex"] is True
    assert bc.get(f"{API}/cards/{b['id']}").json()["im_pokedex"] is True

    # Löschen der letzten Karte einer Nr. hinterlässt einen Platzhalter — beim
    # Löschenden, nicht beim anderen
    def placeholders(c, nr):
        items = c.get(f"{API}/cards", params={"limit": 500, "pokedex_nr": nr}).json()["items"]
        return [i for i in items if not i["besessen"]]

    before_admin = len(placeholders(client, 1))
    assert bc.delete(f"{API}/cards/{b['id']}").status_code == 204
    assert len(placeholders(bc, 1)) == 1
    assert len(placeholders(client, 1)) == before_admin
    client.delete(f"{API}/cards/{a['id']}")


def test_fill_pokedex_is_per_user(client, bob, monkeypatch):
    _, bc = bob

    async def fake_names():
        return {nr: (f"De{nr}", f"En{nr}") for nr in range(1, 1026)}

    monkeypatch.setattr(pokedex_fill, "fetch_species_names", fake_names)
    r = bc.post(f"{API}/cards/meta/fill-pokedex").json()
    assert r["created"] == 1025 and r["existing"] == 0
    assert bc.post(f"{API}/cards/meta/fill-pokedex").json()["created"] == 0


def test_catalog_owned_flag_is_per_user(client, bob):
    _, bc = bob
    db = SessionLocal()
    try:
        if not db.get(TcgdexCatalog, "mu-001"):
            db.add(TcgdexCatalog(card_id="mu-001", region="west", set_code="MUX", set_name="Multi",
                                 local_id="001", name="Mehrbenutzer-Mew", enriched=False))
            db.commit()
    finally:
        db.close()
    card = bc.post(f"{API}/cards", json={"kartenname": "Mehrbenutzer-Mew", "besessen": True}).json()
    db = SessionLocal()
    try:   # tcgdex_card_id setzt sonst erst der (in Tests abgeklemmte) Bild-Abruf
        db.get(PokemonCard, card["id"]).tcgdex_card_id = "mu-001"
        db.commit()
    finally:
        db.close()

    def owned(c):
        items = c.get(f"{API}/catalog", params={"q": "Mehrbenutzer-Mew"}).json()["items"]
        return [i["owned"] for i in items if i["card_id"] == "mu-001"]

    try:
        assert owned(bc) == [True]
        assert owned(client) == [False]
    finally:   # andere Tests zählen Katalogzeilen (Enrichment) — Spuren entfernen
        bc.delete(f"{API}/cards/{card['id']}")
        db = SessionLocal()
        try:
            db.delete(db.get(TcgdexCatalog, "mu-001"))
            db.commit()
        finally:
            db.close()


# ── Persönliche vs. Instanz-Einstellungen ────────────────────────────────────

def test_settings_split(client, bob):
    _, bc = bob
    base = client.get(f"{API}/settings").json()["cards_per_page"]
    assert bc.put(f"{API}/settings", json={"cards_per_page": 200, "default_condition": ""}).status_code == 200
    assert bc.get(f"{API}/settings").json()["cards_per_page"] == 200
    assert client.get(f"{API}/settings").json()["cards_per_page"] == base
    # Instanz-Einstellungen nur für Admins
    assert bc.put(f"{API}/settings", json={"gemini_api_key": "AIzaGEKAPERT"}).status_code == 403
    assert bc.put(f"{API}/settings", json={"price_update_hour": 5}).status_code == 403
    assert bc.post(f"{API}/settings/change-password",
                   json={"current_password": "x", "new_password": "yyyyyyyy"}).status_code == 403


def test_admin_only_endpoints(client, bob):
    bob_id, bc = bob
    assert bc.post(f"{API}/catalog/sync").status_code == 403
    assert bc.get(f"{API}/data/backup").status_code == 403
    assert bc.post(f"{API}/data/restore", data={"confirm": "JA_WIRKLICH"},
                   files={"file": ("x.zip", b"PK", "application/zip")}).status_code == 403
    assert bc.get(f"{API}/users").status_code == 403
    assert bc.patch(f"{API}/users/{bob_id}", json={"is_admin": True}).status_code == 403

    rows = client.get(f"{API}/users").json()
    assert any(r["username"] == "bob-mu" and not r["is_admin"] for r in rows)
    me = client.get(f"{API}/auth/me").json()
    assert me["is_admin"] is True
    # sich selbst nicht entmachten/aussperren
    assert client.patch(f"{API}/users/{me['id']}", json={"is_admin": False}).status_code == 400
    assert client.patch(f"{API}/users/{me['id']}", json={"is_active": False}).status_code == 400


def test_deactivated_user_is_locked_out(client, bob):
    bob_id, bc = bob
    assert bc.get(f"{API}/cards").status_code == 200
    assert client.patch(f"{API}/users/{bob_id}", json={"is_active": False}).status_code == 200
    r = bc.get(f"{API}/cards")
    assert r.status_code == 401 and r.json()["detail"] == "Benutzer deaktiviert"
    client.patch(f"{API}/users/{bob_id}", json={"is_active": True})
    assert bc.get(f"{API}/cards").status_code == 200


def test_legacy_single_user_token_still_works(client):
    """Tokens aus der Einzelbenutzer-Zeit (sub = APP_USERNAME, keine uid)."""
    token = jwt.encode({"sub": "admin", "exp": int(time.time()) + 60}, settings.jwt_secret, algorithm="HS256")
    r = TestClient(app).get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["username"] == "admin"


def test_token_for_deleted_user_is_rejected(client):
    uid = _make_user("ghost-mu")
    gc = _client_for(uid)
    _drop_user(uid)
    assert gc.get(f"{API}/cards").status_code == 401


# ── SSO (OIDC) gegen einen Provider-Stub ─────────────────────────────────────

ISSUER = "https://auth.example.test/application/o/pokecollect/"
CLIENT_ID = "pc-client"
CLIENT_SECRET = "pc-secret-0123456789"


class FakeProvider:
    def __init__(self, alg="RS256"):
        self.alg = alg
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.codes: dict[str, dict] = {}
        self.claims = {}
        self.last_token_request = None

    def jwk(self):
        pub = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**pub, "kid": "k1", "use": "sig", "alg": "RS256"}

    def id_token(self, nonce):
        now = int(time.time())
        payload = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 300, "nonce": nonce, **self.claims}
        if self.alg == "HS256":
            return jwt.encode(payload, CLIENT_SECRET, algorithm="HS256")
        return jwt.encode(payload, self.key, algorithm="RS256", headers={"kid": "k1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == ISSUER + ".well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": "https://auth.example.test/application/o/authorize/",
                "token_endpoint": "https://auth.example.test/application/o/token/",
                "userinfo_endpoint": "https://auth.example.test/application/o/userinfo/",
                "jwks_uri": ISSUER + "jwks/",
            })
        if url == ISSUER + "jwks/":
            return httpx.Response(200, json={"keys": [self.jwk()]})
        if url.endswith("/token/"):
            form = parse_qs(request.content.decode())
            self.last_token_request = form
            code = form.get("code", [""])[0]
            entry = self.codes.pop(code, None)
            if entry is None or form.get("client_secret") != [CLIENT_SECRET]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(form["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
            if challenge != entry["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE"})
            return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer",
                                             "id_token": self.id_token(entry["nonce"])})
        if url.endswith("/userinfo/"):
            return httpx.Response(200, json={"sub": self.claims.get("sub"), "groups": ["pokefans"]})
        return httpx.Response(404)


@pytest.fixture()
def provider(client, monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "oidc_client_secret", CLIENT_SECRET)
    monkeypatch.setattr(settings, "public_url", "")
    monkeypatch.setattr(settings, "oidc_admin_users", "")
    monkeypatch.setattr(settings, "oidc_admin_group", "")
    monkeypatch.setattr(oidc, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(p.handler)))

    async def fake_names():
        return {nr: (f"De{nr}", f"En{nr}") for nr in range(1, 1026)}

    monkeypatch.setattr(pokedex_fill, "fetch_species_names", fake_names)
    oidc.reset_caches()
    # Ausgangslage: Primär-Admin noch nicht per SSO verknüpft
    db = SessionLocal()
    try:
        for u in db.scalars(select(User).where(User.oidc_sub.isnot(None))).all():
            u.oidc_sub = None
        db.commit()
    finally:
        db.close()
    yield p
    oidc.reset_caches()
    db = SessionLocal()
    try:
        for u in db.scalars(select(User)).all():
            if u.username.startswith("sso-"):
                db.delete(u)
            else:
                u.oidc_sub = None
        db.commit()
    finally:
        db.close()


def _sso_login(provider: FakeProvider, claims: dict, *, origin: str = "", tamper_state=False):
    """Kompletter Browser-Ablauf; → (Callback-Location, TestClient)."""
    provider.claims = claims
    c = TestClient(app, base_url="https://tcg.example.test")
    params = {"next": "/collections"}
    if origin:
        params["origin"] = origin
    r = c.get(f"{API}/auth/oidc/login", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    auth = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(auth.query).items()}
    assert q["redirect_uri"] == "https://tcg.example.test/api/v1/auth/oidc/callback"
    assert q["code_challenge_method"] == "S256" and q["client_id"] == CLIENT_ID
    provider.codes["code-1"] = {"nonce": q["nonce"], "challenge": q["code_challenge"]}
    state = "falsch" if tamper_state else q["state"]
    r = c.get(f"{API}/auth/oidc/callback", params={"code": "code-1", "state": state}, follow_redirects=False)
    assert r.status_code == 302
    return r.headers["location"], c


def _token_from(location: str) -> str:
    frag = parse_qs(urlsplit(location).fragment)
    assert frag["next"] == ["/collections"]
    return frag["token"][0]


def test_auth_config_reports_sso(provider, client):
    cfg = TestClient(app).get(f"{API}/auth/config").json()
    assert cfg == {"oidc_enabled": True, "oidc_provider_name": "Authentik", "password_login": True}


def test_first_sso_login_claims_existing_collection(provider, client):
    # Bestand des Primär-Admins (Passwort-Login)
    mine = client.post(f"{API}/cards", json={"kartenname": "Vorher-Evoli", "besessen": True}).json()

    loc, _ = _sso_login(provider, {"sub": "ak-1", "preferred_username": "brian", "email": "b@example.test",
                                   "name": "Brian"})
    assert loc.startswith("/auth/callback#")          # gleicher Host → relativ
    token = _token_from(loc)
    sc = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    me = sc.get(f"{API}/auth/me").json()
    assert me["is_admin"] and me["sso_linked"] and me["username"] == "admin" and me["display_name"] == "Brian"
    assert sc.get(f"{API}/cards/{mine['id']}").status_code == 200   # gleicher Bestand

    # zweiter SSO-Benutzer → eigenes, leeres Konto (kein Admin), Pokédex vorbefüllt
    loc2, _ = _sso_login(provider, {"sub": "ak-2", "preferred_username": "sso-anna", "email": "a@example.test"})
    ac = TestClient(app, headers={"Authorization": f"Bearer {_token_from(loc2)}"})
    me2 = ac.get(f"{API}/auth/me").json()
    assert me2["username"] == "sso-anna" and not me2["is_admin"]
    assert ac.get(f"{API}/cards/{mine['id']}").status_code == 404
    stats = ac.get(f"{API}/cards/meta/stats").json()
    assert stats["besessen"] == 0 and stats["nicht_besessen"] == 1025

    # erneuter Login derselben Person → dasselbe Konto
    loc3, _ = _sso_login(provider, {"sub": "ak-2", "preferred_username": "sso-anna"})
    again = TestClient(app, headers={"Authorization": f"Bearer {_token_from(loc3)}"}).get(f"{API}/auth/me").json()
    assert again["id"] == me2["id"]
    client.delete(f"{API}/cards/{mine['id']}")


def test_explicit_admin_list_prevents_claiming(provider, client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_admin_users", "brian@example.test")
    loc, _ = _sso_login(provider, {"sub": "ak-9", "preferred_username": "sso-mallory"})
    me = TestClient(app, headers={"Authorization": f"Bearer {_token_from(loc)}"}).get(f"{API}/auth/me").json()
    assert me["username"] == "sso-mallory" and not me["is_admin"]
    # die gelistete E-Mail übernimmt den Primär-Admin
    loc, _ = _sso_login(provider, {"sub": "ak-10", "preferred_username": "bk", "email": "Brian@example.test"})
    me = TestClient(app, headers={"Authorization": f"Bearer {_token_from(loc)}"}).get(f"{API}/auth/me").json()
    assert me["username"] == "admin" and me["is_admin"]


def test_admin_group_and_userinfo_groups(provider, client, monkeypatch):
    monkeypatch.setattr(settings, "oidc_admin_users", "nobody")
    monkeypatch.setattr(settings, "oidc_admin_group", "pokefans")   # kommt nur per userinfo
    loc, _ = _sso_login(provider, {"sub": "ak-20", "preferred_username": "sso-gina"})
    me = TestClient(app, headers={"Authorization": f"Bearer {_token_from(loc)}"}).get(f"{API}/auth/me").json()
    assert me["is_admin"] is True


def test_hs256_signed_id_token(provider, client):
    provider.alg = "HS256"
    loc, _ = _sso_login(provider, {"sub": "ak-30", "preferred_username": "sso-hans"})
    assert TestClient(app, headers={"Authorization": f"Bearer {_token_from(loc)}"}).get(
        f"{API}/auth/me").status_code == 200


def test_state_mismatch_is_rejected(provider, client):
    loc, _ = _sso_login(provider, {"sub": "ak-40", "preferred_username": "sso-x"}, tamper_state=True)
    assert loc.startswith("/login?sso_error=")


def test_forged_signature_is_rejected(provider, client):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    real = provider.key
    provider.key = other                    # signiert mit fremdem Schlüssel …
    provider.jwk = lambda: {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(real.public_key())),
                            "kid": "k1", "alg": "RS256"}   # … JWKS zeigt den echten
    loc, _ = _sso_login(provider, {"sub": "ak-50", "preferred_username": "sso-forger"})
    assert loc.startswith("/login?sso_error=")


def test_foreign_origin_never_receives_token(provider, client):
    loc, _ = _sso_login(provider, {"sub": "ak-60", "preferred_username": "sso-o"}, origin="https://evil.example")
    assert loc.startswith("/auth/callback#")       # Positivliste: zurück auf den eigenen Host


def test_allowed_lan_origin_and_host_bounce(provider, client, monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://tcg.example.test")
    monkeypatch.setattr(settings, "oidc_allowed_origins", "http://192.168.1.225:3011")
    lan = TestClient(app, base_url="http://192.168.1.225:3011")
    r = lan.get(f"{API}/auth/oidc/login", params={"next": "/collections"}, follow_redirects=False)
    assert r.status_code == 302
    bounced = urlsplit(r.headers["location"])
    assert bounced.netloc == "tcg.example.test" and bounced.path == f"{API}/auth/oidc/login"
    assert parse_qs(bounced.query)["origin"] == ["http://192.168.1.225:3011"]
    loc, _ = _sso_login(provider, {"sub": "ak-70", "preferred_username": "sso-lan"},
                        origin="http://192.168.1.225:3011")
    assert loc.startswith("http://192.168.1.225:3011/auth/callback#token=")


def test_password_login_can_be_disabled(client, monkeypatch, test_password):
    monkeypatch.setattr(settings, "password_login", False)
    r = TestClient(app).post(f"{API}/auth/login", json={"username": "admin", "password": test_password})
    assert r.status_code == 403


def test_restore_of_single_user_backup_keeps_users(client):
    """Backup aus der Einzelbenutzer-Zeit (ohne users/user_id) → Bestand geht an
    den Primär-Admin, die bestehenden Benutzer bleiben erhalten."""
    import io
    import zipfile

    card = client.post(f"{API}/cards", json={"kartenname": "Restore-Relikt", "besessen": True}).json()
    me_before = client.get(f"{API}/auth/me").json()
    raw = client.get(f"{API}/data/backup").content
    zin = zipfile.ZipFile(io.BytesIO(raw))
    payload = json.loads(zin.read("data.json"))
    payload["tables"].pop("users")
    payload["tables"].pop("user_settings")
    for table in ("pokemon_cards", "collections", "sealed_products"):
        for row in payload["tables"][table]:
            row.pop("user_id", None)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        zout.writestr("data.json", json.dumps(payload))
    r = client.post(f"{API}/data/restore", files={"file": ("old.zip", out.getvalue(), "application/zip")},
                    data={"confirm": "JA_WIRKLICH"})
    assert r.status_code == 200, r.text
    # gleiches Token gilt weiter (Benutzer blieb), Karte gehört wieder dem Admin
    assert client.get(f"{API}/auth/me").json()["id"] == me_before["id"]
    assert client.get(f"{API}/cards/{card['id']}").status_code == 200
    db = SessionLocal()
    try:
        assert db.get(PokemonCard, card["id"]).user_id == me_before["id"]
    finally:
        db.close()
    client.delete(f"{API}/cards/{card['id']}")
