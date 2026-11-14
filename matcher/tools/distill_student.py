#!/usr/bin/env python3
"""
A small, fast "student" image model for the matcher's shortlist, taught by the
DINOv2 model it uses now (the teacher). Runs on a PC with an NVIDIA GPU; a CPU
works too, just much slower.

The student learns to turn a phone photo of a card into the teacher's vector of
that card's catalog image. So the matcher can keep the catalog index it already
has (teacher vectors) and only the photo side gets fast — and because the
student matches photos against catalog images rather than memorising card ids,
cards that come out later work the same way.

Training data, all from your NAS (see matcher/README.md, "Student model"):
  catalog_emb.npz   the teacher's catalog vectors (matcher data volume, emb/)
  catalog.csv       card_id,image_url of the catalog (database export)
  scans ZIP         Settings → Scan dataset: your confirmed card photos
  dinov2_vits14_cls.onnx   the teacher itself (only for `check`)

Three steps:
  python distill_student.py fetch --catalog catalog.csv --targets catalog_emb.npz --images images/
  python distill_student.py train --images images/ --targets catalog_emb.npz --scans scans.zip --out run/
  python distill_student.py check --run run/ --teacher dinov2_vits14_cls.onnx --targets catalog_emb.npz --scans scans.zip

`train` keeps a fifth of the scan photos and a tenth of the cards out of training
(the same split every time); `check` measures teacher and student on exactly
those — how often the right card is 1st, in the top 5 and in the top 30 (the
shortlist) — plus the time per picture on this PC's CPU, and says whether the
student is good enough to replace the teacher for photos.

`train --resume run/student.pt --epochs 3` continues a run (e.g. after new
scans): the same split, new photos join in.

Needs: pip install numpy opencv-python-headless onnxruntime httpx onnx — and
PyTorch + torchvision with CUDA, installed the way pytorch.org's "Get started"
page shows for your system (the plain Windows wheel is CPU-only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # matcher/ → app.*
from app.embedding import autolevel  # the matcher's own preprocessing

SIZE = (224, 308)          # student input (w, h) — the teacher's, so the matcher can swap it in as is
SAME_ART = 0.95            # teacher vectors this close: same artwork (reprints) — all count as right
SCAN_HOLDOUT = 5           # 1 in 5 scan photos (by photo) is only for `check`
CARD_HOLDOUT = 10          # 1 in 10 cards is never trained on: how the student does on new cards
UA = {"User-Agent": "PokeCollect-distill/1.0 (self-hosted)"}


# ── data ─────────────────────────────────────────────────────────────────────

def load_targets(path: Path) -> tuple[list[str], np.ndarray]:
    with np.load(path) as z:
        raw = z["ids"]
        ids = json.loads(raw.item() if raw.ndim == 0 else str(raw))
        emb = z["emb"].astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9
    return ids, emb


def _bucket(key: str, n: int) -> int:
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) % n


def held_out_card(card_id: str) -> bool:
    return _bucket("card:" + card_id, CARD_HOLDOUT) == 0


def held_out_photo(original: str) -> bool:
    return _bucket("photo:" + original, SCAN_HOLDOUT) == 0


def safe_name(card_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", card_id)


class Scans:
    """The scan dataset ZIP (or its unpacked folder): crops with their confirmed card."""

    def __init__(self, src: Path):
        self.src = src
        self.zip = None if src.is_dir() else zipfile.ZipFile(src)
        text = (src / "labels.jsonl").read_text("utf-8") if src.is_dir() else self.zip.read("labels.jsonl").decode()
        self.rows = [json.loads(x) for x in text.splitlines() if x.strip()]

    def read(self, name: str) -> np.ndarray | None:
        data = (self.src / name).read_bytes() if self.zip is None else self.zip.read(name)
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)

    def usable(self, known: set[str]) -> list[dict]:
        return [r for r in self.rows if r.get("card_id") in known and r.get("crop")
                and (r.get("check") or {}).get("status") != "low"]


# ── preprocessing and augmentation ───────────────────────────────────────────

def to_input(card_bgr: np.ndarray, size=SIZE) -> np.ndarray:
    """As the matcher feeds its model: autolevel, resize, RGB, [0, 1], CHW."""
    x = cv2.cvtColor(cv2.resize(autolevel(card_bgr), size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(x.transpose(2, 0, 1), dtype=np.float32) / 255.0


def _glare(img: np.ndarray, rng) -> np.ndarray:
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.float32)
    for _ in range(rng.integers(1, 3)):
        c = (int(rng.uniform(0, w)), int(rng.uniform(0, h)))
        ax = (int(rng.uniform(0.08, 0.35) * w), int(rng.uniform(0.04, 0.2) * h))
        cv2.ellipse(mask, c, ax, float(rng.uniform(0, 180)), 0, 360, float(rng.uniform(0.3, 0.9)), -1)
    mask = cv2.GaussianBlur(mask, (0, 0), max(3.0, 0.04 * w))
    return np.clip(img * (1 - mask[..., None]) + 255 * mask[..., None], 0, 255).astype(np.uint8)


def photo_like(card: np.ndarray, rng) -> np.ndarray:
    """A catalog image made to look like the matcher's crop of a phone photo of that card."""
    h, w = 440, 315
    card = cv2.resize(card, (w, h), interpolation=cv2.INTER_AREA)
    # on a table / in a pocket, seen at a slant
    m = int(0.25 * w)
    bg = np.full((h + 2 * m, w + 2 * m, 3), rng.integers(60, 235, 3), np.uint8)
    bg = cv2.add(bg, rng.integers(0, 25, bg.shape).astype(np.uint8))
    bg[m:m + h, m:m + w] = card
    if rng.random() < 0.5:     # sleeve / pocket sheen: a faint lighter band
        band = np.zeros(bg.shape[:2], np.float32)
        x0 = int(rng.uniform(0, bg.shape[1]))
        cv2.line(band, (x0, 0), (x0 + int(rng.uniform(-200, 200)), bg.shape[0]), 0.25, int(rng.uniform(20, 90)))
        band = cv2.GaussianBlur(band, (0, 0), 15)
        bg = np.clip(bg * (1 - band[..., None]) + 255 * band[..., None], 0, 255).astype(np.uint8)
    corners = np.float32([[m, m], [m + w, m], [m + w, m + h], [m, m + h]])
    t = rng.uniform(-0.12, 0.12, (4, 2)) * [w, h]
    seen = corners + t.astype(np.float32)
    P = cv2.getPerspectiveTransform(corners, seen)
    photo = cv2.warpPerspective(bg, P, (bg.shape[1], bg.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    # light: brightness, contrast, colour cast, a gradient, glare
    photo = photo.astype(np.float32) * rng.uniform(0.6, 1.25) + rng.uniform(-30, 30)
    photo *= rng.uniform(0.85, 1.15, 3)
    gy = np.linspace(rng.uniform(0.75, 1.15), rng.uniform(0.75, 1.15), photo.shape[0], dtype=np.float32)
    photo = np.clip(photo * gy[:, None, None], 0, 255).astype(np.uint8)
    if rng.random() < 0.6:
        photo = _glare(photo, rng)
    # the camera: blur, noise, JPEG
    if rng.random() < 0.6:
        k = int(rng.choice([3, 5, 7]))
        photo = cv2.GaussianBlur(photo, (k, k), 0)
    photo = np.clip(photo + rng.normal(0, rng.uniform(0, 8), photo.shape), 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, int(rng.uniform(45, 92))])
    photo = cv2.imdecode(enc, cv2.IMREAD_COLOR) if ok else photo
    # the matcher's crop: the found outline is a little off (detection, art-grown guess)
    found = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), P).reshape(4, 2)
    found += (rng.normal(0, 0.025, (4, 2)) * [w, h]).astype(np.float32)
    back = cv2.getPerspectiveTransform(found.astype(np.float32), np.float32([[0, 0], [w, 0], [w, h], [0, h]]))
    return cv2.warpPerspective(photo, back, (w, h), borderMode=cv2.BORDER_REPLICATE)


def jitter_scan(crop: np.ndarray, rng) -> np.ndarray:
    """A real crop, a little different each time (outline, light, sharpness)."""
    h, w = crop.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = src + (rng.normal(0, 0.02, (4, 2)) * [w, h]).astype(np.float32)
    out = cv2.warpPerspective(crop, cv2.getPerspectiveTransform(dst, src), (w, h), borderMode=cv2.BORDER_REPLICATE)
    out = np.clip(out.astype(np.float32) * rng.uniform(0.8, 1.15) + rng.uniform(-15, 15), 0, 255).astype(np.uint8)
    if rng.random() < 0.3:
        out = cv2.GaussianBlur(out, (3, 3), 0)
    return out


# ── fetch ────────────────────────────────────────────────────────────────────

def cmd_fetch(a) -> None:
    import csv

    import httpx
    ids, _ = load_targets(a.targets)
    want = set(ids)
    rows = []
    with open(a.catalog, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("card_id") in want and (r.get("image_url") or "").startswith("https://"):
                rows.append((r["card_id"], r["image_url"]))
    a.images.mkdir(parents=True, exist_ok=True)
    todo = [(c, u) for c, u in rows if not (a.images / f"{safe_name(c)}.jpg").exists()]
    if a.max_cards:
        todo = todo[:a.max_cards]
    print(f"{len(rows)} catalog cards with a teacher vector and an image URL; {len(todo)} to download")
    done = failed = 0
    t0 = time.time()

    def get(item):
        cid, url = item
        for attempt in range(3):
            try:
                r = http.get(url)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        cv2.imwrite(str(a.images / f"{safe_name(cid)}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 93])
                        return True
                if r.status_code in (404, 410):
                    return False
            except httpx.HTTPError:
                pass
            time.sleep(1 + 2 * attempt)
        return False

    with httpx.Client(timeout=30, headers=UA, follow_redirects=True) as http, ThreadPoolExecutor(4) as pool:
        for ok in pool.map(get, todo):
            done += ok
            failed += not ok
            if (done + failed) % 500 == 0:
                rate = (done + failed) / max(1e-6, time.time() - t0)
                print(f"  {done + failed}/{len(todo)} ({failed} failed), {rate:.1f}/s")
    (a.images / "index.json").write_text(json.dumps({c: f"{safe_name(c)}.jpg" for c, _ in rows}), "utf-8")
    print(f"done: {done} downloaded, {failed} failed")


# ── model ────────────────────────────────────────────────────────────────────

def build_student(dim: int, arch: str, pretrained: bool):
    import torch
    import torchvision

    class Student(torch.nn.Module):
        """RGB in [0, 1] (as the matcher sends it) → unit vector in the teacher's space."""

        def __init__(self):
            super().__init__()
            weights = "DEFAULT" if pretrained else None
            if arch == "mobilenet_v3_large":
                net = torchvision.models.mobilenet_v3_large(weights=weights)
                net.classifier[-1] = torch.nn.Linear(net.classifier[-1].in_features, dim)
            elif arch == "mobilenet_v3_small":
                net = torchvision.models.mobilenet_v3_small(weights=weights)
                net.classifier[-1] = torch.nn.Linear(net.classifier[-1].in_features, dim)
            elif arch == "efficientnet_b0":
                net = torchvision.models.efficientnet_b0(weights=weights)
                net.classifier[-1] = torch.nn.Linear(net.classifier[-1].in_features, dim)
            else:
                raise SystemExit(f"unknown --arch {arch}")
            self.net = net
            self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
            self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

        def forward(self, x):
            v = self.net((x - self.mean) / self.std)
            return v / (v.norm(dim=1, keepdim=True) + 1e-9)

    return Student()


class Samples:
    """One epoch: every training card once (as a fake photo) + the training scans, several times."""

    def __init__(self, images: Path, index: dict[str, str], scans: Scans | None, scan_rows: list[dict],
                 card_rows: list[tuple[str, int]], pos: dict[str, int], scan_repeat: int, size):
        self.images, self.index, self.size = images, index, size
        self.items = [("cat", cid, t) for cid, t in card_rows]
        self.items += [("scan", r["crop"], pos[r["card_id"]]) for r in scan_rows] * scan_repeat
        self._scans_path = scans.src if scans else None
        self.scans: Scans | None = None          # opened in each worker process

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, k: int):
        import torch
        rng = np.random.default_rng()
        kind, ref, target = self.items[k]
        if kind == "cat":
            img = cv2.imread(str(self.images / self.index[ref]), cv2.IMREAD_COLOR)
            img = photo_like(img, rng) if rng.random() < 0.9 else img      # sometimes the clean image
        else:
            if self.scans is None:              # per worker process: its own ZIP handle
                self.scans = Scans(self._scans_path)
            img = jitter_scan(self.scans.read(ref), rng)
        return torch.from_numpy(to_input(img, self.size)), target, kind == "scan"


def cmd_train(a) -> None:
    import torch
    ids, T = load_targets(a.targets)
    pos = {c: i for i, c in enumerate(ids)}
    index = json.loads((a.images / "index.json").read_text("utf-8")) if (a.images / "index.json").exists() else {}
    have = {c for c, f in index.items() if (a.images / f).exists()}
    train_cards = [(c, pos[c]) for c in ids if c in have and not held_out_card(c)]
    if a.max_cards:
        rng = np.random.default_rng(1)
        train_cards = [train_cards[i] for i in sorted(rng.permutation(len(train_cards))[:a.max_cards])]
    scans = Scans(a.scans) if a.scans else None
    scan_rows = [r for r in scans.usable(set(ids)) if not held_out_photo(r["original"])
                 and not held_out_card(r["card_id"])] if scans else []
    repeat = max(1, round(a.scan_share * len(train_cards) / max(1, len(scan_rows)))) if scan_rows else 0
    print(f"training on {len(train_cards)} catalog cards and {len(scan_rows)} scan photos (×{repeat}); "
          f"{sum(held_out_card(c) for c in ids)} cards and 1 in {SCAN_HOLDOUT} scan photos kept out for `check`")
    dev = torch.device(a.device if a.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
    size = tuple(int(v) for v in a.size.lower().split("x"))
    model = build_student(T.shape[1], a.arch, not a.no_pretrained).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    start = 0
    if a.resume:
        ck = torch.load(a.resume, map_location=dev, weights_only=False)
        model.load_state_dict(ck["model"])
        if "opt" in ck and not a.fresh_optimizer:
            opt.load_state_dict(ck["opt"])
        start = ck.get("epoch", 0)
        print(f"resumed {a.resume} after epoch {start}")
    # the catalog the student ranks against: the teacher's vectors, without the held-out cards
    keep = np.array([not held_out_card(c) for c in ids])
    Tt = torch.from_numpy(T).to(dev)
    neg_mask = torch.from_numpy(~keep).to(dev)
    ds = Samples(a.images, index, scans, scan_rows, train_cards, pos, repeat, size)
    dl = torch.utils.data.DataLoader(ds, batch_size=a.batch, shuffle=True, num_workers=a.workers, drop_last=True,
                                     persistent_workers=a.workers > 0, pin_memory=dev.type == "cuda")
    steps = a.epochs * max(1, len(dl))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.1)
    amp = dev.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=amp)
    a.out.mkdir(parents=True, exist_ok=True)
    for epoch in range(start, start + a.epochs):
        model.train()
        t0, n, tot, hit = time.time(), 0, 0.0, 0
        for x, y, _ in dl:
            x, y = x.to(dev, non_blocking=True), y.to(dev)
            with torch.autocast(dev.type, enabled=amp):
                s = model(x).float()
            logits = (s @ Tt.T) / a.tau
            logits = logits.masked_fill(neg_mask[None, :], -1e4)
            same = (Tt[y] @ Tt.T) > SAME_ART                    # the card itself and same-art reprints
            loss_ce = torch.logsumexp(logits, 1) - torch.logsumexp(logits.masked_fill(~same, -1e4), 1)
            loss_cos = 1 - (s * Tt[y]).sum(1)
            loss = (loss_ce + a.cos_weight * loss_cos).mean()
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            n += len(y)
            tot += loss.item() * len(y)
            hit += int(same.gather(1, logits.argmax(1, keepdim=True)).sum())
        print(f"epoch {epoch + 1}: loss {tot / max(1, n):.4f}, right card 1st {100 * hit / max(1, n):.1f}% "
              f"(training pictures), {time.time() - t0:.0f} s")
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "epoch": epoch + 1,
                    "arch": a.arch, "size": size, "dim": T.shape[1]}, a.out / "student.pt")
    export_onnx(model.cpu().eval(), size, a.out / "student.onnx")
    (a.out / "run.json").write_text(json.dumps({"arch": a.arch, "size": size, "epochs": start + a.epochs,
                                                "cards": len(train_cards), "scans": len(scan_rows)}), "utf-8")
    print(f"wrote {a.out / 'student.onnx'} — next: python {Path(__file__).name} check --run {a.out} …")


def export_onnx(model, size, path: Path) -> None:
    import torch
    w, h = size
    dummy = torch.rand(2, 3, h, w)
    kw = {"input_names": ["image"], "output_names": ["embedding"], "opset_version": 17,
          "dynamic_axes": {"image": {0: "batch"}, "embedding": {0: "batch"}}}
    try:
        torch.onnx.export(model, dummy, str(path), dynamo=False, **kw)
    except TypeError:          # older torch: no dynamo argument
        torch.onnx.export(model, dummy, str(path), **kw)


# ── check ────────────────────────────────────────────────────────────────────

class Onnx:
    def __init__(self, path: Path, threads: int = 0):
        import onnxruntime as ort
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        self.name = inp.name
        hw = inp.shape[2:]
        self.size = (int(hw[1]), int(hw[0])) if all(isinstance(v, int) for v in hw) else SIZE

    def embed(self, imgs: list[np.ndarray]) -> np.ndarray:
        x = np.stack([to_input(i, self.size) for i in imgs])
        out = self.sess.run(None, {self.name: x})[0].astype(np.float32)
        return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)


def _ranks(Q: np.ndarray, T: np.ndarray, right: list[np.ndarray], domain=None) -> np.ndarray:
    """Rank (0 = first) of the best right card for each query."""
    if domain is not None:
        S = (Q - domain.mu_photo) @ domain.cat.T
        S = S / (np.linalg.norm(Q - domain.mu_photo, axis=1, keepdims=True) + 1e-9) - domain.penalty
    else:
        S = Q @ T.T
    out = []
    for s, r in zip(S, right):
        out.append(int((s > s[r].max()).sum()))
    return np.array(out)


def cmd_check(a) -> None:
    from app.embedding import EmbSnapshot, build_domain
    ids, T = load_targets(a.targets)
    student_path = a.student or (a.run / "student.onnx")
    scans = Scans(a.scans)
    rows = scans.usable(set(ids))
    test = [r for r in rows if held_out_photo(r["original"]) or held_out_card(r["card_id"])]
    train = [r for r in rows if r not in test]
    if a.max_photos:
        test = test[:a.max_photos]
    if not test:
        raise SystemExit("no held-out scan photos — export a scan dataset with more photos")
    pos = {c: i for i, c in enumerate(ids)}
    same = [np.flatnonzero(T @ T[pos[r["card_id"]]] > SAME_ART) for r in test]
    new = np.array([held_out_card(r["card_id"]) for r in test])
    print(f"{len(test)} held-out scan photos ({int(new.sum())} of cards the student never trained on), "
          f"catalog {len(ids)} cards")
    res = {}
    for name, path in (("teacher", a.teacher), ("student", student_path)):
        eng = Onnx(path, a.threads)
        crops = [scans.read(r["crop"]) for r in test]
        t0 = time.perf_counter()
        Q = np.concatenate([eng.embed(crops[k:k + 16]) for k in range(0, len(crops), 16)])
        # time as the matcher uses it: one card, upright + upside down
        probe = crops[0]
        eng.embed([probe, probe])
        t1 = time.perf_counter()
        for _ in range(a.speed_runs):
            eng.embed([probe, cv2.rotate(probe, cv2.ROTATE_180)])
        ms = (time.perf_counter() - t1) * 1000 / a.speed_runs / 2
        dom = None
        if len(train) >= 50:
            P = np.concatenate([eng.embed([scans.read(r["crop"]) for r in train[k:k + 16]])
                                for k in range(0, min(len(train), a.max_domain), 16)])
            dom = build_domain(EmbSnapshot(ids, T), [r["card_id"] for r in train[:a.max_domain]], P)
        plain = _ranks(Q, T, same)
        corr = _ranks(Q, T, same, dom) if dom is not None else None
        res[name] = {"ms_per_picture": round(ms, 1), "plain": plain, "domain": corr,
                     "embed_s": round(time.perf_counter() - t0, 1)}

    def rec(r, mask=None):
        r = r if mask is None else r[mask]
        return {f"top{k}": round(100 * float((r < k).mean()), 1) if len(r) else None for k in (1, 5, 30)}

    report = {}
    print(f"\n{'':10s}{'ms/picture':>11s}   {'all held-out photos (1st / top 5 / top 30)':>44s}   "
          f"{'cards never trained on':>24s}")
    for name, r in res.items():
        for how in ("plain", "domain"):
            if r[how] is None:
                continue
            al, nw = rec(r[how]), rec(r[how], new)
            report[f"{name}_{how}"] = {"all": al, "new_cards": nw, "ms_per_picture": r["ms_per_picture"]}
            fmt = lambda d: " / ".join("-" if v is None else f"{v:5.1f}%" for v in d.values())
            label = f"{name}{' +domain' if how == 'domain' else ''}"
            print(f"{label:16s}{r['ms_per_picture']:5.1f}   {fmt(al):>44s}   {fmt(nw):>24s}")
    best = lambda name: max((report[k]["all"] for k in report if k.startswith(name)),
                            key=lambda d: (d["top30"] or 0, d["top1"] or 0))
    t, s = best("teacher"), best("student")
    ok = s["top30"] >= t["top30"] - a.max_drop_top30 and s["top1"] >= t["top1"] - a.max_drop_top1
    speed = res["teacher"]["ms_per_picture"] / max(0.1, res["student"]["ms_per_picture"])
    verdict = (f"PASS: the student keeps the shortlist ({s['top30']}% vs {t['top30']}% in the top 30) "
               f"and is {speed:.1f}× faster per picture" if ok else
               f"NOT YET: the student loses too much ({s['top1']}% vs {t['top1']}% first, "
               f"{s['top30']}% vs {t['top30']}% in the top 30) — train longer, a larger --arch, or more scans")
    print("\n" + verdict)
    out = (a.run or Path(student_path).parent) / "check.json"
    out.write_text(json.dumps({"report": report, "pass": bool(ok), "speedup": round(speed, 1),
                               "photos": len(test), "new_card_photos": int(new.sum())}, indent=1), "utf-8")
    print(f"(saved to {out})")


# ── command line ─────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download the catalog images")
    f.add_argument("--catalog", type=Path, required=True, help="CSV with card_id,image_url")
    f.add_argument("--targets", type=Path, required=True, help="catalog_emb.npz (which cards to get)")
    f.add_argument("--images", type=Path, required=True)
    f.add_argument("--max-cards", type=int, default=0)
    t = sub.add_parser("train", help="train the student")
    t.add_argument("--images", type=Path, required=True)
    t.add_argument("--targets", type=Path, required=True)
    t.add_argument("--scans", type=Path, help="scan dataset ZIP (Settings → Scan dataset)")
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--arch", default="mobilenet_v3_large",
                   choices=["mobilenet_v3_large", "mobilenet_v3_small", "efficientnet_b0"])
    t.add_argument("--size", default=f"{SIZE[0]}x{SIZE[1]}", help="input w×h (the matcher expects 224x308)")
    t.add_argument("--epochs", type=int, default=15)
    t.add_argument("--batch", type=int, default=96)
    t.add_argument("--lr", type=float, default=1e-3)
    t.add_argument("--tau", type=float, default=0.05, help="softmax temperature over the catalog")
    t.add_argument("--cos-weight", type=float, default=1.0)
    t.add_argument("--scan-share", type=float, default=0.3, help="real photos' share of an epoch")
    t.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 2))
    t.add_argument("--device", default="auto")
    t.add_argument("--max-cards", type=int, default=0, help="train on a random subset (quick try)")
    t.add_argument("--resume", type=Path)
    t.add_argument("--fresh-optimizer", action="store_true")
    t.add_argument("--no-pretrained", action="store_true", help="start from random weights (offline)")
    c = sub.add_parser("check", help="teacher vs student on the held-out scans")
    c.add_argument("--run", type=Path, help="folder from train (student.onnx)")
    c.add_argument("--student", type=Path)
    c.add_argument("--teacher", type=Path, required=True, help="dinov2_vits14_cls.onnx")
    c.add_argument("--targets", type=Path, required=True)
    c.add_argument("--scans", type=Path, required=True)
    c.add_argument("--threads", type=int, default=0, help="CPU threads for the timing (0 = all)")
    c.add_argument("--speed-runs", type=int, default=20)
    c.add_argument("--max-photos", type=int, default=0)
    c.add_argument("--max-domain", type=int, default=2000, help="training photos for the domain correction")
    c.add_argument("--max-drop-top30", type=float, default=0.5, help="allowed loss, percentage points")
    c.add_argument("--max-drop-top1", type=float, default=3.0)
    a = p.parse_args()
    if a.cmd == "check" and not (a.run or a.student):
        p.error("check needs --run or --student")
    {"fetch": cmd_fetch, "train": cmd_train, "check": cmd_check}[a.cmd](a)


if __name__ == "__main__":
    main()
