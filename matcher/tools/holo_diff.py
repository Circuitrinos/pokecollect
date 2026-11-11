#!/usr/bin/env python3
"""
Foil / reverse-holo pattern training material from a PokéCollect scan dataset.

The catalog image of a card shows it without foil. Align a scanned crop to
that reference, even out the lighting and subtract: what is left is mostly
the foil. A reverse holo lights up around the art box, a Poké Ball / Master
Ball pattern shows its repeated motif, a holo only the art.

  python holo_diff.py pokecollect-scans-<date>.zip [--out holo_diff] [--pairs] [--download]

Input: the ZIP from Settings → Scan dataset (or its unpacked folder).
Output (in --out):
  <foiling>__<pattern>/<id>.png   difference image (left: aligned photo,
                                  middle: reference, right: difference)
  index.csv                       one row per photo: labels, how well it
                                  aligned, mean difference in the art box and
                                  in the frame around it, and their ratio
--pairs   also compare photos of the SAME card with different foiling
          (e.g. your reverse holo against your plain copy), photo vs photo
--download  fetch reference_url for cards whose catalog image isn't in the ZIP

Needs opencv-python-headless and numpy. Both are in the matcher image, which
ships this script: put the ZIP in the matcher's /data volume and run
docker exec -it <matcher> python /app/tools/holo_diff.py /data/<zip> --out /data/holo_diff
The frame/art ratio alone already separates reverse holos from plain cards
fairly well; the images are meant for training a real pattern classifier.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

W, H = 630, 880
# Art box of a modern card (fractions of the card) — rough, era-dependent
ART = (0.08, 0.11, 0.92, 0.55)


class Dataset:
    def __init__(self, src: Path):
        self.zip = zipfile.ZipFile(src) if src.is_file() else None
        self.dir = None if self.zip else src

    def read(self, name: str | None) -> bytes | None:
        if not name:
            return None
        try:
            if self.zip:
                return self.zip.read(name)
            p = self.dir / name
            return p.read_bytes() if p.is_file() else None
        except KeyError:
            return None

    def labels(self) -> list[dict]:
        raw = self.read("labels.jsonl") or b""
        return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def decode(data: bytes | None) -> np.ndarray | None:
    if not data:
        return None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return None if img is None else cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)


_orb = cv2.ORB_create(nfeatures=3000)
_bf = cv2.BFMatcher(cv2.NORM_HAMMING)


def align(photo: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray | None, int]:
    """Warp the photo onto the reference (homography from ORB matches). → (warped, inliers)."""
    g1, g2 = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY), cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    k1, d1 = _orb.detectAndCompute(g1, None)
    k2, d2 = _orb.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 8 or len(k2) < 8:
        return None, 0
    good = [m for m, n in (p for p in _bf.knnMatch(d1, d2, k=2) if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return None, len(good)
    src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    hom, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
    inl = int(mask.sum()) if mask is not None else 0
    if hom is None or inl < 12:
        return None, inl
    # Sub-pixel refinement (ECC): leftover misalignment shows up as bright edges
    try:
        hom = hom.astype(np.float32)
        _, hom = cv2.findTransformECC(cv2.GaussianBlur(g2, (5, 5), 0), cv2.GaussianBlur(g1, (5, 5), 0),
                                      np.linalg.inv(hom).astype(np.float32), cv2.MOTION_HOMOGRAPHY,
                                      (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5), None, 5)
        warped = cv2.warpPerspective(photo, hom, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                                     borderMode=cv2.BORDER_REPLICATE)
    except cv2.error:
        warped = cv2.warpPerspective(photo, hom, (W, H), borderMode=cv2.BORDER_REPLICATE)
    return warped, inl


def match_light(photo: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Per-channel mean/std of the photo (Lab) set to the reference's — glare stays, lighting goes."""
    a = cv2.cvtColor(photo, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32)
    for ch in range(3):
        sa, sb = a[..., ch].std() or 1.0, b[..., ch].std()
        a[..., ch] = (a[..., ch] - a[..., ch].mean()) * (sb / sa) + b[..., ch].mean()
    return cv2.cvtColor(np.clip(a, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def difference(photo: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """
    Colour difference (Lab distance), 0..255, where the print is flat: both
    images get the same softness first, and differences on the reference's
    edges (text, outlines — left over from alignment and blur) are damped, so
    what remains is mostly foil texture and glare.
    """
    a = cv2.cvtColor(cv2.GaussianBlur(photo, (0, 0), 1.4), cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(cv2.GaussianBlur(ref, (0, 0), 1.4), cv2.COLOR_BGR2LAB).astype(np.float32)
    d = np.sqrt(((a - b) ** 2).sum(axis=2))
    g = cv2.cvtColor(cv2.GaussianBlur(ref, (0, 0), 1.4), cv2.COLOR_BGR2GRAY).astype(np.float32)
    edge = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    edge = cv2.dilate(edge, np.ones((7, 7), np.uint8))
    d = d / (1.0 + edge / 20.0)
    return np.clip(d * 3.0, 0, 255).astype(np.uint8)


def regions(diff: np.ndarray) -> tuple[float, float]:
    """Mean difference inside the art box and in the frame around it (card border trimmed)."""
    x0, y0, x1, y1 = (int(ART[0] * W), int(ART[1] * H), int(ART[2] * W), int(ART[3] * H))
    art = diff[y0:y1, x0:x1]
    inner = diff[int(0.04 * H):int(0.96 * H), int(0.05 * W):int(0.95 * W)].astype(np.float64)
    frame_sum = inner.sum() - art.astype(np.float64).sum()
    frame_n = inner.size - art.size
    return float(art.mean()), float(frame_sum / max(1, frame_n))


def sheet(photo: np.ndarray, ref: np.ndarray, diff: np.ndarray) -> np.ndarray:
    heat = cv2.applyColorMap(diff, cv2.COLORMAP_INFERNO)
    return np.hstack([photo, ref, heat])


def fetch(url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=20) as r:   # noqa: S310 — URL from the app's allow-list
            return r.read()
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--out", type=Path, default=Path("holo_diff"))
    ap.add_argument("--pairs", action="store_true")
    ap.add_argument("--download", action="store_true")
    args = ap.parse_args()
    ds = Dataset(args.dataset)
    labels = ds.labels()
    args.out.mkdir(parents=True, exist_ok=True)
    rows, skipped = [], defaultdict(int)
    ref_cache: dict[str, np.ndarray | None] = {}

    def compare(lab: dict, photo: np.ndarray, ref: np.ndarray, kind: str, against: str):
        warped, inl = align(photo, ref)
        if warped is None:
            skipped["no_alignment"] += 1
            return
        warped = match_light(warped, ref)
        diff = difference(warped, ref)
        art, frame = regions(diff)
        group = f"{(lab.get('foiling') or 'unknown').replace(' ', '_')}__{(lab.get('pattern') or 'none').replace(' ', '_')}"
        out = args.out / kind / group
        out.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out / f"{lab['id']}_vs_{against}.png"), sheet(warped, ref, diff))
        rows.append({"kind": kind, "id": lab["id"], "against": against, "card_id": lab.get("card_id"),
                     "foiling": lab.get("foiling"), "pattern": lab.get("pattern"), "rarity": lab.get("rarity"),
                     "check": (lab.get("check") or {}).get("status"), "inliers": inl,
                     "art_diff": round(art, 2), "frame_diff": round(frame, 2),
                     "frame_art_ratio": round(frame / art, 3) if art else None})

    for lab in labels:
        if (lab.get("check") or {}).get("status") == "low":
            skipped["weak_crop"] += 1
            continue
        photo = decode(ds.read(lab.get("crop")))
        if photo is None:
            skipped["no_photo"] += 1
            continue
        cid = lab.get("card_id") or ""
        if cid not in ref_cache:
            data = ds.read(lab.get("reference")) or (fetch(lab.get("reference_url")) if args.download else None)
            ref_cache[cid] = decode(data)
        ref = ref_cache[cid]
        if ref is None:
            skipped["no_reference"] += 1
        else:
            compare(lab, photo, ref, "vs_catalog", "catalog")

    if args.pairs:
        by_card = defaultdict(list)
        for lab in labels:
            by_card[lab.get("card_id")].append(lab)
        for group in by_card.values():
            plain = [g for g in group if (g.get("foiling") or "Normal") == "Normal" and not g.get("pattern")]
            foiled = [g for g in group if g not in plain]
            if not plain or not foiled:
                continue
            base = decode(ds.read(plain[0].get("crop")))
            if base is None:
                continue
            for lab in foiled:
                photo = decode(ds.read(lab.get("crop")))
                if photo is not None:
                    compare(lab, photo, base, "vs_plain_photo", str(plain[0]["id"]))

    with open(args.out / "index.csv", "w", newline="", encoding="utf-8") as f:
        fields = ["kind", "id", "against", "card_id", "foiling", "pattern", "rarity", "check",
                  "inliers", "art_diff", "frame_diff", "frame_art_ratio"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    stats = defaultdict(list)
    for r in rows:
        if r["frame_art_ratio"] is not None:
            stats[(r["kind"], r["foiling"] or "?", r["pattern"] or "-")].append(r["frame_art_ratio"])
    print(f"{len(rows)} comparisons written to {args.out}/ ({dict(skipped) or 'nothing skipped'})")
    for (kind, foil, pattern), vals in sorted(stats.items()):
        print(f"  {kind:15s} {foil:13s} {pattern:12s} n={len(vals):4d}  frame/art median {np.median(vals):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
