#!/usr/bin/env python3
"""
Sort the foil of your scanned cards into buckets of similar patterns.

For every scanned card (scan dataset of the app) this aligns the photo to the
card's catalog image and subtracts them (tools/holo_diff.py), so mostly the
foil remains. Each difference is described by where the foil is (art box,
frame, a coarse grid), what its texture looks like (spectrum: fine sparkle vs.
repeated motifs like Poké Balls or type symbols, and the direction of lines)
and how strong it is. Similar cards end up in the same bucket (k-means; the
number of buckets is picked automatically unless you give --k).

Run it in the matcher container, which has everything it needs:

  docker exec -it <matcher> python /app/tools/holo_cluster.py \\
      --server http://api:8000 --user <name> --out /tmp/holo_clusters --download

  (password from PC_PASSWORD or asked; a ZIP from Settings → Scan dataset works too:
   python holo_cluster.py pokecollect-scans-<date>.zip --out holo_clusters)

Output in --out:
  cluster_01.jpg …     contact sheet per bucket: photo + difference, the most
                       typical cards first
  clusters.csv         one row per card: bucket, distance to the bucket's
                       centre, labels you set in the app (foiling, pattern …)
  summary.txt          bucket sizes and which labels each bucket contains
  cache/               differences and features; --recluster re-sorts from
                       them without aligning everything again (try other --k)
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import sys
import time
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from holo_diff import ART, H, W, Dataset, align, decode, difference, fetch, match_light, regions  # noqa: E402

THUMB = (126, 176)
GRID = (6, 4)            # rows × columns of the coarse "where is the foil" layout
RADIAL_BINS = 16
ORIENT_BINS = 12


# ── Server ──────────────────────────────────────────────────────────────────

def download_dataset(server: str, user: str, dest: Path) -> Path:
    password = os.environ.get("PC_PASSWORD") or getpass.getpass(f"PokéCollect password for {user}: ")
    base = server.rstrip("/")
    req = urllib.request.Request(f"{base}/api/v1/auth/login", method="POST",
                                 data=json.dumps({"username": user, "password": password}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:   # noqa: S310 — the user's own server
        token = json.loads(r.read())["access_token"]
    req = urllib.request.Request(f"{base}/api/v1/data/scan-dataset", headers={"Authorization": f"Bearer {token}"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading the scan dataset …", flush=True)
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:   # noqa: S310
        while chunk := r.read(1 << 20):
            f.write(chunk)
    print(f"  {dest.stat().st_size / 1e6:.1f} MB", flush=True)
    return dest


# ── Features ────────────────────────────────────────────────────────────────

def _masks():
    art = np.zeros((H, W), bool)
    art[int(ART[1] * H):int(ART[3] * H), int(ART[0] * W):int(ART[2] * W)] = True
    inner = np.zeros((H, W), bool)
    inner[int(0.04 * H):int(0.96 * H), int(0.05 * W):int(0.95 * W)] = True
    return art, inner & ~art


ART_MASK, FRAME_MASK = _masks()
# Texture patch: the text box below the art, where reverse-holo patterns show on modern cards
PATCH = (int(0.58 * H), int(0.93 * H), int(0.07 * W), int(0.93 * W))


def _radial_spectrum(patch: np.ndarray) -> np.ndarray:
    win = np.outer(np.hanning(patch.shape[0]), np.hanning(patch.shape[1]))
    f = np.abs(np.fft.fftshift(np.fft.fft2((patch - patch.mean()) * win))) ** 2
    cy, cx = f.shape[0] // 2, f.shape[1] // 2
    yy, xx = np.indices(f.shape)
    r = np.hypot((yy - cy) / f.shape[0], (xx - cx) / f.shape[1])
    edges = np.geomspace(0.01, 0.5, RADIAL_BINS + 1)
    out = np.array([f[(r >= a) & (r < b)].mean() if np.any((r >= a) & (r < b)) else 0.0
                    for a, b in zip(edges[:-1], edges[1:], strict=True)])
    out = np.log1p(out)
    return out / (out.sum() or 1.0)


def _orientation(patch: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(patch, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(patch, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    ang = (np.degrees(np.arctan2(gy, gx)) + 180.0) % 180.0
    hist, _ = np.histogram(ang, bins=ORIENT_BINS, range=(0, 180), weights=mag)
    return hist / (hist.sum() or 1.0)


def features(diff: np.ndarray) -> np.ndarray:
    d = diff.astype(np.float32) / 255.0
    art, frame = float(d[ART_MASK].mean()), float(d[FRAME_MASK].mean())
    where = [np.log((frame + 1e-3) / (art + 1e-3)), frame, art]
    g = cv2.resize(d, (GRID[1], GRID[0]), interpolation=cv2.INTER_AREA).ravel()
    layout = g / (g.sum() or 1.0)
    y0, y1, x0, x1 = PATCH
    patch = d[y0:y1, x0:x1]
    stats = []
    for m in (FRAME_MASK, ART_MASK):
        v = d[m]
        stats += [float(np.percentile(v, 50)), float(np.percentile(v, 90)), float(np.percentile(v, 99)),
                  float((v > 0.25).mean())]
    return np.concatenate([where, layout, _radial_spectrum(patch), _orientation(patch), stats]).astype(np.float32)


# Feature groups and how much each counts after standardizing
_N_WHERE, _N_LAYOUT, _N_STATS = 3, GRID[0] * GRID[1], 8
WEIGHTS = np.concatenate([np.full(_N_WHERE, 1.5), np.full(_N_LAYOUT, 0.6), np.full(RADIAL_BINS, 1.0),
                          np.full(ORIENT_BINS, 0.8), np.full(_N_STATS, 1.0)]).astype(np.float32)


def heat(diff: np.ndarray) -> np.ndarray:
    """Difference as a heat map, stretched per card so faint foil is visible in a thumbnail."""
    top = float(np.percentile(diff, 99.5)) or 1.0
    return cv2.applyColorMap(np.clip(diff.astype(np.float32) * (255.0 / top), 0, 255).astype(np.uint8),
                             cv2.COLORMAP_INFERNO)


# ── Clustering ──────────────────────────────────────────────────────────────

def kmeans(x: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 200, 1e-4)
    cv2.setRNGSeed(7)
    _, labels, centers = cv2.kmeans(x, k, None, crit, 8, cv2.KMEANS_PP_CENTERS)
    return labels.ravel(), centers


def silhouette(x: np.ndarray, labels: np.ndarray, sample: int = 1200) -> float:
    rng = np.random.default_rng(7)
    idx = rng.choice(len(x), min(sample, len(x)), replace=False)
    xs, ls = x[idx], labels[idx]
    d = np.sqrt(((xs[:, None, :] - xs[None, :, :]) ** 2).sum(-1))
    s = []
    for i in range(len(xs)):
        same = ls == ls[i]
        same[i] = False
        if not same.any():
            continue
        a = d[i, same].mean()
        b = min(d[i, ls == c].mean() for c in set(ls.tolist()) if c != ls[i])
        s.append((b - a) / max(a, b))
    return float(np.mean(s)) if s else -1.0


def choose_k(x: np.ndarray, max_k: int) -> int:
    best_k, best_s = 2, -1.0
    for k in range(2, max(2, min(max_k, len(x) - 1)) + 1):
        labels, _ = kmeans(x, k)
        s = silhouette(x, labels)
        print(f"  k={k:2d}  silhouette {s:.3f}", flush=True)
        if s > best_s + 0.01:
            best_k, best_s = k, s
    return best_k


def contact_sheet(items: list[dict], cache: Path, path: Path, title: str, cols: int = 6, max_items: int = 36):
    tw, th = THUMB
    cell_w, cell_h = tw * 2 + 6, th + 22
    shown = items[:max_items]
    rows = max(1, (len(shown) + cols - 1) // cols)
    sheet = np.full((rows * cell_h + 34, cols * cell_w, 3), 24, np.uint8)
    cv2.putText(sheet, title, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2)
    for n, it in enumerate(shown):
        img = cv2.imread(str(cache / f"{it['id']}.jpg"))
        if img is None:
            continue
        y, x = 34 + (n // cols) * cell_h, (n % cols) * cell_w
        sheet[y:y + th, x:x + img.shape[1]] = img[:th, :cell_w]
        tag = it.get("pattern") or it.get("foiling") or ""
        label = f"#{it['id']} {(it.get('name') or '')[:16]}{' - ' + tag[:12] if tag else ''}"
        label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()   # putText: ASCII only
        cv2.putText(sheet, label, (x + 2, y + th + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1)
    cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", type=Path, nargs="?", help="scan dataset ZIP or folder (or use --server)")
    ap.add_argument("--server", help="the app's API, e.g. http://api:8000 inside the compose network")
    ap.add_argument("--user", default=os.environ.get("PC_USER"), help="user name for --server (admin)")
    ap.add_argument("--out", type=Path, default=Path("holo_clusters"))
    ap.add_argument("--k", type=int, default=0, help="number of buckets (default: pick automatically)")
    ap.add_argument("--max-k", type=int, default=10)
    ap.add_argument("--download", action="store_true", help="fetch catalog images that aren't in the dataset")
    ap.add_argument("--recluster", action="store_true", help="only re-sort cached features")
    ap.add_argument("--limit", type=int, default=0, help="only the first N cards (quick test)")
    args = ap.parse_args()
    out, cache = args.out, args.out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    feat_file = cache / "features.npz"
    meta_file = cache / "meta.jsonl"

    known: dict[int, dict] = {}
    feats: dict[int, np.ndarray] = {}
    if feat_file.is_file() and meta_file.is_file():
        z = np.load(feat_file)
        for i, f in zip(z["ids"].tolist(), z["feats"], strict=True):
            feats[int(i)] = f
        for line in meta_file.read_text(encoding="utf-8").splitlines():
            m = json.loads(line)
            known[int(m["id"])] = m

    if not args.recluster:
        if args.server:
            if not args.user:
                args.user = input("PokéCollect user name: ").strip()
            src = download_dataset(args.server, args.user, out / "scans.zip")
        elif args.dataset:
            src = args.dataset
        else:
            ap.error("give a dataset ZIP/folder or --server")
        ds = Dataset(src)
        labels = [lab for lab in ds.labels() if (lab.get("check") or {}).get("status") != "low"]
        if args.limit:
            labels = labels[:args.limit]
        refs: dict[str, np.ndarray | None] = {}
        todo = [lab for lab in labels if int(lab["id"]) not in feats]
        print(f"{len(labels)} cards, {len(todo)} to compare ({len(labels) - len(todo)} cached)", flush=True)
        skipped = Counter()
        t0 = time.time()
        for n, lab in enumerate(todo, 1):
            cid = lab.get("card_id") or ""
            if cid not in refs:
                data = ds.read(lab.get("reference")) or (fetch(lab.get("reference_url")) if args.download else None)
                refs[cid] = decode(data)
            ref, photo = refs[cid], decode(ds.read(lab.get("crop")))
            if ref is None or photo is None:
                skipped["no_reference" if ref is None else "no_photo"] += 1
                continue
            warped, inl = align(photo, ref)
            if warped is None:
                skipped["no_alignment"] += 1
                continue
            warped = match_light(warped, ref)
            diff = difference(warped, ref)
            art, frame = regions(diff)
            feats[int(lab["id"])] = features(diff)
            small = np.hstack([cv2.resize(warped, THUMB, interpolation=cv2.INTER_AREA), np.full((THUMB[1], 6, 3), 24, np.uint8),
                               cv2.resize(heat(diff), THUMB, interpolation=cv2.INTER_AREA)])
            cv2.imwrite(str(cache / f"{lab['id']}.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 85])
            known[int(lab["id"])] = {k: lab.get(k) for k in ("id", "card_id", "name", "set", "number", "language",
                                                            "foiling", "pattern", "rarity")} | {
                "inliers": inl, "art_diff": round(art, 2), "frame_diff": round(frame, 2)}
            if n % 25 == 0 or n == len(todo):
                rate = (time.time() - t0) / n
                print(f"  {n}/{len(todo)}  ~{rate * (len(todo) - n) / 60:.1f} min left", flush=True)
        if skipped:
            print(f"skipped: {dict(skipped)}  (no_reference: try --download)", flush=True)
        ids = sorted(feats)
        np.savez(feat_file, ids=np.array(ids), feats=np.stack([feats[i] for i in ids]) if ids else np.zeros((0, 1)))
        meta_file.write_text("\n".join(json.dumps(known[i], ensure_ascii=False) for i in ids if i in known) + "\n",
                             encoding="utf-8")

    ids = [i for i in sorted(feats) if i in known]
    if len(ids) < 3:
        print("Not enough cards to sort (need at least 3 with a catalog image).")
        return 1
    x = np.stack([feats[i] for i in ids]).astype(np.float32)
    mu, sd = x.mean(0), x.std(0)
    sd[sd < 1e-6] = 1.0
    xs = ((x - mu) / sd * WEIGHTS).astype(np.float32)
    if args.k:
        k = min(args.k, len(ids) - 1)
    else:
        print("Choosing the number of buckets …", flush=True)
        k = choose_k(xs, args.max_k)
    labels, centers = kmeans(xs, k)
    dist = np.linalg.norm(xs - centers[labels], axis=1)

    # Buckets ordered by how much foil they show (plain cards first)
    strength = {c: float(np.median([known[ids[i]]["frame_diff"] + known[ids[i]]["art_diff"]
                                    for i in np.where(labels == c)[0]])) for c in range(k)}
    order = {c: n + 1 for n, c in enumerate(sorted(range(k), key=lambda c: strength[c]))}
    for old in out.glob("cluster_*.jpg"):
        old.unlink()
    rows, lines = [], []
    for c in sorted(range(k), key=lambda c: order[c]):
        members = sorted((i for i in range(len(ids)) if labels[i] == c), key=lambda i: dist[i])
        items = [known[ids[i]] | {"cluster": order[c], "dist": round(float(dist[i]), 3)} for i in members]
        rows += items
        fo = Counter(it.get("foiling") or "?" for it in items)
        pa = Counter(it.get("pattern") or "-" for it in items)
        ratio = np.median([(it["frame_diff"] + 0.1) / (it["art_diff"] + 0.1) for it in items])
        line = (f"bucket {order[c]:2d}: {len(items):4d} cards  frame/art {ratio:4.2f}  "
                f"foiling {dict(fo.most_common(3))}  pattern {dict(pa.most_common(3))}")
        lines.append(line)
        contact_sheet(items, cache, out / f"cluster_{order[c]:02d}.jpg",
                      f"Bucket {order[c]} - {len(items)} cards - frame/art {ratio:.2f}")
    with open(out / "clusters.csv", "w", newline="", encoding="utf-8") as f:
        fields = ["cluster", "dist", "id", "card_id", "name", "set", "number", "language", "foiling", "pattern",
                  "rarity", "inliers", "art_diff", "frame_diff"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    summary = (f"{len(ids)} cards in {k} buckets (ordered from least to most foil).\n"
               "Look at cluster_NN.jpg; labels in brackets are what you set in the app.\n\n" + "\n".join(lines) + "\n")
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Contact sheets and clusters.csv in {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
