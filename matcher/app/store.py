"""
On-disk and in-memory template store.

A store is a list of *segments*. The base segment is the converted
PokemonScanner index; extension segments hold cards added later from TCGdex
images. Each segment is one .npz (arrays) + one .json (per-template metadata).
No pickle is involved at runtime.

Segment arrays:
  des      (M, 32) uint8    all ORB descriptors, concatenated
  kp       (M, 2)  float32  matching keypoint coordinates
  offsets  (n+1,)  int64    template i owns rows offsets[i]:offsets[i+1]
  phash    (n,)    uint64   63-bit pHash per template
  hist     (n, 256) float32 HSV histogram per template
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from .features import Features


@dataclass
class Segment:
    name: str
    des: np.ndarray
    kp: np.ndarray
    offsets: np.ndarray
    phash: np.ndarray
    hist: np.ndarray
    meta: list[dict]
    card_ids: list[str | None] = field(default_factory=list)  # resolved TCGdex ids

    def __len__(self) -> int:
        return len(self.meta)

    def template(self, j: int) -> tuple[np.ndarray, np.ndarray]:
        a, b = int(self.offsets[j]), int(self.offsets[j + 1])
        return self.des[a:b], self.kp[a:b]


def save_segment(path: Path, feats: Iterable[Features], metas: list[dict]) -> None:
    feats = list(feats)
    if len(feats) != len(metas):
        raise ValueError("features/meta length mismatch")
    counts = [len(f.des) for f in feats]
    offsets = np.zeros(len(feats) + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    des = np.concatenate([f.des for f in feats]) if feats else np.zeros((0, 32), np.uint8)
    kp = np.concatenate([f.kp_xy for f in feats]) if feats else np.zeros((0, 2), np.float32)
    phash = np.array([f.phash for f in feats], dtype=np.uint64)
    hist = np.stack([f.hist for f in feats]) if feats else np.zeros((0, 256), np.float32)
    tmp = path.with_suffix(".tmp.npz")
    np.savez(tmp, des=des, kp=kp, offsets=offsets, phash=phash, hist=hist)
    tmp.replace(path.with_suffix(".npz"))
    path.with_suffix(".json").write_text(json.dumps(metas, ensure_ascii=False), encoding="utf-8")


def load_segment(path: Path) -> Segment:
    with np.load(path.with_suffix(".npz")) as z:
        arrays = {k: z[k] for k in ("des", "kp", "offsets", "phash", "hist")}
    meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    return Segment(name=path.stem, meta=meta, **arrays)


@dataclass
class Snapshot:
    """Immutable view used by one /match call (safe while extension runs)."""
    segments: list[Segment]
    phash: np.ndarray      # (T,) uint64, only templates with a card id
    hist: np.ndarray       # (T, 256) float32
    seg_of: np.ndarray     # (T,) int32
    idx_in_seg: np.ndarray  # (T,) int32
    card_id: list[str]     # (T,)
    by_card: dict[str, list[int]] = field(default_factory=dict)  # card id -> template rows

    def __len__(self) -> int:
        return len(self.card_id)


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._segments: list[Segment] = []
        self._snap = Snapshot([], np.zeros(0, np.uint64), np.zeros((0, 256), np.float32),
                              np.zeros(0, np.int32), np.zeros(0, np.int32), [])

    def snapshot(self) -> Snapshot:
        return self._snap

    def known_card_ids(self) -> set[str]:
        return set(self._snap.card_id)

    def add(self, seg: Segment) -> None:
        self.add_many([seg])

    def add_many(self, segs: list[Segment]) -> None:
        """Add segments with one rebuild (startup loads ~80 at once)."""
        with self._lock:
            self._segments.extend(segs)
            self._rebuild()

    def _rebuild(self) -> None:
        ph, hi, so, ix, cid = [], [], [], [], []
        for s_no, seg in enumerate(self._segments):
            keep = [j for j, c in enumerate(seg.card_ids) if c]
            if not keep:
                continue
            ph.append(seg.phash[keep])
            hi.append(seg.hist[keep])
            so.append(np.full(len(keep), s_no, np.int32))
            ix.append(np.asarray(keep, np.int32))
            cid.extend(seg.card_ids[j] for j in keep)
        if ph:
            by_card: dict[str, list[int]] = {}
            for t, c in enumerate(cid):
                by_card.setdefault(c, []).append(t)
            self._snap = Snapshot(list(self._segments), np.concatenate(ph), np.concatenate(hi),
                                  np.concatenate(so), np.concatenate(ix), cid, by_card)
        else:
            self._snap = Snapshot(list(self._segments), np.zeros(0, np.uint64),
                                  np.zeros((0, 256), np.float32), np.zeros(0, np.int32),
                                  np.zeros(0, np.int32), [])

    def stats(self) -> dict:
        segs = self._segments
        return {
            "segments": len(segs),
            "templates": sum(len(s) for s in segs),
            "usable": len(self._snap),
        }
