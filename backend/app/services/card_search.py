"""
Kartensuche mit Schlüsselwörtern wie bei Limitless TCG (limitlesstcg.com/cards/syntax),
angepasst an die eigene Sammlung.

Leerzeichen = UND, Komma = ODER (``is:holo,rh``), Plus = UND in einem Feld
(``is:ex+1st``), ``-`` davor verneint, ``!`` davor verlangt den genauen Wert,
Anführungszeichen oder ``_`` für Leerzeichen (``a:"mitsuhiro arita"``, ``a:mitsuhiro_arita``),
Vergleiche ``< <= > >= =`` bei Nummer, Pokédex-Nr., Preis und Regulation Mark.

Wie bei Limitless: name, set/e, number/n, artist/illus/a, rarity/r, type/t, stage/s,
is:, regulation/mark/reg, format/f, language/lang/l, pokedex/dex/p, price/usd/eur, sort/o.
Dazu für die Sammlung: in: (Sammlung, ``in:none``, ``in:deck``), deck:, foil:,
is:holo/rh/1st/stamp/owned/wishlist/photo.
Was die Sammlung nicht kennt (hp, attack, weakness, text …), kommt als Hinweis zurück.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import Integer, String, and_, cast, false, func, not_, or_, select
from sqlalchemy.orm import Session

from app.models.card import PokemonCard
from app.models.collection import Collection, collection_cards
from app.models.deck import Deck
from app.models.pokemon_set import PokemonSet
from app.models.tcgdex_catalog import TcgdexCatalog

_TERM = re.compile(r"^(?P<neg>-)?(?P<exact>!)?(?P<key>[a-z][a-z0-9.]*)(?P<op><=|>=|!=|<|>|=|:)(?P<val>.*)$", re.I)
_TOKEN = re.compile(r'(?:[^\s"]+|"[^"]*")+')

KEYS = {
    "name": "name",
    "set": "set", "e": "set", "expansion": "set",
    "number": "number", "n": "number", "nr": "number",
    "artist": "artist", "illus": "artist", "illustrator": "artist", "a": "artist",
    "rarity": "rarity", "r": "rarity",
    "type": "type", "t": "type",
    "stage": "stage", "s": "stage",
    "is": "is",
    "regulation": "reg", "mark": "reg", "reg": "reg",
    "format": "format", "f": "format",
    "language": "lang", "lang": "lang", "l": "lang",
    "pokedex": "dex", "dex": "dex", "p": "dex",
    "price": "price", "usd": "price", "eur": "price", "cm": "price", "value": "price",
    "sort": "sort", "order": "sort", "o": "sort",
    "in": "in", "collection": "in", "binder": "in",
    "deck": "deck",
    "foil": "foil", "foiling": "foil",
}
# Limitless-Felder, zu denen die Sammlung keine Daten hat
UNSUPPORTED = {"text", "hp", "attack", "att", "ac", "has", "weakness", "weak", "w", "resistance", "rs",
               "retreat", "rc", "series", "block", "era", "banned", "bans", "unique", "u", "display", "d", "show"}
COMPARABLE = {"number", "dex", "price", "reg"}

_RARITY = {
    "c": ["Common"], "common": ["Common"], "u": ["Uncommon"], "uncommon": ["Uncommon"],
    "r": ["Rare"], "rare": ["Rare"], "hr": ["Rare"], "holo-rare": ["Rare"],
    "rr": ["Double Rare", "Holo Rare V", "Holo Rare VMAX", "Holo Rare VSTAR"],
    "double-rare": ["Double Rare", "Holo Rare V", "Holo Rare VMAX", "Holo Rare VSTAR"],
    "rrr": ["Holo Rare VMAX"], "triple-rare": ["Holo Rare VMAX"],
    "ur": ["Ultra Rare"], "ultra-rare": ["Ultra Rare"], "sr": ["Secret Rare"], "secret-rare": ["Secret Rare"],
    "rar": ["Rainbow Rare"], "rainbow-rare": ["Rainbow Rare"],
    "ir": ["Illustration Rare"], "illustration-rare": ["Illustration Rare"],
    "sir": ["Special Illustration Rare"], "special-illustration-rare": ["Special Illustration Rare"],
    "hyper": ["Hyper Rare"], "hyper-rare": ["Hyper Rare"], "mhr": ["Mega Hyper Rare"],
    "ace": ["ACE SPEC Rare"], "ace-spec": ["ACE SPEC Rare"],
    "shiny": ["Shiny Rare"], "shiny-rare": ["Shiny Rare"], "ssr": ["Shiny Ultra Rare"], "shiny-ultra-rare": ["Shiny Ultra Rare"],
    "promo": ["Promo"], "pr": ["Promo"],
}
_FOIL = {"normal": "Normal", "nonholo": "Normal", "non-holo": "Normal", "holo": "Holo",
         "rh": "Reverse Holo", "reverse": "Reverse Holo", "reverse-holo": "Reverse Holo", "revholo": "Reverse Holo"}
_VERSION = {"fa": "Full Art", "full-art": "Full Art", "aa": "Special Art", "alternate-art": "Special Art",
            "rainbow": "Rainbow", "gold": "Gold"}
_STAGE = {"basic": "basic", "0": "basic", "stage1": "stage1", "1": "stage1", "stage2": "stage2", "2": "stage2",
          "vstar": "vstar", "vmax": "vmax", "vunion": "vunion", "v-union": "vunion", "mega": "mega", "break": "break",
          "restored": "restored", "legend": "legend"}
_TYPES = {"pokemon": ("category", "pokemon"), "pkmn": ("category", "pokemon"), "pokémon": ("category", "pokemon"),
          "trainer": ("category", "trainer"), "tr": ("category", "trainer"),
          "energy": ("category", "energy"), "en": ("category", "energy"),
          "supporter": ("trainer", "supporter"), "su": ("trainer", "supporter"),
          "item": ("trainer", "item"), "it": ("trainer", "item"),
          "tool": ("trainer", "tool"), "to": ("trainer", "tool"), "pokemon-tool": ("trainer", "tool"),
          "stadium": ("trainer", "stadium"), "st": ("trainer", "stadium"),
          "special": ("energy", "special"), "special-energy": ("energy", "special"), "sp": ("energy", "special"),
          "basic-energy": ("energy", "basic")}
_POKEMON_TYPES = {"g", "r", "w", "l", "p", "f", "d", "m", "n", "c", "y", "grass", "fire", "water", "lightning",
                  "psychic", "fighting", "darkness", "dark", "metal", "dragon", "colorless", "fairy"}
_LANG = {"jp": "JP", "ja": "JP", "cn": "CN", "zh": "CN"}
_SORT = {"pokedex": "pokedex_nr", "dex": "pokedex_nr", "set": "set", "usd": "wert", "eur": "wert", "price": "wert",
         "value": "wert", "added": "hinzugefuegt_am", "date": "hinzugefuegt_am", "new": "hinzugefuegt_am",
         "regulation": "regulation", "reg": "regulation", "collection": "collection", "in": "collection"}


@dataclass
class Term:
    key: Optional[str]          # None = Freitext (Name)
    op: str
    values: list[list[str]]     # ODER über „,", UND über „+"
    neg: bool
    exact: bool
    raw: str


@dataclass
class Parsed:
    terms: list[Term] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)   # {"term", "reason": "unsupported" | "unknown" | "value"}
    sort: Optional[str] = None


def _unquote(v: str) -> str:
    return v.replace('"', "")


def parse(text: str) -> Parsed:
    text = (text or "").replace("“", '"').replace("”", '"').replace("„", '"').replace("–", "-").replace("—", "-")
    out = Parsed()
    for tok in _TOKEN.findall(text):
        m = _TERM.match(tok)
        if m and m.group("key").lower() not in ("http", "https"):
            raw_key = m.group("key").lower()
            key = KEYS.get(raw_key)
            val = _unquote(m.group("val")).strip()
            if key is None:
                out.warnings.append({"term": tok, "reason": "unsupported" if raw_key in UNSUPPORTED else "unknown"})
                continue
            op = "=" if m.group("op") == ":" else m.group("op")
            if op not in ("=", "!=") and key not in COMPARABLE:
                out.warnings.append({"term": tok, "reason": "value"})
                continue
            neg = bool(m.group("neg"))
            if op == "!=":
                op, neg = "=", not neg
            if not val:
                out.warnings.append({"term": tok, "reason": "value"})
                continue
            if key == "sort":
                s = _SORT.get(val.lower())
                if s:
                    out.sort = s
                else:
                    out.warnings.append({"term": tok, "reason": "value"})
                continue
            values = [[p.strip() for p in alt.split("+") if p.strip()] for alt in val.split(",") if alt.strip()]
            out.terms.append(Term(key, op, values, neg, bool(m.group("exact")), tok))
        else:
            neg = tok.startswith("-") and len(tok) > 1
            word = tok[1:] if neg else tok
            exact = word.startswith("!") and len(word) > 1
            word = _unquote(word[1:] if exact else word).strip()
            if word:
                out.terms.append(Term(None, "=", [[word]], neg, exact, tok))
    return out


# ── SQL ─────────────────────────────────────────────────────────────────────

def _name():
    return func.coalesce(PokemonCard.englischer_name, PokemonCard.kartenname)


def _text(v: str) -> str:
    return v.replace("_", " ").replace("%", r"\%")


def _like(col, v: str, exact: bool):
    v = _text(v)
    return func.lower(col) == v.lower() if exact else col.ilike(f"%{v}%")


def _catalog(cond):
    """Bedingung über den verknüpften Katalog-Druck."""
    return func.upper(PokemonCard.tcgdex_card_id).in_(select(func.upper(TcgdexCatalog.card_id)).where(cond))


def _norm(col):
    return func.lower(func.regexp_replace(col, "[^A-Za-z0-9]", "", "g"))


def _number_col():
    left = func.split_part(PokemonCard.karten_nr, "/", 1)
    return cast(func.nullif(func.regexp_replace(left, "[^0-9]", "", "g"), ""), Integer)


def _cmp(col, op: str, v):
    return {"=": col == v, "<": col < v, "<=": col <= v, ">": col > v, ">=": col >= v}[op]


class _Bad(Exception):
    pass


def _one(db: Session, t: Term, v: str, ctx: dict):
    k, low = t.key, v.lower()
    if k is None or k == "name":
        if t.exact:
            return or_(func.lower(PokemonCard.englischer_name) == _text(v).lower(),
                       func.lower(PokemonCard.kartenname) == _text(v).lower())
        cond = PokemonCard.kartenname.ilike(f"%{_text(v)}%") | PokemonCard.englischer_name.ilike(f"%{_text(v)}%")
        if k is None and v.isdigit():
            cond = cond | cast(PokemonCard.pokedex_nr, String).ilike(f"%{v}%")
        return cond
    if k == "set":
        code = v.upper()
        if t.exact:
            return or_(PokemonCard.set_edition.ilike(f"%({code})"), func.upper(PokemonCard.set_edition) == code,
                       func.lower(PokemonCard.set_id) == low)
        return or_(PokemonCard.set_edition.ilike(f"%({code})%"), func.upper(PokemonCard.set_edition) == code,
                   func.lower(PokemonCard.set_id) == low, PokemonCard.set_edition.ilike(f"%{_text(v)}%"))
    if k == "number":
        if t.op == "=":
            return func.upper(func.ltrim(func.split_part(PokemonCard.karten_nr, "/", 1), "0")) == (v.upper().lstrip("0") or "0")
        if not v.isdigit():
            raise _Bad
        return _cmp(_number_col(), t.op, int(v))
    if k == "dex":
        if not v.isdigit():
            raise _Bad
        return _cmp(PokemonCard.pokedex_nr, t.op, int(v))
    if k == "price":
        try:
            num = float(v.lstrip("$€"))
        except ValueError:
            raise _Bad from None
        return _cmp(PokemonCard.wert_eur, t.op, num)
    if k == "artist":
        return _like(PokemonCard.illustrator, v, t.exact)
    if k == "rarity":
        names = _RARITY.get(low)
        if names:
            return PokemonCard.seltenheit.in_(names)
        return _like(PokemonCard.seltenheit, v.replace("-", " "), t.exact)
    if k == "reg":
        if low in ("none", "-"):
            return PokemonCard.regulation_mark.is_(None) & PokemonCard.legal_mark.isnot(None)
        if not re.fullmatch(r"[a-z]", low):
            raise _Bad
        return _cmp(PokemonCard.regulation_mark, t.op, v.upper())
    if k == "lang":
        return PokemonCard.sprache == _LANG.get(low, v.upper())
    if k == "foil":
        f = _FOIL.get(low)
        if f is None:
            raise _Bad
        return PokemonCard.folierung == f
    if k == "format":
        from app.services import regulation

        if low in ("standard", "std", "s"):
            return regulation.standard_legal_clause(ctx.setdefault("min_mark", regulation.standard_min_mark(db)))
        if low in ("expanded", "exp", "e"):
            return PokemonCard.basic_energy.is_(True) | _catalog(TcgdexCatalog.legal_expanded.is_(True))
        raise _Bad
    if k == "stage":
        s = _STAGE.get(low.replace(" ", ""))
        if s is None:
            raise _Bad
        return _catalog(_norm(TcgdexCatalog.stage) == s)
    if k == "type":
        if low in _POKEMON_TYPES:
            ctx.setdefault("no_types", True)
            raise _Bad
        kind = _TYPES.get(low)
        if kind is None:
            raise _Bad
        what, val = kind
        if what == "category":
            return _catalog(func.lower(TcgdexCatalog.category).like(f"{val[:7]}%"))
        if what == "trainer":
            return _catalog(func.lower(TcgdexCatalog.trainer_type) == val)
        basic = PokemonCard.basic_energy.is_(True)
        if val == "basic":
            return basic
        return ~basic & _catalog(func.lower(TcgdexCatalog.category) == "energy")
    if k == "is":
        return _is(db, low, ctx)
    if k == "in":
        if low in ("none", "nothing"):
            return ~PokemonCard.id.in_(select(collection_cards.c.card_id))
        if low == "any":
            return PokemonCard.id.in_(select(collection_cards.c.card_id))
        if low == "deck":
            return PokemonCard.deck_id.isnot(None)
        hit = select(collection_cards.c.card_id).join(Collection, Collection.id == collection_cards.c.collection_id)
        hit = hit.where(func.lower(Collection.name) == _text(v).lower() if t.exact else Collection.name.ilike(f"%{_text(v)}%"))
        return PokemonCard.id.in_(hit)
    if k == "deck":
        if low in ("none", "no"):
            return PokemonCard.deck_id.is_(None)
        if low in ("any", "yes"):
            return PokemonCard.deck_id.isnot(None)
        decks = select(Deck.id).where(func.lower(Deck.name) == _text(v).lower() if t.exact else Deck.name.ilike(f"%{_text(v)}%"))
        return PokemonCard.deck_id.in_(decks)
    raise _Bad


def _is(db: Session, v: str, ctx: dict):
    name = _name()
    if v == "ex":
        return name.op("~*")(r"[ -]ex$")
    if v in ("v", "vmax", "vstar", "gx"):
        return name.op("~")(rf"[ -]{v.upper()}$")
    if v in ("vunion", "v-union"):
        return name.ilike("%V-UNION%")
    if v in ("prism", "prism-star"):
        return name.ilike("%◇%") | name.ilike("%prism star%")
    if v == "radiant":
        return name.ilike("radiant %")
    if v in ("tag-team", "tt"):
        return name.like("%&%")
    if v == "mega":
        return name.ilike("mega %") | name.like("M %")
    if v in ("ace", "ace-spec"):
        return (PokemonCard.seltenheit == "ACE SPEC Rare") | _catalog(TcgdexCatalog.rarity.ilike("ace spec%"))
    if v in ("basic-energy",):
        return PokemonCard.basic_energy.is_(True)
    if v in _FOIL:
        return PokemonCard.folierung == _FOIL[v]
    if v in _VERSION:
        return PokemonCard.kartenversion == _VERSION[v]
    if v in ("1st", "first", "first-edition", "1st-edition"):
        return PokemonCard.erste_edition.is_(True)
    if v in ("stamp", "stamped", "play", "prize-pack", "pp"):
        return PokemonCard.prize_pack.is_(True)
    if v == "owned":
        return PokemonCard.besessen.is_(True)
    if v in ("wishlist", "wish", "wanted"):
        return PokemonCard.wunschliste.is_(True)
    if v == "pokedex":
        return PokemonCard.im_pokedex.is_(True)
    if v == "photo":
        return PokemonCard.bild_karte_pfad.isnot(None) | PokemonCard.bild_scan_pfad.isnot(None)
    if v in ("legal", "standard"):
        from app.services import regulation

        return regulation.standard_legal_clause(ctx.setdefault("min_mark", regulation.standard_min_mark(db)))
    if v in ("linked", "matched"):
        return PokemonCard.tcgdex_card_id.isnot(None)
    raise _Bad


def _kurzcode(db: Session, words: list[str]):
    """„MEG 117" → Set + Nummer (wie bisher), wenn das Kürzel ein bekanntes Set ist."""
    from app.domain.search import parse_kurzcode

    kc = parse_kurzcode(" ".join(words))
    if not kc or not db.scalar(select(PokemonSet.code).where(PokemonSet.code == kc.code)):
        return None
    return and_(or_(PokemonCard.set_edition.ilike(f"%({kc.code})%"), PokemonCard.set_edition.ilike(kc.code)),
                func.ltrim(func.split_part(PokemonCard.karten_nr, "/", 1), "0") == kc.nr)


def conditions(db: Session, text: str) -> tuple[list, Parsed]:
    """Suchtext → (SQL-Bedingungen, Parsed mit Hinweisen und ggf. Sortierung)."""
    parsed = parse(text)
    conds = []
    ctx: dict = {}
    free = [t for t in parsed.terms if t.key is None and not t.neg and not t.exact]
    if free and len(free) <= 2:
        kc = _kurzcode(db, [t.values[0][0] for t in free])
        if kc is not None:
            conds.append(kc)
            parsed.terms = [t for t in parsed.terms if t not in free]
    for t in parsed.terms:
        try:
            alts = [and_(*[_one(db, t, v, ctx) for v in group]) for group in t.values]
        except _Bad:
            parsed.warnings.append({"term": t.raw, "reason": "types" if ctx.pop("no_types", None) else "value"})
            continue
        cond = or_(*alts) if len(alts) > 1 else alts[0]
        conds.append(not_(func.coalesce(cond, false())) if t.neg else cond)
    return conds, parsed

