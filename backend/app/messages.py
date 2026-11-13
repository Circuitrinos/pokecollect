"""Englische Fassung der Fehlermeldungen, die das Backend an Menschen schickt.

Die Meldungen im Code bleiben deutsch (Upstream). Schickt der Client
`X-UI-Lang: EN` (das Web-Frontend tut das je nach UI-Sprache) oder fehlt der
Kopf und der Browser bevorzugt Englisch (Accept-Language), übersetzt
`localize()` die Meldung — in HTTPException-, 422- und SSO-Antworten.
Unbekannte Meldungen bleiben, wie sie sind.
"""
from __future__ import annotations

import re
from typing import Any, Optional

EN: dict[str, str] = {
    # Anmeldung
    "Passwort-Login ist deaktiviert": "Password login is disabled",
    "Ungültige Anmeldedaten": "Invalid username or password",
    "SSO ist nicht eingerichtet": "SSO is not configured",
    "Nicht eingeloggt": "Not logged in",
    "Ungültiges Token": "Invalid token",
    "Benutzer deaktiviert": "User is deactivated",
    "Benutzer ist deaktiviert": "User is deactivated",
    "Für dieses Konto ist PokéCollect nicht freigeschaltet": "This account is not allowed to use PokéCollect",
    "Anmeldung abgelaufen oder unterbrochen — bitte erneut versuchen":
        "Login expired or was interrupted — please try again",
    "Ungültiger Anmeldestatus — bitte erneut versuchen": "Invalid login state — please try again",
    "Provider lieferte kein ID-Token (Scope 'openid' fehlt?)":
        "The provider returned no ID token (is the 'openid' scope missing?)",
    "ID-Token ist HS256-signiert, aber OIDC_CLIENT_SECRET fehlt":
        "The ID token is HS256-signed, but OIDC_CLIENT_SECRET is missing",
    "Signaturschlüssel des ID-Tokens unbekannt": "Unknown ID token signing key",
    "ID-Token unlesbar": "The ID token is unreadable",
    "ID-Token passt nicht zu dieser Anmeldung (nonce)": "The ID token does not match this login (nonce)",
    # Rechte, Konten, Einstellungen
    "Nur für Administratoren": "Administrators only",
    "Nur Admins dürfen Instanz-Einstellungen ändern": "Only admins can change instance settings",
    "Aktuelles Passwort falsch": "Current password is wrong",
    "Benutzer nicht gefunden": "User not found",
    "Eigene Admin-Rechte/Zugang können nicht entzogen werden": "You can't remove your own admin rights or access",
    "Mindestens ein aktiver Admin muss bleiben": "At least one active admin must remain",
    # Freunde
    "Anfrage ist schon gesendet": "Request already sent",
    "Anfrage nicht gefunden": "Request not found",
    "Du kannst dich nicht selbst hinzufügen": "You can't add yourself",
    "Ihr seid schon befreundet": "You are already friends",
    "Kein Freund": "Not a friend",
    "Kein Konto mit diesem Benutzernamen": "No account with this username",
    "Nur mit Freunden teilbar": "Can only be shared with friends",
    # Karten, Sammlungen, Katalog
    "Karte nicht gefunden": "Card not found",
    "Karte nicht im Katalog": "Card not in the catalog",
    "Anderer Kartenname — nur ein anderer Druck derselben Karte": "A different card name — only another print of the same card",
    "Unbekannte Folierung": "Unknown foiling",
    "Karte nicht in dieser Sammlung": "Card not in this collection",
    "Katalog-Karte nicht gefunden": "Catalog card not found",
    "Katalog-Karte oder Sammlung nicht gefunden": "Catalog card or collection not found",
    "Katalog-Produkt nicht gefunden": "Catalog product not found",
    "Sammlung nicht gefunden": "Collection not found",
    "Sealed-Produkt nicht gefunden": "Sealed product not found",
    "Set-Sammlung braucht ein ziel_set_id": "A set collection needs a ziel_set_id",
    "Soll-Eintrag nicht gefunden": "Target entry not found",
    "Unbekannter Zustand": "Unknown condition",
    "collection_id fehlt": "collection_id is missing",
    "deck_id fehlt": "deck_id is missing",
    "Deck nicht gefunden": "Deck not found",
    "Karte nicht in der Deckliste": "Card not in the deck list",
    "Keine Kartenzeilen erkannt": "No card lines found",
    "Nur eigene Karten können in ein Deck": "Only cards you own can go into a deck",
    "Karte steckt schon in einem anderen Deck": "Card is already in another deck",
    "Karte nicht in diesem Deck": "Card is not in this deck",
    "Nicht-besessene Karten (Pokédex-Platzhalter) können nicht gelöscht werden.":
        "Cards you don't own (Pokédex placeholders) can't be deleted.",
    "Kein Kartenlink von pokemon.com": "Not a pokemon.com card link",
    "Zu diesem Link gibt es kein Kartenbild": "There is no card image for this link",
    "Kein Katalogbild für diese Karte gefunden": "No catalog image found for this card",
    "all_scanned gibt es nur für catalog_image": "all_scanned only works with catalog_image",
    "Für dieses Bild gibt es keine anderen Sprachen": "This image isn't available in other languages",
    "Kein Kartenbild in dieser Sprache": "There is no card image in this language",
    "Kein Originalfoto zum Neu-Zuschneiden": "There is no original photo to crop again",
    "Scan-Prüfung nicht gefunden": "Scan check not found",
    "Bild-Abgleich nicht erreichbar": "The image matcher can't be reached",
    "Bildadresse nicht erlaubt": "This image address isn't allowed",
    "Bild zu groß (max. 15 MB)": "Image too large (max. 15 MB)",
    "Ungültige Ecken": "Invalid corners",
    "Bild zu groß (max. 5 MB)": "Image too large (max. 5 MB)",
    "Ungültige Karten-ID": "Invalid card id",
    "Keine gültige Modelldatei": "Not a valid model file",
    "Kein eigenes Foto zum Markieren": "There is no photo of your own to mark",
    "Scan-Entwurf nicht gefunden": "Scan draft not found",
    "Schon gespeichert — vermutlich an einem anderen Gerät": "Already saved — probably on another device",
    "Entwurf zu groß": "Draft too large",
    "Entwurf ist kein gültiges JSON": "Draft is not valid JSON",
    "Entwurf ohne Kartenliste": "Draft without a card list",
    "Zu wenige Stempel zum Lernen — mindestens 3 von Hand markieren":
        "Too few stamps to learn from — mark at least 3 by hand",
    "Karte ohne Katalog-Zuordnung": "The card isn't linked to a catalog card",
    "4 Ecken mit Werten zwischen 0 und 1 erwartet": "Expected 4 corners with values between 0 and 1",
    "Markierung zu klein": "The marked area is too small",
    "Leere Datei": "Empty file",
    # Bilder
    "Bild nicht verfügbar": "Image not available",
    "Bild zu groß (max. 12 MB)": "Image too large (max. 12 MB)",
    "Content-Type muss image/* sein": "Content-Type must be image/*",
    "Leeres Bild": "Empty image",
    "Leeres oder zu großes Vorschaubild": "Preview image is empty or too large",
    "Bilddatei konnte nicht verarbeitet werden": "The image file could not be processed",
    # Sicherung
    "Backup größer als 500 MB": "Backup larger than 500 MB",
    "Datei ist kein gültiges ZIP": "The file is not a valid ZIP",
    "ZIP enthält keine data.json — kein PokéCollect-Backup": "The ZIP has no data.json — not a PokéCollect backup",
    "data.json hat nicht die erwartete Struktur (tables fehlt)":
        "data.json doesn't have the expected structure (tables is missing)",
    "data.json ist kein gültiges JSON": "data.json is not valid JSON",
    "data.json ohne Versionsfeld (app_version) — kein PokéCollect-Backup?":
        "data.json has no version field (app_version) — not a PokéCollect backup?",
    'Restore nicht bestätigt — confirm="JA_WIRKLICH" ist Pflicht (ersetzt ALLE Daten).':
        'Restore not confirmed — confirm="JA_WIRKLICH" is required (replaces ALL data).',
    # Eingaben
    "Name darf nicht leer sein": "Name can't be empty",
    "Darf nicht leer sein": "Can't be empty",
    "Text enthält ungültige Zeichen (nicht als UTF-8 kodierbar)": "Text contains invalid characters (not UTF-8)",
    "Text enthält ein NUL-Zeichen": "Text contains a NUL character",
    "Struktur zu tief verschachtelt": "Structure nested too deeply",
    "Text enthält unerlaubte Steuerzeichen": "Text contains control characters",
    "Liste zu lang": "List too long",
    "Objekt zu groß": "Object too large",
    "Feld darf nicht null sein (weglassen = unverändert)": "Field can't be null (leave it out to keep it unchanged)",
    "Regulation Mark: ein Buchstabe A–Z": "Regulation mark: one letter A–Z",
    "Ein Wert liegt außerhalb des gültigen Bereichs": "A value is out of range",
    "Steuerzeichen in Pfad oder Query sind nicht erlaubt": "Control characters in path or query are not allowed",
}

# Meldungen mit eingesetzten Werten: (Muster, englische Vorlage mit \1, \2 …)
PATTERNS: list[tuple[re.Pattern[str], str]] = [(re.compile(p, re.S), r) for p, r in [
    (r"^Backup-Format (.*) wird nicht unterstützt \(erwartet: (.*)\)$",
     r"Backup format \1 is not supported (expected: \2)"),
    (r"^Tabelle (.*) in data\.json ist keine Zeilenliste$", r"Table \1 in data.json is not a list of rows"),
    (r"^Unsicherer Pfad im ZIP: (.*)$", r"Unsafe path in the ZIP: \1"),
    (r"^Set '(.*)' existiert bereits$", r"Set '\1' already exists"),
    (r"^Pokémon-Namen nicht ladbar: (.*)$", r"Couldn't load Pokémon names: \1"),
    (r"^Dateityp (.*) nicht erlaubt \(nur (.*)\)$", r"File type \1 not allowed (only \2)"),
    (r"^Anmeldedienst nicht erreichbar \((.*)\)$", r"Login service unreachable (\1)"),
    (r"^OIDC-Discovery unvollständig: (.*) fehlt$", r"OIDC discovery incomplete: \1 is missing"),
    (r"^Token-Endpoint nicht erreichbar \((.*)\)$", r"Token endpoint unreachable (\1)"),
    (r"^Code-Einlösung abgelehnt: (.*)$", r"Code exchange rejected: \1"),
    (r"^Schlüssel des Anmeldedienstes nicht ladbar \((.*)\)$", r"Couldn't load the login service keys (\1)"),
    (r"^Nicht unterstützter Signatur-Algorithmus: (.*)$", r"Unsupported signing algorithm: \1"),
    (r"^ID-Token ungültig \((.*)\)$", r"Invalid ID token (\1)"),
]]


def to_english(msg: str) -> str:
    if msg in EN:
        return EN[msg]
    for rx, repl in PATTERNS:
        if rx.match(msg):
            return rx.sub(repl, msg)
    return msg


def wants_english(headers: Any) -> bool:
    """X-UI-Lang (vom Frontend) gewinnt; sonst die erste Browsersprache; ohne
    beides bleibt es deutsch (Skripte, Tests)."""
    ui = (headers.get("x-ui-lang") or "").strip().upper()
    if ui in ("DE", "EN"):
        return ui == "EN"
    first = (headers.get("accept-language") or "").split(",")[0].strip().lower()
    return bool(first) and not first.startswith("de")


def localize(detail: Optional[Any], headers: Any) -> Any:
    """Eine Fehlermeldung (Text oder FastAPI-422-Liste) für den Client übersetzen."""
    if detail is None or not wants_english(headers):
        return detail
    if isinstance(detail, str):
        return to_english(detail)
    if isinstance(detail, list):
        out = []
        for item in detail:
            if isinstance(item, dict) and isinstance(item.get("msg"), str):
                msg = item["msg"]
                prefix = "Value error, " if msg.startswith("Value error, ") else ""
                item = {**item, "msg": prefix + to_english(msg[len(prefix):])}
            out.append(item)
        return out
    return detail
