"""
Read-only access to PokéCollect's card catalog (tcgdex_catalog + pokemon_sets)
and the pokemontcg.io -> TCGdex id mapping for the converted base index.
"""
from __future__ import annotations

import collections
import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass

import psycopg2

log = logging.getLogger(__name__)


@dataclass
class CatalogCard:
    card_id: str
    region: str
    set_id: str | None
    set_name_en: str | None
    set_name: str | None
    local_id: str | None
    name: str | None
    name_en: str | None
    image_url: str | None
    set_official: int | None


_CATALOG_SQL = """
SELECT c.card_id, c.region, c.set_id, s.name_en, c.set_name, c.local_id,
       c.name, c.name_en, c.image_url, s.card_count_official
FROM tcgdex_catalog c
LEFT JOIN pokemon_sets s ON s.set_id = c.set_id
"""


def load_catalog(database_url: str) -> dict[str, CatalogCard]:
    with psycopg2.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(_CATALOG_SQL)
        rows = cur.fetchall()
    out = {r[0]: CatalogCard(*r) for r in rows}
    log.info("Catalog: %d cards", len(out))
    return out


# ── normalisation ────────────────────────────────────────────────────────────

def norm_text(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


_NUM_RE = re.compile(r"([A-Z]*)0*(\d+)([A-Z]*)")


def num_key(s: str | None) -> str:
    """'TG05' -> 'TG5', '039' -> '39', 'SV001' -> 'SV1'; anything else uppercased."""
    if not s:
        return ""
    s = str(s).strip().upper()
    m = _NUM_RE.fullmatch(s)
    return f"{m.group(1)}{int(m.group(2))}{m.group(3)}" if m else s


_SUBSET_SUFFIXES = (" trainer gallery", " galarian gallery", " shiny vault")


@dataclass
class MappingReport:
    mapped: int
    unmapped: int
    by_rule: dict
    unmapped_sets: list  # [(set name, count)]

    def as_dict(self) -> dict:
        return {"mapped": self.mapped, "unmapped": self.unmapped, "by_rule": self.by_rule,
                "unmapped_sets": self.unmapped_sets[:25]}


def map_pokemontcg(metas: list[dict], catalog: dict[str, CatalogCard]) -> tuple[list[str | None], MappingReport]:
    west = [c for c in catalog.values() if c.region == "west"]
    by_set_num: dict[tuple[str, str], str] = {}
    by_name_num: dict[tuple[str, str], list[CatalogCard]] = collections.defaultdict(list)
    for c in west:
        k = num_key(c.local_id)
        if c.set_name_en:
            by_set_num.setdefault((norm_text(c.set_name_en), k), c.card_id)
        if c.name_en or c.name:
            by_name_num[(norm_text(c.name_en or c.name), k)].append(c)

    out: list[str | None] = [None] * len(metas)
    rules = collections.Counter()

    # Pass 1: exact set + number (gallery subsets fold into their parent set).
    for i, m in enumerate(metas):
        sname, k = norm_text(m.get("set")), num_key(m.get("number"))
        cid = by_set_num.get((sname, k))
        rule = "set+number"
        if cid is None:
            for suf in _SUBSET_SUFFIXES:
                if sname.endswith(suf):
                    cid, rule = by_set_num.get((sname[: -len(suf)].strip(), k)), "subset->parent"
                    break
        if cid:
            out[i] = cid
            rules[rule] += 1

    # Pass 2: card name + number for renamed sets, choosing only among catalog
    # cards that pass 1 did not already claim (e.g. "Base" -> "Base Set" once
    # "Base Set 2" #4 is taken by its own exact match).
    claimed = {c for c in out if c}
    missing = collections.Counter()
    for i, m in enumerate(metas):
        if out[i]:
            continue
        sname, k = norm_text(m.get("set")), num_key(m.get("number"))
        cands = [c for c in by_name_num.get((norm_text(m.get("name")), k), []) if c.card_id not in claimed]
        cid = None
        if len(cands) == 1:
            cid, rule = cands[0].card_id, "name+number"
        elif cands:
            scored = sorted(
                ((difflib.SequenceMatcher(None, sname, norm_text(c.set_name_en)).ratio(), c)
                 for c in cands), key=lambda t: t[0], reverse=True)
            best, second = scored[0], scored[1]
            if best[0] >= 0.6 and best[0] - second[0] >= 0.1:
                cid, rule = best[1].card_id, "name+number+set~"
        if cid:
            out[i] = cid
            claimed.add(cid)
            rules[rule] += 1
        else:
            missing[m.get("set") or "?"] += 1

    rep = MappingReport(mapped=sum(rules.values()), unmapped=sum(missing.values()),
                        by_rule=dict(rules), unmapped_sets=missing.most_common())
    log.info("Mapping: %d mapped %s, %d unmapped", rep.mapped, dict(rules), rep.unmapped)
    return out, rep
