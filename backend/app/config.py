from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Eine Projektversion pro Dev-Stand (nicht getrennt nach Front-/Backend).
    # Spiegelt web/src/lib/version.ts › APP_VERSION; bei jedem Release beide
    # hochzählen. Per Env-Var APP_VERSION überschreibbar.
    app_version: str = "1.13.0"

    database_url: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    # 30 Tage (Lock-Spec Issue #1): Single-User-LAN-App, keine Revocation —
    # der Web-Client hält das Token in localStorage und loggt sich bei 401 neu ein.
    jwt_expire_minutes: int = 60 * 24 * 30  # 30 Tage

    # Erlaubte CORS-Origins, kommagetrennt (Env CORS_ORIGINS). Leer/ungesetzt →
    # Default: Prod-Web (3011) + lokaler Teststand (3021). Kein "*" mehr —
    # seit Auth-Zwang (Issue #1) wäre eine Wildcard-Origin ein unnötiges Risiko.
    cors_origins: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return origins or ["http://localhost:3011", "http://localhost:3021"]

    # Cardmarket-OAuth ist seit v0.7.0 nur noch optionaler Preis-Fallback –
    # Primärquelle für Preise ist TCGdex (kein Key nötig).
    cardmarket_app_token: str = ""
    cardmarket_app_secret: str = ""
    cardmarket_access_token: str = ""
    cardmarket_access_secret: str = ""


    # Optional: aktiviert die stärkere Scan-Variante B (Gemini). Default aus.
    # Ist der Key gesetzt, nutzt der Scan Gemini; sonst lokale OCR als Fallback.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    images_dir: str = "/app/images"
    # Regulation Marks eigener Karten kurz nach dem Start nachholen (TCGdex);
    # die Testsuite schaltet das ab.
    regulation_backfill_on_start: bool = True

    # Welche Sprachfassung des Kartenscans der Katalog-Sync bevorzugt ("de" | "en").
    # Env CATALOG_IMAGE_LANG. Default "de" = bisheriges Verhalten.
    catalog_image_lang: str = "de"

    # Währung der Instanz ("EUR" | "USD"), Env CURRENCY. Bestimmt, in welcher
    # Währung wert_eur/kaufpreis_eur/Preisverlauf GESPEICHERT werden (die
    # Spaltennamen bleiben aus Kompatibilität „_eur"). USD: TCGplayer-$ zuerst,
    # sonst Cardmarket-€ zum Tageskurs in $ umgerechnet.
    currency: str = "EUR"

    # Bild-Abgleich-Dienst (Scan-Provider „local", Env MATCHER_URL). Läuft im
    # selben Stack als Service „matcher".
    matcher_url: str = "http://matcher:8000"

    # ── Mehrbenutzer / Single Sign-On (OIDC, z. B. Authentik) ────────────────
    # OIDC ist aktiv, sobald Issuer + Client-ID gesetzt sind. Issuer bei
    # Authentik: https://<authentik>/application/o/<app-slug>/
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_provider_name: str = "Authentik"   # Beschriftung des Login-Knopfs
    oidc_scopes: str = "openid profile email"
    # Wer beim ersten SSO-Login den bestehenden Bestand (Primär-Admin) über-
    # nimmt und Admin wird: kommagetrennte Benutzernamen/E-Mails des Providers.
    # Leer → APP_USERNAME.
    oidc_admin_users: str = ""
    # Optional: Mitglieder dieser Provider-Gruppe werden (zusätzlich) Admin.
    oidc_admin_group: str = ""
    # Neue SSO-Benutzer automatisch anlegen (Zugang regelt der Provider über
    # die App-Bindung). False → nur bereits bekannte Benutzer dürfen rein.
    oidc_auto_create: bool = True
    # Öffentliche Basis-URL der App (z. B. https://tcg.example.com). Bestimmt
    # die Redirect-URI ({PUBLIC_URL}/api/v1/auth/oidc/callback); ungesetzt wird
    # sie aus dem Request abgeleitet (Host + Cf-Visitor/X-Forwarded-Proto).
    public_url: str = ""
    oidc_redirect_uri: str = ""
    # Weitere Web-Origins, auf die nach dem Login zurückgeleitet werden darf
    # (kommagetrennt, z. B. die LAN-Adresse). PUBLIC_URL + CORS_ORIGINS gelten immer.
    oidc_allowed_origins: str = ""
    # Lokaler Passwort-Login (Notzugang des Primär-Admins). Nur wirksam, wenn
    # ein Passwort-Hash konfiguriert ist.
    password_login: bool = True

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer.strip() and self.oidc_client_id.strip())

    @property
    def usd_mode(self) -> bool:
        return self.currency.strip().upper() == "USD"

    class Config:
        env_file = ".env"


settings = Settings()
