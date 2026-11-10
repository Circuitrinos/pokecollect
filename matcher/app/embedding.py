"""
DINOv2 shortlist: which catalog cards are worth the (expensive) ORB + RANSAC
check for a straightened photo?

Measured on real binder photos (embed_experiment): the right card is in the
DINOv2 top 5 for ~96 % of photos, while the old pHash prefilter's top 300
held it for ~64 %. So when the model is present, the candidates are

    DINOv2 top-EMBED_TOP cards  +  pHash top-PHASH_TOP templates

instead of the pHash top 300. Without the model file nothing changes (pHash
only, as before); while catalog embeddings are still incomplete, the full pHash
preselection stays on as well, so no card becomes unreachable.

Files in DATA_DIR (/data):
  dinov2_vits14_cls.onnx  the model (export_matcher.py on a PC with PyTorch)
  dinov2_catalog.npz      catalog embeddings computed on that PC (optional
                          bootstrap; read once, then merged into emb/)
  emb/catalog_emb.npz     the matcher's own copy, extended in the background
                          for cards that have no embedding yet
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import httpx
import numpy as np

from . import timing
from .catalog import CatalogCard

log = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MODEL_PATH = Path(os.getenv("EMBED_MODEL", str(DATA_DIR / "dinov2_vits14_cls.onnx")))
BOOTSTRAP = Path(os.getenv("EMBED_BOOTSTRAP", str(DATA_DIR / "dinov2_catalog.npz")))
EMB_DIR = DATA_DIR / "emb"
EMB_FILE = EMB_DIR / "catalog_emb.npz"
EMBED_TOP = int(os.getenv("EMBED_TOP", "30"))         # cards from the embedding shortlist
LEARNED_TOP = int(os.getenv("LEARNED_TOP", "5"))          # cards added from learned scan photos
LEARNED_MIN_SIM = float(os.getenv("LEARNED_MIN_SIM", "0.5"))
PHASH_TOP = int(os.getenv("PHASH_TOP", "10"))         # templates from pHash on top of that
MIN_COVERAGE = float(os.getenv("EMBED_MIN_COVERAGE", "0.98"))
THREADS = int(os.getenv("EMBED_THREADS", "0"))        # 0 = onnxruntime default
IN_W, IN_H = 224, 308
PAD = 12                                              # detect.warp's white border
BACKFILL_CHUNK = 64
_UA = {"User-Agent": "PokeCollect-matcher/1.0 (self-hosted)"}


# ── preprocessing (identical to embed_experiment) ────────────────────────────

def autolevel(img: np.ndarray) -> np.ndarray:
    """Undo haze/glare wash-out and colour cast: stretch each channel's 1st-99th percentile."""
    s = 256 / max(img.shape[:2])
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
    lo = np.percentile(small, 1, axis=(0, 1)).astype(np.float32)
    hi = np.percentile(small, 99, axis=(0, 1)).astype(np.float32)
    lut = np.clip((np.arange(256, dtype=np.float32)[:, None] - lo) / np.maximum(hi - lo, 1.0) * 255.0, 0, 255)
    return np.dstack([cv2.LUT(img[..., c], lut[:, c].astype(np.uint8)) for c in range(3)])


def prep(img: np.ndarray) -> np.ndarray:
    """BGR image -> (3, 308, 224) float32 RGB in [0,1]; the model normalises itself."""
    x = cv2.cvtColor(cv2.resize(img, (IN_W, IN_H), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(x.transpose(2, 0, 1), dtype=np.float32) / 255.0


# ── model ────────────────────────────────────────────────────────────────────

class Engine:
    def __init__(self, path: Path):
        import onnxruntime as ort
        so = ort.SessionOptions()
        if THREADS > 0:
            so.intra_op_num_threads = THREADS
        self.sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
        self.input = self.sess.get_inputs()[0].name
        self._lock = threading.Lock()       # one inference at a time; it uses all cores anyway
        self.dim = int(self.embed([np.zeros((IN_H, IN_W, 3), np.uint8)]).shape[1])

    def embed(self, imgs: list[np.ndarray]) -> np.ndarray:
        x = np.stack([prep(i) for i in imgs])
        t = time.perf_counter()
        with self._lock:
            timing.add("embed_wait", time.perf_counter() - t)   # e.g. behind a backfill batch
            out = self.sess.run(None, {self.input: x})[0]
        out = out.astype(np.float32)
        return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)


# ── index ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EmbSnapshot:
    ids: list[str]
    mat: np.ndarray          # (N, D) float32, L2-normalised


class EmbIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = EmbSnapshot([], np.zeros((0, 0), np.float32))
        self._pos: dict[str, int] = {}

    def snapshot(self) -> EmbSnapshot:
        return self._snap

    def __len__(self) -> int:
        return len(self._snap.ids)

    def has(self, card_id: str) -> bool:
        return card_id in self._pos

    def add(self, ids: list[str], vecs: np.ndarray, save: bool = True) -> None:
        with self._lock:
            old = self._snap
            keep = [i for i, c in enumerate(ids) if c not in self._pos]
            if not keep:
                return
            new_ids = old.ids + [ids[i] for i in keep]
            new = vecs[keep].astype(np.float32)
            mat = new if old.mat.size == 0 else np.concatenate([old.mat, new])
            self._snap = EmbSnapshot(new_ids, mat)
            self._pos = {c: i for i, c in enumerate(new_ids)}
            if save:
                self._save()

    def _save(self) -> None:
        EMB_DIR.mkdir(parents=True, exist_ok=True)
        tmp = EMB_DIR / "catalog_emb.tmp.npz"
        np.savez(tmp, ids=json.dumps(self._snap.ids), emb=self._snap.mat.astype(np.float16))
        tmp.replace(EMB_FILE)

    def load(self, path: Path) -> int:
        with np.load(path) as z:
            raw = z["ids"]
            ids = json.loads(raw.item() if raw.ndim == 0 else str(raw))
            emb = z["emb"].astype(np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9
        before = len(self)
        self.add(ids, emb, save=False)
        return len(self) - before

    def top(self, q: np.ndarray, n: int) -> list[str]:
        """Best n card ids for query vectors q (k, D): a card scores its best match."""
        snap = self._snap
        if not snap.ids:
            return []
        s = (q @ snap.mat.T).max(axis=0)
        n = min(n, len(s))
        idx = np.argpartition(-s, n - 1)[:n]
        return [snap.ids[i] for i in idx[np.argsort(-s[idx])]]


# ── service ──────────────────────────────────────────────────────────────────

class Shortlist:
    """The model + index + background backfill, as one object for main.py."""

    def __init__(self) -> None:
        self.engine: Engine | None = None
        self.index = EmbIndex()
        # embeddings of users' confirmed scan photos (learn.py), several per card allowed
        self._learned: tuple[list[str], np.ndarray] = ([], np.zeros((0, 0), np.float32))
        self.coverage = 0.0
        self.status = {"active": False, "model": str(MODEL_PATH), "error": None, "cards": 0,
                       "coverage": 0.0, "backfill": {"running": False, "todo": 0, "done": 0, "failed": 0,
                                                     "last_run": None}}

    def load(self) -> None:
        if not MODEL_PATH.exists():
            log.info("No %s: shortlist stays pHash only", MODEL_PATH.name)
            return
        try:
            self.engine = Engine(MODEL_PATH)
            if EMB_FILE.exists():
                self.index.load(EMB_FILE)
            if BOOTSTRAP.exists():
                added = self.index.load(BOOTSTRAP)
                if added:
                    self.index._save()
                    log.info("Bootstrap: %d catalog embeddings from %s", added, BOOTSTRAP.name)
            log.info("DINOv2 shortlist ready (%d-dim, %d cards embedded)", self.engine.dim, len(self.index))
        except Exception as exc:
            log.exception("Embedding model failed to load; staying pHash only")
            self.engine = None
            self.status["error"] = str(exc)
        self._update_status()

    @property
    def active(self) -> bool:
        return self.engine is not None and len(self.index) > 0

    def update_coverage(self, known_ids: set[str]) -> None:
        self.coverage = (sum(1 for c in known_ids if self.index.has(c)) / len(known_ids)) if known_ids else 0.0
        self._update_status()

    def _update_status(self) -> None:
        self.status.update(active=self.active, cards=len(self.index), coverage=round(self.coverage, 4))

    def query(self, card_bgr: np.ndarray) -> np.ndarray | None:
        """Embeddings of a straightened card (detect.warp output), upright and upside down."""
        if self.engine is None:
            return None
        h, w = card_bgr.shape[:2]
        core = card_bgr[PAD:h - PAD, PAD:w - PAD] if h > 4 * PAD and w > 4 * PAD else card_bgr
        core = autolevel(core)
        return self.engine.embed([core, cv2.rotate(core, cv2.ROTATE_180)])

    def photo_embedding(self, card_bgr: np.ndarray) -> np.ndarray:
        """Embedding of a straightened scan photo, prepared like a query (upright only)."""
        return self.engine.embed([autolevel(card_bgr)])[0]   # the app's crop has no white border

    def set_learned(self, ids: list[str], mat: np.ndarray) -> None:
        self._learned = (list(ids), mat.astype(np.float32) if mat.size else np.zeros((0, 0), np.float32))
        self.status["learned_photos"] = len(ids)

    def shortlist(self, card_bgr: np.ndarray) -> tuple[list[str], int] | None:
        """(card ids, how many pHash templates to add) or None = old behaviour."""
        if not self.active:
            return None
        q = self.query(card_bgr)
        if q is None:
            return None
        ids = self.index.top(q, EMBED_TOP)
        l_ids, l_mat = self._learned
        if l_ids:
            s = (q @ l_mat.T).max(axis=0)
            for i in np.argsort(-s)[:LEARNED_TOP]:
                if s[i] >= LEARNED_MIN_SIM and l_ids[i] not in ids:
                    ids.append(l_ids[i])
        return ids, (PHASH_TOP if self.coverage >= MIN_COVERAGE else -1)

    # ── backfill: embed catalog cards the index doesn't have yet ──────────────
    def backfill(self, catalog: dict[str, CatalogCard], known_ids: set[str]) -> None:
        if self.engine is None or self.status["backfill"]["running"]:
            return
        bf = self.status["backfill"]
        todo = [catalog[c] for c in known_ids
                if c in catalog and not self.index.has(c) and (catalog[c].image_url or "").startswith("https://")]
        bf.update(running=True, todo=len(todo), done=0, failed=0)
        try:
            if todo:
                log.info("Embedding backfill: %d cards", len(todo))
            with httpx.Client(timeout=20, headers=_UA, follow_redirects=False) as http, \
                    ThreadPoolExecutor(4) as pool:
                for s in range(0, len(todo), BACKFILL_CHUNK):
                    batch = todo[s:s + BACKFILL_CHUNK]
                    imgs = list(pool.map(lambda c: _download(http, c.image_url), batch))
                    ok = [(c.card_id, i) for c, i in zip(batch, imgs) if i is not None]
                    bf["failed"] += len(batch) - len(ok)
                    if ok:
                        vecs = np.concatenate([self.engine.embed([i for _, i in ok[k:k + 16]])
                                               for k in range(0, len(ok), 16)])
                        self.index.add([c for c, _ in ok], vecs)
                    bf["done"] += len(ok)
                    self.update_coverage(known_ids)
        except Exception:
            log.exception("Embedding backfill failed")
        finally:
            bf.update(running=False, last_run=datetime.now(timezone.utc).isoformat())
            self.update_coverage(known_ids)


def _download(http: httpx.Client, url: str) -> np.ndarray | None:
    for attempt in range(2):
        try:
            r = http.get(url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                return cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
            return None
        except Exception:
            time.sleep(1 + attempt)
    return None
