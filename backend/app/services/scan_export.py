"""Welche Kartenfotos in den Scan-Trainingsdatensatz kommen (Admin-Export).

Eine Quelle für die Bedingung — Export, Admin-Übersicht und Kartenfilter
nutzen dieselben Ausdrücke. Exportierbar ist ein Foto mit bestätigter
Katalog-Karte (Label) und gespeichertem Originalfoto (aus dem der Zuschnitt
stammt).
"""
from sqlalchemy import and_, func, or_

from app.models.card import PokemonCard


def photo():
    return func.coalesce(PokemonCard.bild_scan_pfad, PokemonCard.bild_karte_pfad)


def has_photo():
    return photo().isnot(None)


def no_card():
    return and_(has_photo(), PokemonCard.tcgdex_card_id.is_(None))


def no_original():
    return and_(has_photo(), PokemonCard.bild_original_pfad.is_(None))


def exportable():
    return and_(has_photo(), PokemonCard.tcgdex_card_id.isnot(None), PokemonCard.bild_original_pfad.isnot(None))


def not_exportable():
    return and_(has_photo(), or_(PokemonCard.tcgdex_card_id.is_(None), PokemonCard.bild_original_pfad.is_(None)))
