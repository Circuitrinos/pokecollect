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


def _numbers(local_id: str) -> list[str]:
    """"065" → ["65", "065"]; "SWSH300"/"TG05" bleiben, wie sie sind."""
    lid = local_id.strip()
    out = []
    if lid.isascii() and lid.isdigit():
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
        for num in _numbers(local_id):
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
