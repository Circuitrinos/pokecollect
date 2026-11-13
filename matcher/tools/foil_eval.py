#!/usr/bin/env python3
"""
Can the foil difference tell Reverse Holo / Holo / Normal apart — on cards it
has never seen?

  python holo_diff.py pokecollect-scans-<date>.zip --out holo_diff --pairs
  python foil_eval.py holo_diff [--save foil_model.json] [--precision 0.8]

Reads holo_diff's index.csv (one row per scanned card: how much the photo
differs from the plain catalog image inside the art box and in the frame
around it). Reverse holos light up the frame, holos the art, plain cards
neither — if the photo caught the light.

  1. each number on its own: how well it separates two foilings (AUC,
     best threshold, balanced accuracy)
  2. a small model on all of them (logistic regression), always tested on
     cards it did not learn from (5 folds by card)
  3. your own pairs: the same card as Normal and as Reverse Holo — does the
     reverse holo light up more?

The verdict says whether a simple rule is enough for a suggestion in the scan,
or whether it takes a model trained on the difference images themselves.

--save writes the model trained on all your cards, set to ask "reverse holo?"
only where --precision of those questions (out-of-fold) were right. Upload it
in the app (Settings → Admin → Play! stamps & foil → Reverse holo model): the
scan then asks for plain cards whose print exists as reverse holo.
Needs numpy; with --save also the matcher's app/ next to tools/ (for the
feature definition shared with the scan).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np

FOLDS = 5
CLASSES = ["Normal", "Holo", "Reverse Holo"]


def load(path: Path) -> list[dict]:
    f = path / "index.csv" if path.is_dir() else path
    with open(f, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        try:
            art, frame = float(r["art_diff"]), float(r["frame_diff"])
        except (TypeError, ValueError):
            continue
        r["art"], r["frame"] = art, frame
        r["ratio"] = frame / art if art > 0 else np.nan
        try:   # set code box vs catalog (newer holo_diff): ≈ +1 same print, negative = inverted
            r["code"] = float(r["code_corr"]) if r.get("code_corr") not in (None, "") else None
        except ValueError:
            r["code"] = None
        r["inl"] = float(r.get("inliers") or 0)
        out.append(r)
    return out


try:   # the scan computes the same numbers (app/foil.py); standalone copy otherwise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.foil import FEATURES, feature_values  # noqa: E402
except ImportError:
    FEATURES = ["log_art", "log_frame", "log_frame_art", "code_corr"]

    def feature_values(art: float, frame: float, code: float | None = None) -> dict[str, float]:
        a, f = max(float(art), 0.1), max(float(frame), 0.1)
        return {"log_art": float(np.log(a)), "log_frame": float(np.log(f)), "log_frame_art": float(np.log(f / a)),
                "code_corr": 1.0 if code is None else float(code)}

USE = list(FEATURES)     # main() drops code_corr for an index.csv from an older holo_diff


def features(r: dict) -> list[float]:
    v = feature_values(r["art"], r["frame"], r.get("code"))
    return [v[n] for n in USE]


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(score of a positive > score of a negative) — 0.5 is chance, 1.0 perfect."""
    if not len(pos) or not len(neg):
        return float("nan")
    allv = np.concatenate([pos, neg])
    ranks = allv.argsort().argsort().astype(float) + 1
    # ties: average ranks
    for v in np.unique(allv):
        m = allv == v
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def best_threshold(pos: np.ndarray, neg: np.ndarray) -> tuple[float, float]:
    """Threshold (pos above) with the best balanced accuracy → (threshold, balanced accuracy)."""
    best = (float("nan"), 0.0)
    for t in np.unique(np.concatenate([pos, neg])):
        bacc = ((pos >= t).mean() + (neg < t).mean()) / 2
        if bacc > best[1]:
            best = (float(t), float(bacc))
    return best


def fold_of(card_id: str) -> int:
    return zlib.crc32((card_id or "").encode()) % FOLDS


def fit_logreg(x: np.ndarray, y: np.ndarray, k: int, l2: float = 1e-2, steps: int = 3000) -> np.ndarray:
    """Multinomial logistic regression (balanced class weights), plain gradient descent."""
    xb = np.hstack([x, np.ones((len(x), 1))])
    w = np.zeros((xb.shape[1], k))
    cw = np.array([len(y) / (k * max(1, (y == c).sum())) for c in range(k)])[y]
    onehot = np.eye(k)[y]
    for _ in range(steps):
        z = xb @ w
        p = np.exp(z - z.max(1, keepdims=True))
        p /= p.sum(1, keepdims=True)
        grad = xb.T @ ((p - onehot) * cw[:, None]) / len(y) + l2 * np.vstack([w[:-1], np.zeros((1, k))])
        w -= 0.5 * grad
    return w


def predict(w: np.ndarray, x: np.ndarray) -> np.ndarray:
    return (np.hstack([x, np.ones((len(x), 1))]) @ w).argmax(1)


def cross_validate(rows: list[dict], classes: list[str]) -> np.ndarray | None:
    """Confusion matrix (true × predicted) from 5 folds split by card."""
    data = [r for r in rows if r["foiling"] in classes]
    if any(sum(1 for r in data if r["foiling"] == c) < 10 for c in classes):
        return None
    x = np.array([features(r) for r in data])
    y = np.array([classes.index(r["foiling"]) for r in data])
    folds = np.array([fold_of(r["card_id"]) for r in data])
    conf = np.zeros((len(classes), len(classes)), int)
    for f in range(FOLDS):
        tr, te = folds != f, folds == f
        if not te.any() or len(set(y[tr])) < len(classes):
            continue
        mu, sd = x[tr].mean(0), x[tr].std(0) + 1e-9
        w = fit_logreg((x[tr] - mu) / sd, y[tr], len(classes))
        for t, p in zip(y[te], predict(w, (x[te] - mu) / sd), strict=True):
            conf[t, p] += 1
    return conf


def oof_scores(rows: list[dict], pos: str, neg: str) -> tuple[np.ndarray, np.ndarray] | None:
    """Out-of-fold model score for `pos` (5 folds by card) → (scores, is_pos)."""
    data = [r for r in rows if r["foiling"] in (pos, neg)]
    if sum(1 for r in data if r["foiling"] == pos) < 10:
        return None
    x = np.array([features(r) for r in data])
    y = np.array([1 if r["foiling"] == pos else 0 for r in data])
    folds = np.array([fold_of(r["card_id"]) for r in data])
    score = np.full(len(y), np.nan)
    for f in range(FOLDS):
        tr, te = folds != f, folds == f
        if not te.any() or len(set(y[tr])) < 2:
            continue
        mu, sd = x[tr].mean(0), x[tr].std(0) + 1e-9
        w = fit_logreg((x[tr] - mu) / sd, y[tr], 2)
        z = np.hstack([(x[te] - mu) / sd, np.ones((te.sum(), 1))]) @ w
        score[te] = z[:, 1] - z[:, 0]
    keep = np.isfinite(score)
    return score[keep], y[keep].astype(bool)


def recall_at_precision(score: np.ndarray, is_pos: np.ndarray, want: float) -> tuple[float, int]:
    """Strictest-first: the most reverse holos caught while `want` of the flagged ones are right."""
    order = np.argsort(-score)
    hits = np.cumsum(is_pos[order])
    flagged = np.arange(1, len(order) + 1)
    ok = np.where(hits / flagged >= want)[0]
    if not len(ok):
        return 0.0, 0
    i = ok.max()
    return float(hits[i] / max(1, is_pos.sum())), int(flagged[i])


def balanced(conf: np.ndarray) -> float:
    return float(np.mean([conf[i, i] / conf[i].sum() for i in range(len(conf)) if conf[i].sum()]))


def show_conf(conf: np.ndarray, classes: list[str]) -> None:
    w = max(len(c) for c in classes) + 2
    print("    " + " " * w + "".join(f"{c[:12]:>14}" for c in classes) + "   (rows: label, columns: predicted)")
    for i, c in enumerate(classes):
        print(f"    {c:<{w}}" + "".join(f"{conf[i, j]:>14}" for j in range(len(classes)))
              + f"   {conf[i, i] / max(1, conf[i].sum()):.0%} right")


def save_model(cat: list[dict], path: Path, precision: float) -> None:
    """Normal vs Reverse Holo on all cards; ask only above the score where `precision` of the asks were right."""
    data = [r for r in cat if r["foiling"] in ("Normal", "Reverse Holo")]
    x = np.array([features(r) for r in data])
    y = np.array([1 if r["foiling"] == "Reverse Holo" else 0 for r in data])
    mu, sd = x.mean(0), x.std(0) + 1e-9
    w = fit_logreg((x - mu) / sd, y, 2)
    oof = oof_scores(cat, "Reverse Holo", "Normal")
    if oof is None:
        sys.exit("too few reverse holos to save a model")
    score, is_pos = oof
    order = np.argsort(-score)
    hits = np.cumsum(is_pos[order])
    ok = np.where(hits / np.arange(1, len(order) + 1) >= precision)[0]
    if not len(ok):
        sys.exit(f"no threshold reaches {precision:.0%} precision — nothing saved")
    i = ok.max()
    model = {"version": 1, "kind": "reverse_holo_vs_normal", "features": USE,
             "mu": mu.round(5).tolist(), "sd": sd.round(5).tolist(), "w": w.round(5).tolist(),
             "threshold": round(float(score[order[i]]), 4), "precision": round(float(hits[i] / (i + 1)), 3),
             "recall": round(float(hits[i] / is_pos.sum()), 3),
             "trained_on": {"Normal": int((y == 0).sum()), "Reverse Holo": int((y == 1).sum())}}
    path.write_text(json.dumps(model, indent=1))
    print(f"\nSaved {path}: asks \"reverse holo?\" above {model['threshold']:.2f} — on unseen cards "
          f"{model['precision']:.0%} of the asks were right and {model['recall']:.0%} of reverse holos were asked.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("holo_diff", help="holo_diff.py's --out folder (or its index.csv)")
    ap.add_argument("--save", type=Path, help="write the model for the scan (foil_model.json)")
    ap.add_argument("--precision", type=float, default=0.8, help="share of right asks the model aims for")
    args = ap.parse_args()
    rows = load(Path(args.holo_diff))
    if not any(r.get("code") is not None for r in rows):
        USE.remove("code_corr")
        print("(no set-code column — run the new holo_diff.py again to use the inverted set code box)")
    cat = [r for r in rows if r["kind"] == "vs_catalog"]
    counts = defaultdict(int)
    for r in cat:
        counts[r["foiling"] or "(not set)"] += 1
    print(f"\n{len(cat)} photos compared with the catalog image: "
          + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) + "\n")

    def vals(foil, key):
        return np.array([r[key] for r in cat if r["foiling"] == foil and np.isfinite(r[key])])

    print("1. One number at a time (AUC 0.5 = chance, 1.0 = perfect; balanced accuracy at the best threshold)")
    tests = [("Reverse Holo", "Normal", "ratio", "frame/art"), ("Reverse Holo", "Normal", "frame", "frame"),
             ("Reverse Holo", "Holo", "ratio", "frame/art"), ("Holo", "Normal", "art", "art")]
    verdict_rule = None
    for a, b, key, label in tests:
        pa, pb = vals(a, key), vals(b, key)
        if len(pa) < 5 or len(pb) < 5:
            continue
        t, bacc = best_threshold(pa, pb)
        print(f"   {a:>12} vs {b:<7} by {label:<9}  AUC {auc(pa, pb):.2f}   above {t:.2f}: {bacc:.0%}   "
              f"(medians {np.median(pa):.2f} vs {np.median(pb):.2f})")
        if (a, b, key) == ("Reverse Holo", "Normal", "ratio"):
            verdict_rule = bacc
    # set code box: inverted (negative) on foil prints of newer sets — lower = foil
    pa = np.array([-r["code"] for r in cat if r["foiling"] == "Reverse Holo" and r.get("code") is not None])
    pb = np.array([-r["code"] for r in cat if r["foiling"] == "Normal" and r.get("code") is not None])
    if len(pa) >= 5 and len(pb) >= 5:
        t, bacc = best_threshold(pa, pb)
        print(f"   {'Reverse Holo':>12} vs {'Normal':<7} by set code  AUC {auc(pa, pb):.2f}   below {-t:.2f}: {bacc:.0%}   "
              f"(medians {-np.median(pa):.2f} vs {-np.median(pb):.2f}; ≈ −1 = box inverted)")
        sets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for r in cat:
            if r.get("code") is not None and r["foiling"] in ("Normal", "Holo", "Reverse Holo"):
                sets[(r["card_id"] or "?").rsplit("-", 1)[0]][r["foiling"]].append(r["code"])
        inv = [(sid, v) for sid, v in sets.items() if len(v.get("Reverse Holo", [])) >= 2 and len(v.get("Normal", [])) >= 2]
        if inv:
            print("   set code by set (median; foil prints of sets that invert the box go negative):")
            for sid, v in sorted(inv, key=lambda kv: np.median(kv[1]["Reverse Holo"])):
                cells = "  ".join(f"{f} {np.median(v[f]):+.2f} (n={len(v[f])})" for f in ("Normal", "Holo", "Reverse Holo") if v.get(f))
                print(f"     {sid:<10} {cells}")

    print("\n2. Small model on all numbers, tested on unseen cards")
    two = cross_validate(cat, ["Normal", "Reverse Holo"])
    if two is not None:
        print(f"   Normal vs Reverse Holo: {balanced(two):.0%} balanced accuracy")
        show_conf(two, ["Normal", "Reverse Holo"])
    three = cross_validate(cat, CLASSES)
    if three is not None:
        print(f"   Normal / Holo / Reverse Holo: {balanced(three):.0%} balanced accuracy")
        show_conf(three, CLASSES)

    print("\n3. Your pairs: the same card as Normal and as Reverse Holo")
    by_card = defaultdict(lambda: defaultdict(list))
    for r in cat:
        by_card[r["card_id"]][r["foiling"]].append(r["ratio"])
    pairs = [(np.nanmedian(v["Reverse Holo"]), np.nanmedian(v["Normal"]))
             for v in by_card.values() if v.get("Reverse Holo") and v.get("Normal")]
    if pairs:
        higher = sum(1 for rh, nm in pairs if rh > nm)
        print(f"   reverse holo lights up the frame more in {higher}/{len(pairs)} pairs "
              f"(median frame/art {np.median([p[0] for p in pairs]):.2f} vs {np.median([p[1] for p in pairs]):.2f})")
    direct = [r for r in rows if r["kind"] == "vs_plain_photo" and r["foiling"] == "Reverse Holo"]
    if direct:
        print(f"   photo vs your plain photo of the same card: {len(direct)} reverse holos, "
              f"median frame/art {np.median([r['ratio'] for r in direct]):.2f}")

    score = balanced(two) if two is not None else verdict_rule
    print("\nVerdict (Normal vs Reverse Holo on unseen cards)")
    # what a suggestion would feel like: how many "reverse holo?" hints would be right
    precision = two[1, 1] / two[:, 1].sum() if two is not None and two[:, 1].sum() else None
    if precision is not None:
        print(f"   of the cards it would call Reverse Holo, {precision:.0%} are "
              f"({two[0, 1]} plain cards flagged, {two[1, 0]} reverse holos missed)")
    oof = oof_scores(cat, "Reverse Holo", "Normal")
    rec80 = 0.0
    if oof is not None:
        for want in (0.9, 0.8, 0.6):
            rec, _ = recall_at_precision(*oof, want)
            rec80 = rec if want == 0.8 else rec80
            print(f"   flagging only the surest: {want:.0%} of the flags right → catches {rec:.0%} of reverse holos")
    if score is None:
        print("   not enough rows — run holo_diff.py on the scan dataset first")
    elif score >= 0.9 and (precision or 0) >= 0.85:
        print(f"   {score:.0%}: a rule on these numbers is good enough to suggest Reverse Holo in the scan")
    elif rec80 >= 0.4:
        print(f"   {score:.0%} overall: worth a careful hint (\"reverse holo?\") on the surest cards only — "
              f"it would catch about {rec80:.0%} of reverse holos with 4 in 5 hints right")
    else:
        print(f"   {score:.0%}: these numbers are not enough (too many plain cards would be flagged) — the foil "
              "has to be learned from the "
              "difference images (holo_cluster.py features or a small CNN), with more photos under varied light")
    if args.save:
        save_model(cat, args.save, args.precision)


if __name__ == "__main__":
    main()
