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

Domain correction (EMBED_DOMAIN, on by default once there are enough learned
scan photos): photos and catalog images live in different corners of the
embedding space — a shiny photo lands next to old holo scans whose catalog
image shows foil sheen, whatever its artwork. So the photo is centred on the
mean of the users' confirmed scan photos, the catalog on its own mean, and
each catalog card loses half its "photo hubness" (mean similarity to its 10
nearest scan photos of other cards). Measured with embed_experiment on 914
scan crops: top-1 72 % -> 92 %, true card in the shortlist 98.1 % -> 99.7 %;
on 783 detector crops 74 % -> 90 % and 96.2 % -> 98.9 %.

Photo queries (EMBED_QUERY_MODEL, default /data/student.onnx when present): a
small student network (tools/distill_student.py) taught to map a card photo
straight to DINOv2's vector of that card's catalog image. It replaces the
teacher only for photos — scan queries and the learned scan photos — so the
catalog index stays the teacher's. ~6× less CPU per photo; the domain
correction stays off for it (it learned the photo look itself; on the check
it made the student slightly worse). The file is watched: a new student
(the nightly trainer, or a copy) is swapped in without a restart, and the
learned photos are embedded again for it in the background.

Files in DATA_DIR (/data):
  dinov2_vits14_cls.onnx  the model (export_matcher.py on a PC with PyTorch)
  student.onnx            optional photo model (tools/distill_student.py)
  dinov2_catalog.npz      catalog embeddings computed on that PC (optional
                          bootstrap; read once, then merged into emb/)
  emb/catalog_emb.npz     the matcher's own copy, extended in the background
                          for cards that have no embedding yet
"""
from __future__ import annotations

import hashlib
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
DOMAIN = os.getenv("EMBED_DOMAIN", "1") != "0"        # domain correction (see above)
DOMAIN_MIN_PHOTOS = int(os.getenv("EMBED_DOMAIN_MIN_PHOTOS", "50"))
HUB_K = int(os.getenv("EMBED_HUB_K", "10"))
MIN_COVERAGE = float(os.getenv("EMBED_MIN_COVERAGE", "0.98"))
THREADS = int(os.getenv("EMBED_THREADS", "0"))        # 0 = onnxruntime default
QUERY_MODEL_PATH = Path(os.getenv("EMBED_QUERY_MODEL", str(DATA_DIR / "student.onnx")))
QUERY_DOMAIN = os.getenv("EMBED_QUERY_DOMAIN", "0") == "1"   # domain correction for the student too
TEACHER_ID = "teacher"                                    # learned photos embedded by the teacher
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


def prep(img: np.ndarray, size: tuple[int, int] = (IN_W, IN_H)) -> np.ndarray:
    """BGR image -> (3, h, w) float32 RGB in [0,1] (default 308×224); the model normalises itself."""
    x = cv2.cvtColor(cv2.resize(img, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(x.transpose(2, 0, 1), dtype=np.float32) / 255.0


# ── model ────────────────────────────────────────────────────────────────────

class Engine:
    def __init__(self, path: Path, ident: str = TEACHER_ID):
        import onnxruntime as ort
        so = ort.SessionOptions()
        if THREADS > 0:
            so.intra_op_num_threads = THREADS
        self.sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
        inp = self.sess.get_inputs()[0]
        self.input = inp.name
        hw = inp.shape[2:]                  # fixed (h, w) from the export; else the teacher's size
        self.size = (int(hw[1]), int(hw[0])) if len(hw) == 2 and all(isinstance(v, int) for v in hw) \
            else (IN_W, IN_H)
        self.ident = ident
        self._lock = threading.Lock()       # one inference at a time; it uses all cores anyway
        self.dim = int(self.embed([np.zeros((self.size[1], self.size[0], 3), np.uint8)]).shape[1])

    def embed(self, imgs: list[np.ndarray]) -> np.ndarray:
        x = np.stack([prep(i, self.size) for i in imgs])
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


@dataclass(frozen=True)
class Domain:
    """Catalog scored the photo way (see the module docstring)."""
    key: tuple
    ids: list[str]
    mu_photo: np.ndarray     # (1, D) mean scan photo
    cat: np.ndarray          # (N, D) catalog minus its mean, L2-normalised
    penalty: np.ndarray      # (N,) half each card's photo hubness


def _norm(x: np.ndarray) -> np.ndarray:
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-9)


def build_domain(snap: EmbSnapshot, photo_ids: list[str], photos: np.ndarray, key: tuple = (),
                 k: int = HUB_K, chunk: int = 2048) -> Domain | None:
    """
    Domain correction from the learned scan photos (ids = their confirmed cards).
    A card's own photos don't count towards its hubness, so being the right
    answer is never penalised. None with too few photos.
    """
    if not snap.ids or len(photo_ids) < DOMAIN_MIN_PHOTOS or photos.shape[1] != snap.mat.shape[1]:
        return None
    mu = photos.mean(axis=0, keepdims=True)
    cat = _norm(snap.mat - snap.mat.mean(axis=0, keepdims=True)).astype(np.float32)
    ph = _norm(photos - mu).astype(np.float32)
    pos = {c: i for i, c in enumerate(snap.ids)}
    owner = np.array([pos.get(c, -1) for c in photo_ids])
    k = min(k, len(photo_ids) - 1)
    pen = np.empty(len(cat), np.float32)
    for s in range(0, len(cat), chunk):
        sims = cat[s:s + chunk] @ ph.T
        sims[owner[None, :] == np.arange(s, s + len(sims))[:, None]] = -np.inf
        pen[s:s + chunk] = np.partition(sims, -k, axis=1)[:, -k:].mean(axis=1)
    pen[~np.isfinite(pen)] = 0.0           # a card with (almost) all the photos
    return Domain(key, snap.ids, mu.astype(np.float32), cat, pen / 2)


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

    def top(self, q: np.ndarray, n: int, domain: Domain | None = None) -> list[str]:
        """Best n card ids for query vectors q (k, D): a card scores its best match."""
        if domain is not None:
            ids = domain.ids
            s = (_norm(q - domain.mu_photo) @ domain.cat.T).max(axis=0) - domain.penalty
        else:
            ids = self._snap.ids
            if not ids:
                return []
            s = (q @ self._snap.mat.T).max(axis=0)
        n = min(n, len(s))
        idx = np.argpartition(-s, n - 1)[:n]
        return [ids[i] for i in idx[np.argsort(-s[idx])]]


# ── service ──────────────────────────────────────────────────────────────────

def file_stamp(path: Path) -> tuple[float, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def file_ident(path: Path) -> str:
    """Short content hash: names the model the learned photos were embedded with."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return "student-" + h.hexdigest()[:12]


class Shortlist:
    """The model + index + background backfill, as one object for main.py."""

    def __init__(self) -> None:
        self.engine: Engine | None = None
        self.query_engine: Engine | None = None     # the student for photos (None: the teacher does it)
        self._q_seen: tuple[float, int] | None = None
        self._q_pending: tuple[float, int] | None = None
        self.index = EmbIndex()
        # embeddings of users' confirmed scan photos (learn.py), several per card allowed
        self._learned: tuple[list[str], np.ndarray] = ([], np.zeros((0, 0), np.float32))
        self._learned_ver = 0
        self._learned_model = TEACHER_ID
        self._domain: Domain | None = None
        self._domain_lock = threading.Lock()
        self.coverage = 0.0
        self.status = {"active": False, "model": str(MODEL_PATH), "error": None, "cards": 0,
                       "coverage": 0.0, "backfill": {"running": False, "todo": 0, "done": 0, "failed": 0,
                                                     "last_run": None},
                       "photos": {"model": "teacher", "path": str(QUERY_MODEL_PATH), "error": None}}

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
        if self.engine is not None:
            self.reload_query()
        self._update_status()

    # ── photo model (student) ──────────────────────────────────────────────────
    @property
    def query_id(self) -> str:
        """Which model embeds photos now: TEACHER_ID or the student's content hash."""
        q = self.query_engine
        return q.ident if q is not None else TEACHER_ID

    def _photo_engine(self) -> Engine | None:
        return self.query_engine or self.engine

    def reload_query(self) -> bool:
        """
        (Re)load the student when its file appeared, changed or went away; True
        if the photo model changed. A file still being written (stamp different
        from the last look) waits for the next call; a bad or mismatched file
        keeps whatever was in use.
        """
        if self.engine is None:
            return False
        stamp = file_stamp(QUERY_MODEL_PATH)
        if stamp == self._q_seen:
            return False
        ph = self.status["photos"]
        if stamp is None:
            self._q_seen = self._q_pending = None
            if self.query_engine is None:
                return False
            self.query_engine = None
            ph.update(model="teacher", error=None, size=None, loaded=None)
            log.info("Photo model %s removed: photos go through DINOv2 again", QUERY_MODEL_PATH.name)
            return True
        if self.query_engine is not None and stamp != self._q_pending:
            self._q_pending = stamp              # changed since the last look: let the copy finish
            return False
        self._q_seen, self._q_pending = stamp, None
        t = time.perf_counter()
        try:
            eng = Engine(QUERY_MODEL_PATH, file_ident(QUERY_MODEL_PATH))
        except Exception as exc:  # noqa: BLE001 — any unreadable/odd model file: keep the current one
            log.warning("Photo model %s not usable (%s); keeping %s", QUERY_MODEL_PATH.name, exc, self.query_id)
            ph["error"] = str(exc)
            return False
        if eng.dim != self.engine.dim:
            msg = f"{eng.dim}-dim output, the catalog index is {self.engine.dim}-dim"
            log.warning("Photo model %s doesn't fit (%s); keeping %s", QUERY_MODEL_PATH.name, msg, self.query_id)
            ph["error"] = msg
            return False
        if self.query_engine is not None and eng.ident == self.query_engine.ident:
            return False                         # touched, same content
        probe = [np.zeros((eng.size[1], eng.size[0], 3), np.uint8)] * 2
        eng.embed(probe)                         # warm up the 2-image batch like the teacher's
        t1 = time.perf_counter()
        eng.embed(probe)
        ms = (time.perf_counter() - t1) * 500
        self.query_engine = eng
        ph.update(model=eng.ident, error=None, size=list(eng.size),
                  loaded=datetime.now(timezone.utc).isoformat(), ms_per_photo=round(ms, 1))
        log.info("Photo model: %s (%s, %d×%d, %.0f ms per photo, loaded in %.1fs)%s", QUERY_MODEL_PATH.name,
                 eng.ident, eng.size[0], eng.size[1], ms, time.perf_counter() - t,
                 "" if QUERY_DOMAIN else "; domain correction off for it")
        return True

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
        qs = self.query_many([card_bgr], ["both"])
        return qs[0] if qs else None

    def query_many(self, cards: list[np.ndarray], ways: list[str]) -> list[np.ndarray] | None:
        """
        Several straightened cards in one model run. ways[i]: "up" (the card lies
        upright in its crop — one image), "down" (upside down: the crop turned
        round), "both" (unknown: both, as query()). → (k, D) per card.
        """
        eng = self._photo_engine()
        if eng is None:
            return None
        imgs: list[np.ndarray] = []
        spans: list[tuple[int, int]] = []
        for card_bgr, way in zip(cards, ways):
            h, w = card_bgr.shape[:2]
            core = card_bgr[PAD:h - PAD, PAD:w - PAD] if h > 4 * PAD and w > 4 * PAD else card_bgr
            core = autolevel(core)
            start = len(imgs)
            if way in ("up", "both"):
                imgs.append(core)
            if way in ("down", "both"):
                imgs.append(cv2.rotate(core, cv2.ROTATE_180))
            spans.append((start, len(imgs)))
        if not imgs:
            return []
        out = eng.embed(imgs)
        return [out[a:b] for a, b in spans]

    def photo_embedding(self, card_bgr: np.ndarray) -> tuple[np.ndarray, str]:
        """Embedding of a straightened scan photo, prepared like a query (upright only),
        and which model made it (photos of a different model can't be compared)."""
        eng = self._photo_engine()
        return eng.embed([autolevel(card_bgr)])[0], eng.ident   # the app's crop has no white border

    def set_learned(self, ids: list[str], mat: np.ndarray, model: str | None = None) -> None:
        """Learned photo vectors and which model embedded them (default: the photo model now).
        They're only used while that is still the photo model — after a swap they wait
        for the learner to embed the photos again."""
        self._learned = (list(ids), mat.astype(np.float32) if mat.size else np.zeros((0, 0), np.float32))
        self._learned_model = model or self.query_id
        self._learned_ver += 1
        self.status["learned_photos"] = len(ids)

    def _learned_now(self) -> tuple[list[str], np.ndarray]:
        if self._learned_model != self.query_id:
            return [], np.zeros((0, 0), np.float32)
        return self._learned

    def domain(self) -> Domain | None:
        """The domain correction for the current catalog index and learned photos (rebuilt when either grows)."""
        if not DOMAIN or (self.query_engine is not None and not QUERY_DOMAIN):
            return None
        snap = self.index.snapshot()
        key = (len(snap.ids), self._learned_ver, self.query_id)
        d = self._domain
        if d is not None and d.key == key:
            return d if d.ids else None     # "not enough photos" for this key: no correction
        with self._domain_lock:
            if self._domain is None or self._domain.key != key:
                l_ids, l_mat = self._learned_now()
                t = time.perf_counter()
                built = build_domain(snap, l_ids, l_mat, key)
                # remember "not enough photos" for this key too, so it isn't rebuilt per query
                self._domain = built or Domain(key, [], np.zeros((1, 0), np.float32),
                                               np.zeros((0, 0), np.float32), np.zeros(0, np.float32))
                self.status["domain"] = {"active": built is not None, "photos": len(l_ids),
                                         "min_photos": DOMAIN_MIN_PHOTOS,
                                         "build_ms": round(1000 * (time.perf_counter() - t))}
            d = self._domain
        return d if d.ids else None

    def shortlist(self, card_bgr: np.ndarray) -> tuple[list[str], int] | None:
        """(card ids, how many pHash templates to add) or None = old behaviour."""
        if not self.active:
            return None
        q = self.query(card_bgr)
        if q is None:
            return None
        return self._pick(q)

    def shortlist_many(self, cards: list[np.ndarray], ways: list[str]) -> list[tuple[list[str], int] | None]:
        """shortlist() for several cards in one model run; ways as in query_many()."""
        qs = self.query_many(cards, ways) if self.active else None
        if qs is None:
            return [None] * len(cards)
        return [self._pick(q) if len(q) else None for q in qs]

    def _pick(self, q: np.ndarray) -> tuple[list[str], int]:
        ids = self.index.top(q, EMBED_TOP, self.domain())
        l_ids, l_mat = self._learned_now()
        if l_ids and l_mat.shape[1] == q.shape[1]:
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
