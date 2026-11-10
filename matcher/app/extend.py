"""
Fill gaps in the index from the catalog's own card images (TCGdex/TCGplayer).

Runs in a background thread: every catalog card in the configured regions that
no template covers yet gets its image downloaded once, features extracted and
saved as an extension segment (chunks of CHUNK cards, so a restart resumes).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import cv2
import httpx
import numpy as np

from . import features
from .catalog import CatalogCard
from .store import Store, load_segment, save_segment

log = logging.getLogger(__name__)

CHUNK = 250
RETRY_FAILED_AFTER_S = 7 * 24 * 3600
_UA = {"User-Agent": "PokeCollect-matcher/1.0 (self-hosted)"}


class Extender:
    def __init__(self, seg_dir: Path, store: Store, regions: set[str], workers: int = 4):
        self.seg_dir, self.store, self.regions, self.workers = seg_dir, store, regions, workers
        self.failed_path = seg_dir / "failed.json"
        self.status = {"running": False, "todo": 0, "done": 0, "failed": 0, "last_run": None}
        self._lock = threading.Lock()

    # ── existing extension segments ──────────────────────────────────────────
    def load_existing(self, catalog: dict[str, CatalogCard], progress=None) -> int:
        paths = sorted(self.seg_dir.glob("ext_*.npz"))
        segs = []
        for i, p in enumerate(paths, 1):
            seg = load_segment(p.with_suffix(""))
            seg.card_ids = [m.get("card_id") if m.get("card_id") in catalog else None for m in seg.meta]
            segs.append(seg)
            if progress:
                progress(i, len(paths))
        if segs:
            self.store.add_many(segs)
        return sum(len(s) for s in segs)

    # ── failures ─────────────────────────────────────────────────────────────
    def _failed(self) -> dict[str, float]:
        try:
            return json.loads(self.failed_path.read_text())
        except Exception:
            return {}

    def _save_failed(self, failed: dict[str, float]) -> None:
        self.failed_path.write_text(json.dumps(failed))

    # ── one pass ─────────────────────────────────────────────────────────────
    def run_once(self, catalog: dict[str, CatalogCard]) -> None:
        if not self._lock.acquire(blocking=False):
            return
        try:
            known = self.store.known_card_ids()
            failed = self._failed()
            now = time.time()
            todo = [c for c in catalog.values()
                    if c.region in self.regions and c.image_url and c.image_url.startswith("https://")
                    and c.card_id not in known
                    and now - failed.get(c.card_id, 0) > RETRY_FAILED_AFTER_S]
            self.status.update(running=True, todo=len(todo), done=0, failed=0)
            log.info("Extension: %d catalog cards without a template", len(todo))
            with httpx.Client(timeout=20, headers=_UA, follow_redirects=False) as http, \
                    ThreadPoolExecutor(self.workers) as pool:
                for start in range(0, len(todo), CHUNK):
                    batch = todo[start:start + CHUNK]
                    results = list(pool.map(lambda c: (c, self._fetch_features(http, c)), batch))
                    feats, metas = [], []
                    for card, got in results:
                        if got is None:
                            failed[card.card_id] = now
                            self.status["failed"] += 1
                            continue
                        f, w, h = got
                        feats.append(f)
                        metas.append({"source": "catalog", "card_id": card.card_id,
                                      "name": card.name_en or card.name, "set": card.set_name_en,
                                      "number": card.local_id, "w": w, "h": h})
                    if feats:
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                        base = self.seg_dir / f"ext_{stamp}_{start // CHUNK:04d}"
                        save_segment(base, feats, metas)
                        seg = load_segment(base)
                        seg.card_ids = [m["card_id"] for m in metas]
                        self.store.add(seg)
                    self.status["done"] += len(feats)
                    self._save_failed(failed)
            log.info("Extension pass finished: %d added, %d failed",
                     self.status["done"], self.status["failed"])
        except Exception:
            log.exception("Extension pass failed")
        finally:
            self.status.update(running=False, last_run=datetime.now(timezone.utc).isoformat())
            self._lock.release()

    @staticmethod
    def _fetch_features(http: httpx.Client, card: CatalogCard) -> tuple[features.Features, int, int] | None:
        try:
            r = http.get(card.image_url)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                return None
            img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return None
            f = features.extract(img, min_keypoints=16)
            return (f, img.shape[1], img.shape[0]) if f is not None else None
        except Exception:
            return None
