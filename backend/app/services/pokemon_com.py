"""
Kartenbilder aus der offiziellen Kartendatenbank (pokemon.com) als Rückfall,
wenn TCGdex für eine Karte kein Bild hat.

Die Seiten von pokemon.com stecken hinter einem Bot-Schutz, die Bilder liegen
aber direkt auf dem CDN unter einem festen Muster:

  https://assets.pokemon.com/static-assets/content-assets/cms2/img/cards/web/SV08/SV08_EN_162.png
  https://assets.pokemon.com/static-assets/content-assets/cms2/img/cards/web/SWSHP/SWSHP_EN_SWSH300.png

Der Set-Code dort weicht von der TCGdex-ID ab ("sv08.5" → "SV8PT5",
"swsh4.5" → "SWSH45", Trainer Gallery "swsh11tg" → "SWSH11"). Statt einer
festen Tabelle probieren wir wenige Kandidaten (HEAD, ohne Weiterleitungen)
und merken uns je Set, welcher Code passt. Nur englische Bilder (_EN_).

Eigenimplementierung (MIT). Kein Code aus Drittprojekten übernommen.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

log = logging.getLogger(__name__)

HOST = "assets.pokemon.com"
_BASES = (
    "https://assets.pokemon.com/static-assets/content-assets/cms2/img/cards/web/",
    "https://assets.pokemon.com/assets/cms2/img/cards/web/",
)
_UA = "PokeCollect/0.7 (+self-hosted)"

# Bekannte Abweichungen (zuerst probiert); alles andere leiten _codes() ab.
_SPECIAL = {
    "swsh3.5": ["SWSH35"],
    "swsh4.5": ["SWSH45"],
    "sv10.5w": ["RSV10PT5"],
    "sv10.5b": ["ZSV10PT5"],
}

# Set-ID → (Basis, Code), der schon einmal getroffen hat (Prozess-Cache)
_known: dict[str, tuple[str, str]] = {}
_SAFE = re.compile(r"^[A-Za-z0-9.\-]+$")


def _codes(set_id: str) -> list[str]:
    """Kandidaten für den pokemon.com-Set-Code zu einer TCGdex-Set-ID."""
    sid = set_id.strip().lower()
    out: list[str] = list(_SPECIAL.get(sid, []))
    # Trainer/Galarian Gallery sind auf pokemon.com Teil des Haupt-Sets
    base = re.sub(r"(tg|gg)$", "", sid)
    m = re.match(r"^([a-z]+)0*(\d+)(?:\.(\d+))?([a-z]?)$", base)
    if m:
        prefix, num, half, suffix = m.group(1), m.group(2), m.group(3), m.group(4)
        padded = num.zfill(2) if prefix == "sv" or prefix == "me" else num
        if half:
            out += [f"{prefix}{num}pt{half}{suffix}", f"{prefix}{padded}pt{half}{suffix}", f"{prefix}{num}{half}{suffix}"]
        else:
            out += [f"{prefix}{padded}{suffix}", f"{prefix}{num}{suffix}"]
    out.append(base.replace(".", "pt"))
    seen, codes = set(), []
    for c in out:
        c = c.upper()
        if c and _SAFE.match(c) and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes[:4]


def _numbers(local_id: str, set_id: str = "") -> list[str]:
    """"065" → ["65", "065"]; "SWSH300"/"TG05" bleiben; Promo "300" in swshp →
    auch "SWSH300"."""
    from app.services.tcgdex import PROMO_PREFIX
    lid = local_id.strip()
    out = []
    if lid.isascii() and lid.isdigit():
        prefix = PROMO_PREFIX.get(set_id.strip().lower())
        if prefix:
            out += [f"{prefix}{lid.zfill(3)}", f"{prefix}{int(lid)}"]
        out.append(str(int(lid)))
    out.append(lid)
    return [n for i, n in enumerate(out) if n and _SAFE.match(n) and n not in out[:i]]


def candidate_urls(set_id: Optional[str], local_id: Optional[str]) -> list[str]:
    if not set_id or not local_id:
        return []
    urls: list[str] = []
    known = _known.get(set_id.lower())
    pairs = [known] if known else [(b, c) for c in _codes(set_id) for b in _BASES]
    for base, code in pairs:
        for num in _numbers(local_id, set_id):
            urls.append(f"{base}{code}/{code}_EN_{num}.png")
    return urls


async def _exists(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.head(url)
        if r.status_code == 405:                       # kein HEAD → kurz per GET
            async with client.stream("GET", url) as g:
                return g.status_code == 200 and g.headers.get("content-type", "").startswith("image/")
        return r.status_code == 200 and r.headers.get("content-type", "").startswith("image/")
    except httpx.HTTPError:
        return False


async def find_image(set_id: Optional[str], local_id: Optional[str],
                     client: Optional[httpx.AsyncClient] = None) -> Optional[str]:
    """Erste Kandidaten-URL, die ein Bild liefert, oder None."""
    urls = candidate_urls(set_id, local_id)
    if not urls:
        return None
    own = client is None
    client = client or httpx.AsyncClient(timeout=8.0, follow_redirects=False, headers={"User-Agent": _UA})
    try:
        for url in urls:
            if await _exists(client, url):
                base, code = url.rsplit("/", 2)[0] + "/", url.rsplit("/", 2)[1]
                _known[set_id.lower()] = (base, code)
                return url
        return None
    finally:
        if own:
            await client.aclose()


async def find_many(items: list[tuple[str, str, str]], conc: int = 6) -> dict[str, str]:
    """[(schlüssel, set_id, local_id)] → {schlüssel: url} für alle Treffer.
    Je Set erst eine Karte allein (lernt den Code), dann der Rest parallel."""
    found: dict[str, str] = {}
    by_set: dict[str, list[tuple[str, str]]] = {}
    for key, sid, lid in items:
        by_set.setdefault(sid, []).append((key, lid))
    sem = asyncio.Semaphore(conc)
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False, headers={"User-Agent": _UA}) as client:
        async def one(key: str, sid: str, lid: str):
            async with sem:
                url = await find_image(sid, lid, client)
            if url:
                found[key] = url

        for sid, cards in by_set.items():
            # bis zu drei Karten nacheinander, bis der Code des Sets bekannt ist
            rest = list(cards)
            while rest and sid.lower() not in _known and len(cards) - len(rest) < 3:
                key, lid = rest.pop(0)
                await one(key, sid, lid)
            if sid.lower() in _known:
                await asyncio.gather(*(one(k, sid, l) for k, l in rest))
    return found


# ── Links von pokemon.com (Nutzer kopiert sie aus seinem Browser) ────────────

_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"']+")
_SERIES = re.compile(r"/series?/([A-Za-z0-9.\-]+)/([A-Za-z0-9.\-]+)/?$")
_IMAGE = re.compile(r"/cards/web/([A-Za-z0-9.\-]+)/\1_([A-Z]{2})_([A-Za-z0-9.\-]+)\.png$")


def parse_link(text: Optional[str]) -> Optional[dict]:
    """
    Kartenseite oder Bildadresse von pokemon.com (auch eingebettet in einen
    geteilten Text) → {"code", "number", "image"}. "image" nur, wenn direkt
    eine Bildadresse kam. None, wenn nichts davon erkennbar ist.
      …/pokemon-tcg/pokemon-cards/series/swsh12/65/      → ("swsh12", "65")
      …/cms2/img/cards/web/SWSHP/SWSHP_EN_SWSH300.png     → ("SWSHP", "SWSH300")
    """
    from urllib.parse import urlsplit
    if not text:
        return None
    for raw in _URL_IN_TEXT.findall(text.strip()) or [text.strip()]:
        try:
            parts = urlsplit(raw)
        except ValueError:
            continue
        host = (parts.hostname or "").lower()
        if host == HOST:
            m = _IMAGE.search(parts.path)
            if m:
                return {"code": m.group(1), "number": m.group(3),
                        "image": f"https://{HOST}{parts.path}" if m.group(2) == "EN" else None}
        if host.endswith("pokemon.com"):
            m = _SERIES.search(parts.path)
            if m and _SAFE.match(m.group(1)) and _SAFE.match(m.group(2)):
                return {"code": m.group(1), "number": m.group(2), "image": None}
    return None


def image_urls_for(code: str, number: str) -> list[str]:
    c = code.upper()
    return [f"{base}{c}/{c}_EN_{number}.png" for base in _BASES]


async def image_for_link(link: dict) -> Optional[str]:
    """Bild zur Kartenseite: die Adresse folgt aus Set-Code + Nummer (geprüft)."""
    urls = [link["image"]] if link.get("image") else image_urls_for(link["code"], link["number"])
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False, headers={"User-Agent": _UA}) as client:
        for url in urls:
            if await _exists(client, url):
                return url
    return None


def tcgdex_set_candidates(code: str) -> list[str]:
    """pokemon.com-Set-Code → mögliche TCGdex-Set-IDs ("SV8PT5" → "sv08.5",
    "SWSH45" → "swsh4.5", "SWSHP" → "swshp")."""
    c = code.strip().lower()
    out = [sid for sid, codes in _SPECIAL.items() if c.upper() in codes]
    m = re.match(r"^([a-z]+?)(\d+)(?:pt(\d))?([a-z]?)$", c)
    if m:
        prefix, num, half, suffix = m.groups()
        for n in dict.fromkeys([num.lstrip("0") or "0", num.zfill(2)]):
            out.append(f"{prefix}{n}.{half}{suffix}" if half else f"{prefix}{n}{suffix}")
        if not half and len(num) >= 2 and num.endswith("5"):          # SWSH45 → swsh4.5
            out.append(f"{prefix}{num[:-1]}.5{suffix}")
    out.append(c)
    return list(dict.fromkeys(out))
