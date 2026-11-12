#!/usr/bin/env python3
"""
How well does the Play! stamp template work on your own cards?

  python stamp_eval.py pokecollect-scans-<date>.zip [--out stamp_eval]

Input: the ZIP from Settings → Scan dataset (or its unpacked folder).

Every hand-marked stamp is held out once: the template is learned from all the
others (exactly as "Learn template" does) and has to find the held-out stamp —
in the right place. Every card without Prize Pack is checked with the template
learned from all stamps: a hit there is a false alarm. The table shows both
for each score, and the two thresholds the scan uses are suggested:

  STAMP_SURE_SCORE  Prize Pack is ticked by itself (no false alarm at all)
  STAMP_MIN_SCORE   "stamp?" hint to check the card (false alarms ≤ 1 %)

Set them in the matcher's environment (no rebuild). The cards without Prize
Pack that score highest are listed: some may be Prize Pack cards nobody ticked.
--out writes those and the missed stamps as pictures (box drawn) to look at.

Needs opencv-python-headless and numpy and the matcher's app/ next to tools/
(both in the matcher image: put the ZIP in /data and run
docker exec -it <matcher> python /app/tools/stamp_eval.py /data/<zip> --out /data/stamp_eval).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import stamp  # noqa: E402

GRID = [round(x, 2) for x in np.arange(0.95, 0.495, -0.05)]


class Source:
    def __init__(self, path: Path):
        self.dir = path if path.is_dir() else None
        self.zip = None if self.dir else zipfile.ZipFile(path)

    def text(self, name: str) -> str:
        return (self.dir / name).read_text("utf-8") if self.dir else self.zip.read(name).decode("utf-8")

    def image(self, name: str) -> np.ndarray | None:
        data = (self.dir / name).read_bytes() if self.dir else self.zip.read(name)
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def box_of(quad) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    return min(xs), min(ys), max(xs), max(ys)


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def build_template(samples, path: Path) -> bool:
    built = stamp.build(samples)
    if built is None:
        return False
    stamp.save(built[0], built[1], path)
    return True


def picture(img: np.ndarray, boxes: list[tuple[tuple, tuple]], label: str) -> np.ndarray:
    out = cv2.resize(img, (stamp.W, stamp.H))
    for (x0, y0, x1, y1), colour in boxes:
        cv2.rectangle(out, (int(x0 * stamp.W), int(y0 * stamp.H)), (int(x1 * stamp.W), int(y1 * stamp.H)), colour, 2)
    cv2.putText(out, label, (6, stamp.H - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset")
    ap.add_argument("--out", help="folder for pictures of misses and false alarms")
    ap.add_argument("--max-negatives", type=int, default=0, help="check only this many cards without Prize Pack")
    a = ap.parse_args()
    src = Source(Path(a.dataset))
    labels = [json.loads(x) for x in src.text("labels.jsonl").splitlines() if x.strip()]
    pos = [x for x in labels if x.get("prize_pack") and x.get("stamp_quad")]
    neg = [x for x in labels if not x.get("prize_pack")]
    if a.max_negatives:
        neg = neg[:a.max_negatives]
    if len(pos) < 4:
        sys.exit(f"only {len(pos)} marked stamps — mark more (card → Play! stamp) and export again")
    imgs = {x["id"]: src.image(x["crop"]) for x in pos + neg}
    pos = [x for x in pos if imgs[x["id"]] is not None]
    neg = [x for x in neg if imgs[x["id"]] is not None]
    print(f"{len(pos)} marked stamps ({len({x['card_id'] for x in pos})} cards), {len(neg)} cards without Prize Pack")

    tmp = Path(tempfile.mkdtemp())
    samples = {x["id"]: (imgs[x["id"]], box_of(x["stamp_quad"]), True) for x in pos}
    held: list[tuple[dict, float, bool, list]] = []         # stamp, score, right place, found quad
    for i, x in enumerate(pos):
        path = tmp / f"loo_{x['id']}.png"
        if not build_template([s for k, s in samples.items() if k != x["id"]], path):
            continue
        r = stamp.find(imgs[x["id"]], path=path)
        if r is None:
            held.append((x, 0.0, False, None))
        else:
            held.append((x, r["score"], iou(box_of(r["quad"]), box_of(x["stamp_quad"])) >= 0.3, r["quad"]))
        if i % 5 == 4 or i + 1 == len(pos):
            print(f"\r  held out {i + 1}/{len(pos)}", end="", flush=True)
    print()
    full = tmp / "all.png"
    build_template(list(samples.values()), full)
    alarms: list[tuple[dict, float, list]] = []
    for i, x in enumerate(neg):
        r = stamp.find(imgs[x["id"]], path=full)
        alarms.append((x, r["score"] if r else 0.0, r["quad"] if r else None))
        if i % 25 == 0:
            print(f"\r  checked {i + 1}/{len(neg)} cards without Prize Pack", end="", flush=True)
    print("\n")

    found = lambda t: sum(1 for _, s, ok, _ in held if s >= t and ok)   # noqa: E731
    false = lambda t: sum(1 for _, s, _ in alarms if s >= t)            # noqa: E731
    print(f"  {'score ≥':>8}  {'stamps found':>14}  {'false alarms':>14}")
    for t in GRID:
        print(f"  {t:>8.2f}  {found(t):>5}/{len(held):<4} {found(t) / max(1, len(held)):>4.0%}"
              f"  {false(t):>5}/{len(alarms):<4} {false(t) / max(1, len(alarms)):>4.1%}")

    # ticking by itself: clear of the highest-scoring card without a stamp (with room to
    # spare — new cards score a little differently); the hint may catch ~1 % false alarms
    fine = [round(x, 2) for x in np.arange(0.99, 0.49, -0.01)]
    top_neg = max((s for _, s, _ in alarms), default=0.0)
    sure = next((t for t in reversed(fine) if t >= max(0.7, top_neg + 0.05)), None)
    hint = next((t for t in reversed(fine) if false(t) <= 0.01 * len(alarms) and t >= 0.6), None)
    if hint is not None and sure is not None:
        hint = min(hint, sure)
    print()
    if sure is not None:
        print(f"  STAMP_SURE_SCORE={sure:.2f}   ticks Prize Pack for {found(sure)}/{len(held)} stamps, "
              f"{false(sure)} false alarm(s) on {len(alarms)} cards (highest without a stamp: {top_neg:.2f})")
    else:
        print(f"  STAMP_SURE_SCORE: cards without a stamp score up to {top_neg:.2f} — keep ticking by hand")
    if hint is not None:
        print(f"  STAMP_MIN_SCORE={hint:.2f}    hints at {found(hint)}/{len(held)} stamps, "
              f"{false(hint)} false alarm(s) ({false(hint) / max(1, len(alarms)):.1%})")
    print(f"  now: STAMP_SURE_SCORE={stamp.SURE_SCORE:.2f} → {found(stamp.SURE_SCORE)}/{len(held)} found, "
          f"{false(stamp.SURE_SCORE)} false · STAMP_MIN_SCORE={stamp.MIN_SCORE:.2f} → "
          f"{found(stamp.MIN_SCORE)}/{len(held)}, {false(stamp.MIN_SCORE)} false")

    missed = sorted((h for h in held if not (h[1] >= (hint or stamp.MIN_SCORE) and h[2])), key=lambda h: h[1])
    if missed:
        print(f"\n  stamps it misses ({len(missed)}):")
        for x, s, ok, _ in missed:
            side = "left" if box_of(x["stamp_quad"])[0] < 0.5 else "right"
            print(f"    {x['id']:>7}  {s:.2f}{'' if ok else ' wrong place'}  {side:5}  {x.get('name')} ({x.get('set')})")
    top = sorted(alarms, key=lambda h: -h[1])[:12]
    print("\n  cards without Prize Pack that look most like a stamp (some may be Prize Pack cards):")
    for x, s, _ in top:
        print(f"    {x['id']:>7}  {s:.2f}  {x.get('name')} ({x.get('set')})")

    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for x, s, ok, q in missed:
            boxes = [(box_of(x["stamp_quad"]), (0, 200, 0))] + ([(box_of(q), (0, 0, 255))] if q else [])
            cv2.imwrite(str(out / f"missed_{x['id']}.jpg"), picture(imgs[x["id"]], boxes, f"stamp {s:.2f}"))
        for x, s, q in top:
            if q:
                cv2.imwrite(str(out / f"alarm_{s:.2f}_{x['id']}.jpg"),
                            picture(imgs[x["id"]], [(box_of(q), (0, 0, 255))], f"no Prize Pack {s:.2f}"))
        print(f"\n  pictures in {out}/ (green: your mark, red: what the template found)")


if __name__ == "__main__":
    main()
