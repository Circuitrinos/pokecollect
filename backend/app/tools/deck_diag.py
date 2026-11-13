"""
Deck diagnostics: why a deck's lines are "not legal", "not in the catalog" or
in the wrong section, and where its cards came from.

Inside the api container (reads the database with the app's own code, changes
nothing):

    python -m app.tools.deck_diag 4            # deck 4
    python -m app.tools.deck_diag 4 7 --live   # also ask TCGdex (reachability, card texts)
    python - 4 --live < deck_diag.py           # the same without the file in the image

Sections per deck:
  1. deck, format, the Standard cut-off mark, the legality verdict and its issues
  2. every list line: catalog print, kind (Pokémon/Trainer/Energy), mark, status,
     and for lines without a print what a fresh match by name/number would find
  3. reprint rule for every "not legal"/"unknown" Trainer/Energy line: all
     prints of that name in the catalog with mark and text fingerprint
  4. the cards in the deck: catalog id (and whether the catalog knows it), how
     they were added (scan drafts that saved them: what the scan had matched)
  5. --live: TCGdex reachable from here? the texts of the prints behind section 3
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Optional

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models.card import PokemonCard
from app.models.deck import Deck, DeckSlot
from app.models.scan_draft import ScanDraft
from app.models.tcgdex_catalog import TcgdexCatalog
from app.services import decks as svc
from app.services import regulation


def short(v, n: int = 14) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def kind(row: Optional[TcgdexCatalog]) -> str:
    return (regulation._kind(row.category) or "?") if row is not None else "-"


def table(rows: list[list], head: list[str]) -> str:
    cells = [[str("" if c is None else c) for c in r] for r in [head] + rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(head))]
    return "\n".join("  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in cells)


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 70 - len(title)))


def prints_of(db, name: str) -> list[TcgdexCatalog]:
    """Every western print whose name is the same by the rules (accents, ’ and a
    “(Ghetsis)”-style suffix don't count) — wider than the reprint rule's own lookup."""
    want = svc.rule_name(name)
    first = want.split()[0].split("'")[0] if want else ""
    col = func.lower(func.coalesce(TcgdexCatalog.name_en, TcgdexCatalog.name))
    rows = db.scalars(select(TcgdexCatalog).where(TcgdexCatalog.region == "west", col.like(f"{first}%"))
                      .order_by(TcgdexCatalog.regulation_mark.desc().nullslast(), TcgdexCatalog.card_id)).all()
    return [r for r in rows if svc.rule_name(r.name_en or r.name or "") == want]


def drafts_by_card(db) -> dict[int, tuple[int, dict]]:
    """card id → (draft id, the draft's candidate that saved it)."""
    out: dict[int, tuple[int, dict]] = {}
    for d in db.scalars(select(ScanDraft)).all():
        try:
            st = json.loads(d.state)
        except (TypeError, ValueError):
            continue
        for c in st.get("candidates", []) if isinstance(st, dict) else []:
            saved = c.get("saved") if isinstance(c, dict) else None
            for cid in (saved or {}).get("ids", []) if isinstance(saved, dict) else []:
                if isinstance(cid, int):
                    out[cid] = (d.id, {**c, "_mode": st.get("mode"), "_deck": st.get("deckId")})
    return out


def diagnose(db, deck_id: int, live: bool, drafts: dict[int, tuple[int, dict]], want_texts: set[str]) -> None:
    deck = db.get(Deck, deck_id)
    if deck is None:
        print(f"\n### deck {deck_id}: not found")
        return
    slots = svc._slots(db, deck.id)
    cards = db.scalars(select(PokemonCard).where(PokemonCard.deck_id == deck.id).order_by(PokemonCard.id)).all()
    rows = svc._rows(db, [s.tcgdex_card_id for s in slots] + [c.tcgdex_card_id for c in cards])
    leg = svc.legality(db, deck, slots)
    cov = svc.coverage(db, deck, slots)

    print(f"\n### deck {deck.id} “{deck.name}” — format {deck.format}, owner user {deck.user_id}")
    print(f"  Standard: marks from {leg['min_mark']} on are legal (setting standard_min_mark)")
    print(f"  verdict: {leg['status']}, {leg['total']} cards, sections {leg['sections']}, need_data={leg['need_data']}")
    for i in leg["issues"]:
        print(f"  issue: {i['level']} {i['code']} count={i.get('count')} slots={i.get('slots')}"
              + (f" name={i['name']}" if i.get("name") else ""))

    section("1. list lines")
    out = []
    for s in slots:
        row = rows.get(s.tcgdex_card_id) if s.tcgdex_card_id else None
        st = leg["slots"].get(s.id, {})
        fresh = ""
        if row is None:
            r2, how = svc.match_card(db, s.name, s.set_code, s.number)
            fresh = f"{r2.card_id} ({how}, {kind(r2)})" if r2 is not None else "nothing"
        out.append([s.id, s.position, s.section, s.count, short(s.name, 26), s.set_code, s.number,
                    s.tcgdex_card_id or "—", "yes" if row is not None else ("NO" if s.tcgdex_card_id else "—"),
                    kind(row), row.regulation_mark if row is not None else "",
                    "y" if row is not None and row.regulation_checked else ("n" if row is not None else ""),
                    s.match or "", st.get("status"), st.get("legal_mark") or "", fresh])
    print(table(out, ["slot", "pos", "section", "n", "name", "set", "no.", "catalog id", "in cat", "kind",
                      "mark", "chk", "match", "status", "legal", "fresh match now"]))

    section("2. reprint rule (not legal / unknown Trainer and Energy lines)")
    keys = {k for r in rows.values() if r.regulation_checked and (k := regulation._reprint_key(r))}
    reprints = regulation._reprint_marks(db, keys)
    any_ = False
    for s in slots:
        row = rows.get(s.tcgdex_card_id) if s.tcgdex_card_id else None
        if leg["slots"].get(s.id, {}).get("status") not in ("not_legal", "unknown") or row is None:
            continue
        any_ = True
        key = regulation._reprint_key(row) if row.regulation_checked else None
        print(f"\n  slot {s.id} {s.name} — print {row.card_id}, mark {row.regulation_mark}, "
              f"checked {row.regulation_checked}, kind {kind(row)}")
        if key is None:
            print("    no reprint rule: " + ("not checked against TCGdex yet (regulation_checked = false)"
                                             if not row.regulation_checked else "Pokémon or basic energy"))
            continue
        print(f"    rule key {key}; own text fingerprint: {short(row.effect_key, 60) or '— (unknown)'}")
        print(f"    newest mark, any text: {reprints.get(key)}; with the same text: "
              f"{reprints.get(key + (row.effect_key,)) if row.effect_key else '— (own text unknown)'}")
        prints = prints_of(db, row.name_en or row.name or s.name)
        print(table([[p.card_id, short(p.name_en or p.name, 30),
                      "same" if regulation._reprint_key(p) == key else "OTHER",
                      p.regulation_mark or "", "y" if p.regulation_checked else "n", kind(p),
                      "SAME" if p.effect_key and p.effect_key == row.effect_key else
                      ("—" if not p.effect_key else "differs"), short(p.effect_key, 48)] for p in prints],
                    ["print", "name", "rule key", "mark", "chk", "kind", "text", "fingerprint"]))
        want_texts.add(row.card_id)
        newest = next((p for p in prints if p.regulation_mark), None)
        if newest is not None:
            want_texts.add(newest.card_id)
    if not any_:
        print("  (none)")

    section("3. cards in the deck")
    by_card = {c.id: (s.id, how) for s in slots for c, how in cov.by_slot.get(s.id, [])}
    out = []
    for c in cards:
        row = rows.get(c.tcgdex_card_id) if c.tcgdex_card_id else None
        slot, how = by_card.get(c.id, (None, "EXTRA"))
        dr = drafts.get(c.id)
        origin = f"draft {dr[0]} ({dr[1].get('_mode')})" if dr else ""
        fresh = ""
        if row is None:
            nm = c.englischer_name or c.kartenname or ""
            num = c.karten_nr.split("/")[0] if c.karten_nr else None
            r2, how2 = svc.match_card(db, nm, None, num)
            fresh = f"{r2.card_id} ({how2})" if r2 is not None else "nothing"
        out.append([c.id, short(c.kartenname, 22), short(c.englischer_name, 22), c.karten_nr, c.set_id,
                    c.tcgdex_card_id or "—", "yes" if row is not None else ("NO" if c.tcgdex_card_id else "—"),
                    kind(row), c.folierung, c.sprache, c.regulation_mark, c.legal_mark,
                    str(c.hinzugefuegt_am)[:16] if c.hinzugefuegt_am else "", slot, how, origin, fresh])
    print(table(out, ["card", "name", "name EN", "no.", "set", "catalog id", "in cat", "kind", "foil", "lang",
                      "mark", "legal", "added", "slot", "fits", "from", "fresh match now"]))

    section("4. how the scans had matched these cards (scan drafts)")
    seen = False
    for c in cards:
        dr = drafts.get(c.id)
        if not dr:
            continue
        seen = True
        cand = dr[1]
        m, raw = cand.get("match") or {}, cand.get("raw") or {}
        alts = [a.get("tcgdex_card_id") for a in (cand.get("alternatives") or [])[:3] if isinstance(a, dict)]
        print(f"  card {c.id} ← draft {dr[0]} (mode {cand.get('_mode')}, deck {cand.get('_deck')}): "
              f"matcher read {raw.get('tcgdex_card_id')!r} “{raw.get('name')}” no. {raw.get('number')!r}; "
              f"resolved match {m.get('tcgdex_card_id')!r} “{m.get('name')}”; uncertain {cand.get('uncertain')}; "
              f"needs_choice {cand.get('needs_choice')}; chosen {cand.get('chosenId')!r}; alternatives {alts}")
    if not seen:
        print("  (no scan draft left for these cards — drafts are removed once discarded)")


async def live_checks(card_ids: list[str]) -> None:
    from app.services import tcgdex

    section("5. TCGdex from this container (--live)")
    for cid in ["me01-114", *card_ids]:
        t0 = time.perf_counter()
        try:
            tc = await tcgdex.get_card(cid, "en")
            err = None
        except Exception as exc:  # noqa: BLE001 — diagnostics: show whatever happens
            tc, err = None, exc
        ms = round((time.perf_counter() - t0) * 1000)
        if tc is None:
            print(f"  {cid}: NOTHING after {ms} ms {err or '(see the TCGdex warnings in the api log)'}")
            continue
        print(f"  {cid}: {tc.name} — {tc.category}, mark {tc.regulationMark}, legal {tc.legal}, {ms} ms")
        if tc.effect:
            print(f"      text: {tc.effect!r}")
            print(f"      fingerprint: {regulation.effect_key(tc.effect)}")


def main(argv: list[str]) -> None:
    live = "--live" in argv
    ids = [int(a) for a in argv if a.isdigit()]
    db = SessionLocal()
    try:
        if not ids:
            ids = list(db.scalars(select(Deck.id).order_by(Deck.id)).all())
            print(f"no deck id given — all decks: {ids}")
        drafts = drafts_by_card(db)
        want: set[str] = set()
        for i in ids:
            diagnose(db, i, live, drafts, want)
        if live:
            asyncio.run(live_checks(sorted(want)))
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
