"""
Learn from confirmed scans.

Every card saved from a scan with "use photo" keeps the straightened photo,
the original photo and its TCGdex id (pokemon_cards.bild_karte_pfad /
bild_original_pfad / tcgdex_card_id). Those photos become extra references for
their card:

  - an ORB template in the "learned" segment, so the next scan of that card in
    the same sleeve and lighting matches it directly, and
  - a DINOv2 embedding, so the shortlist finds the card even when the catalog
    image looks different from the physical card.

Nothing is trained; the photos are simply indexed next to the catalog images.
Photos are fetched from the api's /images route (same compose network).
Per-photo features are cached in DATA_DIR/learned/, so a restart doesn't
re-download anything, and a card whose id changes or that is deleted drops out
on the next sync.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import httpx
import numpy as np
import psycopg2

from . import features
from .catalog import CatalogCard
from .store import Store, load_segment, save_segment

log = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
LEARN_DIR = DATA_DIR / "learned"
API_URL = os.getenv("API_URL", "http://api:8000").rstrip("/")
LEARN = os.getenv("LEARN", "1") == "1"
LEARN_MINUTES = float(os.getenv("LEARN_MINUTES", "15"))
SEGMENT = "learned"

# Photos from scans: an original exists only for uploads made by the scan page.
_SQL = """
SELECT id, tcgdex_card_id, bild_karte_pfad
FROM pokemon_cards
WHERE tcgdex_card_id IS NOT NULL AND bild_karte_pfad IS NOT NULL AND bild_original_pfad IS NOT NULL
"""


def _key(row_id: int, card_id: str, path: str) -> str:
    h = hashlib.sha1(f"{row_id}|{card_id}|{path}".encode()).hexdigest()[:16]
    return f"r{row_id}_{h}"


class Learner:
    def __init__(self, store: Store, shortlist, database_url: str) -> None:
        self.store, self.shortlist, self.database_url = store, shortlist, database_url
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self.status = {"enabled": LEARN, "photos": 0, "cards": 0, "added": 0, "removed": 0,
                       "failed": 0, "last_sync": None, "error": None}

    # ── public ───────────────────────────────────────────────────────────────
    def poke(self) -> None:
        """New scan photo saved: sync soon instead of at the next interval."""
        self._wake.set()

    def load_cached(self, catalog: dict[str, CatalogCard]) -> None:
        """At startup: index what earlier syncs cached, without any network."""
        if LEARN:
            self._publish(catalog)

    def loop(self, catalog_ref) -> None:
        """Background thread: sync every LEARN_MINUTES, or ~20 s after a poke;
        once a day also re-check cached photos (a re-uploaded photo keeps its path)."""
        last_check = 0.0
        while LEARN:
            woke = self._wake.wait(LEARN_MINUTES * 60)
            if woke:
                time.sleep(20)          # a scan uploads several photos: take them together
                self._wake.clear()
            try:
                daily = time.time() - last_check > 24 * 3600
                self.sync(catalog_ref(), revalidate=daily)
                if daily:
                    last_check = time.time()
            except Exception as exc:
                log.exception("Learning from scans failed")
                self.status["error"] = str(exc)

    def sync(self, catalog: dict[str, CatalogCard], revalidate: bool = False) -> None:
        with self._lock:
            with psycopg2.connect(self.database_url) as conn, conn.cursor() as cur:
                cur.execute(_SQL)
                rows = cur.fetchall()
            want = {_key(r, c, p): (r, c, p) for r, c, p in rows}
            LEARN_DIR.mkdir(parents=True, exist_ok=True)
            have = {p.stem: p for p in LEARN_DIR.glob("r*.npz")}
            if revalidate and have:
                have = self._drop_changed(have, want)
            removed = 0
            for k, p in have.items():
                if k not in want:
                    p.unlink(missing_ok=True)
                    removed += 1
            todo = [(k, v) for k, v in want.items() if k not in have]
            added = failed = 0
            if todo:
                log.info("Learning from %d new scan photo(s)", len(todo))
                with httpx.Client(timeout=20) as http:
                    for k, (row_id, card_id, path) in todo:
                        if self._learn_one(http, k, row_id, card_id, path):
                            added += 1
                        else:
                            failed += 1
            self.status.update(added=self.status["added"] + added, removed=self.status["removed"] + removed,
                               failed=failed, last_sync=datetime.now(timezone.utc).isoformat(), error=None)
            if added or removed or self.store_missing():
                self._publish(catalog)

    def _drop_changed(self, have: dict[str, Path], want: dict) -> dict[str, Path]:
        keep = {}
        with httpx.Client(timeout=10) as http:
            for k, p in have.items():
                if k not in want:
                    keep[k] = p
                    continue
                try:
                    with np.load(p) as z:
                        etag = json.loads(str(z["meta"])).get("etag")
                    r = http.head(f"{API_URL}/{want[k][2].lstrip('/')}")
                    if r.status_code == 200 and etag and r.headers.get("etag") not in (None, etag):
                        p.unlink(missing_ok=True)          # photo replaced: learn it again
                        continue
                except Exception:
                    pass
                keep[k] = p
        return keep

    def store_missing(self) -> bool:
        return not any(s.name == SEGMENT for s in self.store.snapshot().segments) and \
            any(LEARN_DIR.glob("r*.npz"))

    # ── internals ─────────────────────────────────────────────────────────────
    def _learn_one(self, http: httpx.Client, key: str, row_id: int, card_id: str, path: str) -> bool:
        rel = path.lstrip("/")
        if ".." in rel.split("/"):
            return False
        try:
            r = http.get(f"{API_URL}/{rel}")
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                log.warning("Scan photo %s: HTTP %s", rel, r.status_code)
                return False
            img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        except Exception as exc:
            log.warning("Scan photo %s not loadable: %s", rel, exc)
            return False
        f = features.extract(img) if img is not None else None
        if f is None:
            return False
        emb = np.zeros(0, np.float16)
        if self.shortlist.engine is not None:
            emb = self.shortlist.photo_embedding(img).astype(np.float16)
        h, w = img.shape[:2]
        tmp = LEARN_DIR / f"{key}.tmp.npz"
        np.savez(tmp, des=f.des, kp=f.kp_xy, phash=np.uint64(f.phash), hist=f.hist, emb=emb,
                 meta=json.dumps({"source": "scan", "card_id": card_id, "row": row_id, "w": w, "h": h,
                                  "etag": r.headers.get("etag")}))
        tmp.replace(LEARN_DIR / f"{key}.npz")
        return True

    def _publish(self, catalog: dict[str, CatalogCard]) -> None:
        """Rebuild the learned segment + embeddings from the cache and swap them in."""
        feats, metas, ids, vecs = [], [], [], []
        for p in sorted(LEARN_DIR.glob("r*.npz")):
            try:
                with np.load(p) as z:
                    meta = json.loads(str(z["meta"]))
                    feats.append(features.Features(kp_xy=z["kp"], des=z["des"], phash=int(z["phash"]),
                                                   hist=z["hist"]))
                    if z["emb"].size:
                        ids.append(meta["card_id"])
                        vecs.append(z["emb"].astype(np.float32))
                metas.append(meta)
            except Exception as exc:
                log.warning("Dropping unreadable cache file %s: %s", p.name, exc)
                p.unlink(missing_ok=True)
        if feats:
            base = LEARN_DIR / SEGMENT
            save_segment(base, feats, metas)
            seg = load_segment(base)
            seg.card_ids = [m["card_id"] if m["card_id"] in catalog else None for m in metas]
            self.store.replace(SEGMENT, seg)
        else:
            self.store.replace(SEGMENT, None)
        mat = np.stack(vecs) if vecs else np.zeros((0, 0), np.float32)
        self.shortlist.set_learned(ids, mat)
        self.status.update(photos=len(feats), cards=len({m["card_id"] for m in metas}))
        if feats:
            log.info("Learned references: %d scan photo(s) of %d card(s)", len(feats), self.status["cards"])
