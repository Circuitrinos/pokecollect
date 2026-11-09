"""
One-time conversion of a PokemonScanner index (card_index*.pkl, version 2)
into a store segment (base.npz + base.json).

The pickle stores its entries as __main__.TemplateEntry (app.py ran as a
script), so it is loaded with a lenient unpickler that maps unknown classes to
a plain attribute holder. Only load pickles you created yourself: unpickling
can run code.

Usage:  python -m app.convert /data/card_index1.pkl /data/segments/base
"""
from __future__ import annotations

import logging
import pickle
import sys
from pathlib import Path

import numpy as np

from .features import CROP_FRAC, NFEATURES, Features
from .store import save_segment

log = logging.getLogger(__name__)


class _Stub:
    def __setstate__(self, state):
        if isinstance(state, tuple) and len(state) == 2:
            state = {**(state[0] or {}), **(state[1] or {})}
        self.__dict__.update(state or {})


class _LenientUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module.startswith(("numpy", "builtins", "collections", "copyreg", "_codecs")):
            return super().find_class(module, name)
        return _Stub


def convert(pkl_path: Path, out_base: Path) -> int:
    log.info("Loading %s (this can take a minute) ...", pkl_path)
    with open(pkl_path, "rb") as f:
        idx = _LenientUnpickler(f).load()
    if not isinstance(idx, dict) or idx.get("version", 1) < 2:
        raise SystemExit("Expected a version-2 PokemonScanner index (with phash_int).")
    if idx.get("nfeatures") != NFEATURES or abs(float(idx.get("crop_frac", 0)) - CROP_FRAC) > 1e-6:
        raise SystemExit(f"Index parameters differ (nfeatures={idx.get('nfeatures')}, "
                         f"crop_frac={idx.get('crop_frac')}); features would not be comparable.")

    feats: list[Features] = []
    metas: list[dict] = []
    skipped = 0
    for t in idx["templates"]:
        des = getattr(t, "des", None)
        kp = getattr(t, "kp_xy", None)
        if des is None or kp is None or len(des) == 0:
            skipped += 1
            continue
        m = dict(getattr(t, "meta", None) or {})
        feats.append(Features(
            kp_xy=np.asarray(kp, np.float32),
            des=np.ascontiguousarray(des, np.uint8),
            phash=int(getattr(t, "phash_int")),
            hist=np.asarray(getattr(t, "hsv_hist"), np.float32),
        ))
        metas.append({
            "source": "pokemontcg",
            "pid": m.get("id") or getattr(t, "tid", None),
            "name": m.get("name") or getattr(t, "name", None),
            "set": m.get("set"),
            "set_id": m.get("set_id"),
            "number": m.get("number"),
            "w": int(getattr(t, "w", 0) or 0),   # template size: locates the art window
            "h": int(getattr(t, "h", 0) or 0),
        })
    out_base.parent.mkdir(parents=True, exist_ok=True)
    save_segment(out_base, feats, metas)
    log.info("Converted %d templates (%d skipped) -> %s.npz", len(feats), skipped, out_base)
    return len(feats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) != 3:
        sys.exit("usage: python -m app.convert <card_index.pkl> <out_base_without_ext>")
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
