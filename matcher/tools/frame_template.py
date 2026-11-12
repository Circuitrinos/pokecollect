"""
Rebuild app/frame_template.png — the mean card frame the generic card finder
(app/frame.py) fits to pictures.

Every catalog image is turned into a desaturated gradient map and averaged;
the artworks wash out and the shared frame (border, name bar, attack box,
bottom line) stays. More cards and more eras give a steadier frame.

    # from a scan-dataset export (uses its references/ catalog images)
    python tools/frame_template.py --dataset scans.zip
    # from a folder of upright, tightly cropped card images
    python tools/frame_template.py --images /path/to/cards
    # write somewhere else
    python tools/frame_template.py --dataset scans.zip --out /tmp/frame.png
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import frame  # noqa: E402

EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)


def from_dataset(path: Path):
    if path.is_dir():
        for p in sorted((path / "references").glob("*")):
            if p.suffix.lower() in EXTS:
                yield _decode(p.read_bytes())
        return
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            if name.startswith("references/") and Path(name).suffix.lower() in EXTS:
                yield _decode(z.read(name))


def from_images(path: Path):
    for p in sorted(path.rglob("*")):
        if p.suffix.lower() in EXTS:
            yield _decode(p.read_bytes())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dataset", type=Path, help="scan-dataset zip or unpacked folder")
    src.add_argument("--images", type=Path, help="folder of card images")
    ap.add_argument("--out", type=Path, default=frame.TEMPLATE_PATH)
    a = ap.parse_args()
    images = list(from_dataset(a.dataset) if a.dataset else from_images(a.images))
    images = [im for im in images if im is not None and 1.2 < im.shape[0] / im.shape[1] < 1.6]
    mean = frame.build_template(images)
    if mean is None:
        print("no usable card images found", file=sys.stderr)
        return 1
    frame.save_template(mean, a.out)
    print(f"{a.out}: mean frame of {len(images)} cards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
