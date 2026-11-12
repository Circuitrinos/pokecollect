#!/usr/bin/env python3
"""
Is there enough in a PokéCollect scan dataset to train something? Counts what
each training job can use and says how far it is from a useful size.

  python dataset_report.py pokecollect-scans-<date>.zip

Input: the ZIP from Settings → Scan dataset (or its unpacked folder).
Only the Python standard library is needed.

The thresholds are rules of thumb for fine-tuning, not guarantees: variety
(backgrounds, lighting, sleeves, binders, sets) matters as much as the count,
and whatever you train needs photos it has never seen to be judged on.
"""
from __future__ import annotations

import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

# (enough to try, enough) — see the notes printed with each section
DETECT_PHOTOS = (150, 500)          # fully labelled photos
DETECT_CARDS = (500, 2000)          # card outlines in them
STAMP_POS = (60, 200)               # crops with a marked Play! stamp
STAMP_NEG = (300, 1000)             # crops certainly without one
FOIL_PER_CLASS = (100, 300)         # crops per foiling class
FOIL_CARDS = (50, 150)              # distinct cards per class (not 300 photos of one card)


def read(src: Path) -> tuple[list[dict], list[dict]]:
    def lines(text: str) -> list[dict]:
        return [json.loads(x) for x in text.splitlines() if x.strip()]
    if src.is_dir():
        labels = lines((src / "labels.jsonl").read_text("utf-8"))
        p = src / "photos.jsonl"
        photos = lines(p.read_text("utf-8")) if p.exists() else []
        return labels, photos
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
        labels = lines(z.read("labels.jsonl").decode("utf-8"))
        photos = lines(z.read("photos.jsonl").decode("utf-8")) if "photos.jsonl" in names else []
    return labels, photos


def verdict(n: int, bounds: tuple[int, int]) -> str:
    lo, hi = bounds
    if n >= hi:
        return "enough"
    if n >= lo:
        return f"enough to try ({hi - n} more for a solid set)"
    return f"not yet ({lo - n} more to try)"


def row(label: str, value, note: str = "") -> None:
    print(f"  {label:<38}{value!s:>8}   {note}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    labels, photos = read(Path(sys.argv[1]))
    by_id = {x["id"]: x for x in labels}
    clean = [x for x in labels if (x.get("check") or {}).get("status") != "low"]
    print(f"\n{len(labels)} card photos ({len(labels) - len(clean)} with a weak scan check), "
          f"{len(photos)} whole photos, {len({x['card_id'] for x in labels if x.get('card_id')})} distinct cards\n")

    # ── card detector ──────────────────────────────────────────────────────
    print("Card detector (originals/*.txt, detect.yaml — YOLO OBB / segmentation)")
    labelled = [p for p in photos if p.get("labeled")]
    cards = sum(len(p["cards"]) for p in labelled)
    sizes = Counter("1" if len(p["cards"]) == 1 else "2–8" if len(p["cards"]) <= 8 else "9+" for p in labelled)
    row("fully labelled photos", len(labelled), verdict(len(labelled), DETECT_PHOTOS))
    row("card outlines in them", cards, verdict(cards, DETECT_CARDS))
    row("  photos with 1 card", sizes["1"])
    row("  photos with 2–8 cards", sizes["2–8"])
    row("  photos with 9+ cards (binder pages)", sizes["9+"])
    row("photos missing an outline (left out)", len(photos) - len(labelled))
    print("  Note: a photo lists only the cards saved from it. A card discarded in the review is in")
    print("  the picture without an outline and teaches the detector to ignore cards — look through")
    print("  the multi-card photos (or drop those whose count looks short) before training.")
    print("  Fine-tuning the PokemonScanner YOLO weights needs fewer real photos than starting fresh.\n")

    # ── Play! stamp ────────────────────────────────────────────────────────
    print("Play! stamp (crops/*.txt, stamp.yaml — YOLO detect on straightened crops)")
    pp = [x for x in labels if x.get("prize_pack")]
    marked = [x for x in pp if x.get("stamp_quad")]
    neg = [x for x in labels if not x.get("prize_pack")]
    row("Prize Pack crops", len(pp))
    row("  with the stamp marked (positives)", len(marked), verdict(len(marked), STAMP_POS))
    row("  not marked yet (left out)", len(pp) - len(marked), "mark them: card → Play! stamp")
    row("crops without Prize Pack (negatives)", len(neg), verdict(len(neg), STAMP_NEG))
    row("distinct cards among the positives", len({x["card_id"] for x in marked}))
    side = Counter("left" if sum(p[0] for p in x["stamp_quad"]) / 4 < 0.5 else "right" for x in marked)
    row("  stamp on the left / right", f"{side['left']}/{side['right']}")
    print("  The matcher's stamp template needs only a few marked stamps and already separates them;")
    print("  a learned detector pays off once it beats the template on stamps it has not seen.\n")

    # ── foiling ────────────────────────────────────────────────────────────
    print("Foiling / reverse holo (holo_diff.py → difference images per crop)")
    groups: dict[str, list[dict]] = defaultdict(list)
    for x in clean:
        groups[x.get("foiling") or "(not set)"].append(x)
    print(f"  {'foiling':<22}{'crops':>7}{'cards':>7}{'with ref':>10}   ")
    for f, xs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n_cards = len({x["card_id"] for x in xs})
        refs = sum(1 for x in xs if x.get("reference") or x.get("reference_url"))
        print(f"  {f:<22}{len(xs):>7}{n_cards:>7}{refs:>10}")
    patterns = Counter(x.get("pattern") for x in clean if x.get("foiling") == "Reverse Holo" and x.get("pattern"))
    if patterns:
        print("  reverse holo patterns: " + ", ".join(f"{k} {v}" for k, v in patterns.most_common()))
    per_card: dict[str, set] = defaultdict(set)
    for x in clean:
        if x.get("card_id") and x.get("foiling"):
            per_card[x["card_id"]].add(x["foiling"])
    pairs = sum(1 for s in per_card.values() if {"Normal", "Reverse Holo"} <= s)
    row("same card as Normal AND Reverse Holo", pairs, "best material: same print, only the foil differs")
    rh = groups.get("Reverse Holo", [])
    nm = groups.get("Normal", [])
    n_rh = min(len(rh), len({x["card_id"] for x in rh}) * 3)    # several photos of one card count less
    n_nm = min(len(nm), len({x["card_id"] for x in nm}) * 3)
    row("Reverse Holo vs Normal (usable)", f"{n_rh}/{n_nm}",
        verdict(min(n_rh, n_nm), FOIL_PER_CLASS))
    row("  distinct cards (fewer of the two)", min(len({x['card_id'] for x in rh}), len({x['card_id'] for x in nm})),
        verdict(min(len({x['card_id'] for x in rh}), len({x['card_id'] for x in nm})), FOIL_CARDS))
    print("  A foil only shows where the light catches it: photos of the same card under different")
    print("  light help more than more cards under the same lamp. Try holo_diff.py's frame/art ratio")
    print("  first — it needs no training and tells how far a simple rule gets.\n")

    bad = [i for i in (c["id"] for p in photos for c in p["cards"]) if i not in by_id]
    if bad:
        print(f"({len(bad)} cards in photos.jsonl have no label line — export from an older version?)")


if __name__ == "__main__":
    main()
