#!/usr/bin/env python3
"""
Can the foil difference tell Reverse Holo / Holo / Normal apart — on cards it
has never seen?

  python holo_diff.py pokecollect-scans-<date>.zip --out holo_diff --pairs
  python foil_eval.py holo_diff [--save foil_model.json] [--precision 0.8]
                                [--sheet labels_to_check.jpg] [--app-url https://…]

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
  4. labels to check: cards whose foiling label disagrees with the photo —
     "Normal" with an inverted set code box (or scored like a reverse holo by a
     model that never saw that card), "Reverse Holo" with the plain box, "Holo"
     with an inverted box. Written to labels_to_check.csv; --sheet draws each
     one's set code corner next to the catalog image.

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
import re
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
        def num(key):
            try:
                return float(r[key]) if r.get(key) not in (None, "") else None
            except ValueError:
                return None
        r["code"] = num("code_corr")     # anything odd in the set code corner (foil texture, but also halos)
        r["boxc"] = num("box_corr")      # the set code box itself vs catalog: ≈ +1 same print, negative = inverted
        try:
            r["box_px"] = [int(v) for v in (r.get("box") or "").split()] or None
        except ValueError:
            r["box_px"] = None
        r["inl"] = float(r.get("inliers") or 0)
        out.append(r)
    return out


try:   # the scan computes the same numbers (app/foil.py); standalone copy otherwise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.foil import FEATURES, feature_values  # noqa: E402
except ImportError:
    FEATURES = ["log_art", "log_frame", "log_frame_art", "code_corr", "box_corr"]

    def feature_values(art: float, frame: float, code: float | None = None, box: float | None = None) -> dict[str, float]:
        a, f = max(float(art), 0.1), max(float(frame), 0.1)
        return {"log_art": float(np.log(a)), "log_frame": float(np.log(f)), "log_frame_art": float(np.log(f / a)),
                "code_corr": 1.0 if code is None else float(code), "box_corr": 1.0 if box is None else float(box)}

USE = list(FEATURES)     # main() drops what an index.csv from an older holo_diff lacks


def features(r: dict) -> list[float]:
    v = feature_values(r["art"], r["frame"], r.get("code"), r.get("boxc"))
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
    for r, sc in zip(data, score, strict=True):
        r["oof"] = float(sc)               # score from a model that never saw this card
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


CODE_INVERTED, CODE_SAME = -0.3, 0.3    # set code vs catalog: the negative / the same
CODE_SURE = 0.5                         # this far from 0 a label that disagrees is almost certainly wrong
# cards that never come as reverse holo (V, ex, GX, …; ultra/illustration/… rarities)
NO_REVERSE = re.compile(r"(\b(V|VMAX|VSTAR|V-UNION|ex|EX|GX|BREAK|LV\.X|Prism Star)$)|"
                        r"(Double|Ultra|Illustration|Special|Hyper|Secret|Shiny|ACE|Radiant|Amazing|Rainbow|Gold)",
                        re.IGNORECASE)


def can_be_reverse(r: dict) -> bool:
    name, rarity = (r.get("name") or "").strip(), r.get("rarity") or ""
    return not (re.search(r"\b(V|VMAX|VSTAR|ex|EX|GX)$", name) or NO_REVERSE.search(rarity))


def labels_to_check(cat: list[dict], hint_cut: float | None) -> list[dict]:
    """
    Cards whose foiling label disagrees with the photo, strongest first. The set code box
    (box_corr) is the evidence; the shine (out-of-fold score) only speaks where the box can't:
      Normal       → Reverse Holo?  set code box inverted (a foil print: "Holo?" for cards that
                                    never come as reverse holo), or score above the hint cut
      Reverse Holo → Normal?        set code box the same as the plain print (in a set whose reverse
                                    holos invert it) or a score as low as a typical plain card
      Holo         → Reverse Holo?  only "maybe": holo and reverse holo both invert the box, so an
                                    inverted box on a holo usually means the catalog image shows
                                    the plain print; skipped for cards that never come as reverse
                                    holo (V, ex, ultra rares, …)
    """
    sets: dict[str, list[float]] = defaultdict(list)
    for r in cat:
        if r["foiling"] == "Reverse Holo" and r.get("boxc") is not None:
            sets[(r["card_id"] or "?").rsplit("-", 1)[0]].append(r["boxc"])
    inverts = {sid: float(np.median(v)) <= CODE_INVERTED for sid, v in sets.items() if len(v) >= 2}
    every = [c for v in sets.values() for c in v]       # sets without reverse holos to go by: all of them
    usual = len(every) >= 5 and float(np.median(every)) <= CODE_INVERTED
    normal_oof = [r["oof"] for r in cat if r["foiling"] == "Normal" and np.isfinite(r.get("oof", np.nan))]
    plain_like = float(np.median(normal_oof)) if normal_oof else None
    out = []
    for r in cat:
        code, oof = r.get("boxc"), r.get("oof", np.nan)     # the set code box itself (None: not to be seen)
        sid = (r["card_id"] or "?").rsplit("-", 1)[0]
        why, strength = None, 0.0
        # the set code decides where it can (it doesn't depend on the light); the shine
        # only speaks for cards whose set code says nothing (older sets, no print there)
        inv = inverts.get(sid, usual)                     # this set's reverse holos invert the box
        code_says = code is not None and inv and (code <= CODE_INVERTED or code >= CODE_SAME)
        if r["foiling"] == "Normal":
            suggest = "Reverse Holo"
            if code is not None and code <= -CODE_SURE:
                why, strength = "sure: set code box inverted (a foil print)", -code + 1
                suggest = "Reverse Holo" if can_be_reverse(r) else "Holo"
            elif code is not None and code <= CODE_INVERTED:
                why, strength = "maybe: set code box partly inverted", -code
            elif not code_says and hint_cut is not None and np.isfinite(oof) and oof >= hint_cut:
                why, strength = "maybe: shines like a reverse holo", min(1.0, (oof - hint_cut) / 4)
        elif r["foiling"] == "Reverse Holo":
            if code is not None and code >= CODE_SURE and inv:
                why, strength = "sure: set code box like the plain print", code + 1
            elif code is not None and code >= CODE_SAME and inv:
                why, strength = "maybe: set code box more like the plain print", code
            elif not code_says and plain_like is not None and np.isfinite(oof) and oof <= plain_like:
                why, strength = "maybe: no foil to see", min(1.0, (plain_like - oof) / 4)
            suggest = "Normal"
        elif r["foiling"] == "Holo":
            if code is not None and code <= -CODE_SURE and can_be_reverse(r):
                why, strength = "maybe: box differs from the catalog image — reverse holo, or the catalog shows the plain print", -code * 0.6
            suggest = "Reverse Holo"
        if why:
            out.append({**r, "suggest": suggest, "why": why, "strength": strength})
    return sorted(out, key=lambda x: -x["strength"])


def draw_sheet(found: list[dict], base: Path, path: Path) -> int:
    """Each suspect: the set code corner of the photo above the catalog image's, plus the whole photo."""
    try:
        import cv2
    except ImportError:
        print("   (--sheet needs opencv)")
        return 0
    tiles = []
    for r in found:
        # holo_diff's picture of this comparison (older index.csv: rebuild its path)
        rel = r.get("sheet") or (f"{r['kind']}/{(r['foiling'] or 'unknown').replace(' ', '_')}__"
                                 f"{(r.get('pattern') or 'none').replace(' ', '_')}/{r['id']}_vs_{r.get('against') or 'catalog'}.png")
        img = cv2.imread(str(base / rel))
        if img is None:
            continue
        w = img.shape[1] // 3
        h = img.shape[0]
        photo, ref = img[:, :w].copy(), img[:, w:2 * w].copy()
        if r.get("box_px"):                      # the set code box that was compared
            x0, y0, x1, y1 = r["box_px"]
            for im in (photo, ref):
                cv2.rectangle(im, (x0, y0), (x1, y1), (0, 0, 255), 2)
        corner = lambda im: im[int(0.86 * h):int(0.995 * h), 0:int(0.5 * w)]   # noqa: E731
        codes = np.vstack([corner(photo), corner(ref)])
        thumb = cv2.resize(photo, (int(w * codes.shape[0] / h), codes.shape[0]))
        tile = np.hstack([thumb, codes])
        bar = np.full((34, tile.shape[1], 3), 30, np.uint8)
        txt = f"{r['id']}  {r['foiling']} -> {r['suggest']}?  box {r['boxc'] if r.get('boxc') is not None else '-'}"
        cv2.putText(bar, txt[:60], (6, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(np.vstack([bar, tile]))
    if not tiles:
        return 0
    tw = max(t.shape[1] for t in tiles)
    tiles = [np.pad(t, ((0, 6), (0, tw - t.shape[1] + 6), (0, 0))) for t in tiles]
    cols = 2
    rows = [np.hstack(tiles[i:i + cols] + [np.zeros_like(tiles[0])] * (cols - len(tiles[i:i + cols])))
            for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(path), np.vstack(rows))
    return len(tiles)


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
    ap.add_argument("--sheet", type=Path, help="picture of the labels to check (set code corner vs catalog)")
    ap.add_argument("--app-url", help="your PokéCollect address, for links to the cards (e.g. https://tcg.example.com)")
    args = ap.parse_args()
    rows = load(Path(args.holo_diff))
    for col, feat in (("code", "code_corr"), ("boxc", "box_corr")):
        if not any(r.get(col) is not None for r in rows):
            USE.remove(feat)
    if "box_corr" not in USE:
        print("(no set code box column — run the new holo_diff.py again)")
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
    # set code corner: lower = foil. "corner" = lowest window anywhere there (foil texture,
    # but also halos), "box" = the set code box itself (inverted on foil prints of some sets)
    for key, label in (("code", "corner"), ("boxc", "set code box")):
        pa = np.array([-r[key] for r in cat if r["foiling"] == "Reverse Holo" and r.get(key) is not None])
        pb = np.array([-r[key] for r in cat if r["foiling"] == "Normal" and r.get(key) is not None])
        if len(pa) >= 5 and len(pb) >= 5:
            t, bacc = best_threshold(pa, pb)
            print(f"   {'Reverse Holo':>12} vs {'Normal':<7} by {label:<12} AUC {auc(pa, pb):.2f}   below {-t:.2f}: {bacc:.0%}   "
                  f"(medians {-np.median(pa):.2f} vs {-np.median(pb):.2f})")
    key = "boxc" if any(r.get("boxc") is not None for r in cat) else "code"
    if any(r.get(key) is not None for r in cat):
        sets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for r in cat:
            if r.get(key) is not None and r["foiling"] in ("Normal", "Holo", "Reverse Holo"):
                sets[(r["card_id"] or "?").rsplit("-", 1)[0]][r["foiling"]].append(r[key])
        inv = [(sid, v) for sid, v in sets.items() if len(v.get("Reverse Holo", [])) >= 2 and len(v.get("Normal", [])) >= 2]
        if inv:
            print(f"   {'set code box' if key == 'boxc' else 'set code corner'} by set (median; ≈ +1 same as the catalog "
                  "image, negative = inverted):")
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
    hint_cut = None
    if oof is not None:
        for want in sorted({0.95, 0.9, 0.8, 0.6, args.precision}, reverse=True):
            rec, _ = recall_at_precision(*oof, want)
            rec80 = rec if want == args.precision else rec80
            print(f"   flagging only the surest: {want:.0%} of the flags right → catches {rec:.0%} of reverse holos")
        order = np.argsort(-oof[0])
        hits = np.cumsum(oof[1][order])
        ok = np.where(hits / np.arange(1, len(order) + 1) >= args.precision)[0]
        hint_cut = float(oof[0][order[ok.max()]]) if len(ok) else None
    if score is None:
        print("   not enough rows — run holo_diff.py on the scan dataset first")
    elif score >= 0.9 and (precision or 0) >= 0.85:
        print(f"   {score:.0%}: a rule on these numbers is good enough to suggest Reverse Holo in the scan")
    elif rec80 >= 0.4:
        print(f"   {score:.0%} overall: worth a careful hint (\"reverse holo?\") on the surest cards only — "
              f"it would catch about {rec80:.0%} of reverse holos with {args.precision:.0%} of hints right")
    else:
        print(f"   {score:.0%}: these numbers are not enough (too many plain cards would be flagged) — the foil "
              "has to be learned from the "
              "difference images (holo_cluster.py features or a small CNN), with more photos under varied light")
    print("\n4. Labels to check (the photo disagrees with the foiling you set)")
    found = labels_to_check(cat, hint_cut)
    base = Path(args.holo_diff) if Path(args.holo_diff).is_dir() else Path(args.holo_diff).parent
    if not found:
        print("   none")
    else:
        url = (args.app_url or "").rstrip("/")
        for r in found[:40]:
            code = f"{r['boxc']:+.2f}" if r.get("boxc") is not None else "  –  "
            sc = f"{r['oof']:+.1f}" if np.isfinite(r.get("oof", np.nan)) else "  – "
            print(f"   {str(r['id']):>7}  {r['foiling']:<12} → {r['suggest']:<12}? set code box {code}  score {sc}  "
                  f"{(r.get('name') or r['card_id'] or '')[:28]:<28} {url}/cards/{r['id']}  ({r['why']})")
        if len(found) > 40:
            print(f"   … {len(found) - 40} more in labels_to_check.csv")
        with open(base / "labels_to_check.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "card_id", "name", "set", "number", "label", "suggest", "why", "box_corr", "score"])
            for r in found:
                w.writerow([r["id"], r["card_id"], r.get("name"), r.get("set"), r.get("number"), r["foiling"],
                            r["suggest"], r["why"], r.get("boxc"), round(r["oof"], 2) if np.isfinite(r.get("oof", np.nan)) else ""])
        print(f"   {len(found)} in {base / 'labels_to_check.csv'}")
        if args.sheet:
            n = draw_sheet(found[:60], base, args.sheet)
            print(f"   {n} pictures in {args.sheet} (top: your photo's set code corner, below: the catalog image's; "
                  "red: the box compared)")
    if args.save:
        save_model(cat, args.save, args.precision)


if __name__ == "__main__":
    main()
