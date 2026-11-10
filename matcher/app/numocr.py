"""
Read the printed collector number ("136/197", "TG05/TG30") from a straightened
card. Only used to break ties between a few art-matched candidates, so it
returns every plausible reading rather than a single guess.
"""
from __future__ import annotations

import re
import shutil

import cv2
import numpy as np

from .detect import PAD

_NUM_RE = re.compile(r"([A-Z]{0,3}\d{1,3})\s*/\s*([A-Z]{0,3}\d{1,3})")
_WHITELIST = "0123456789/TGSVHRC"
_CONFIGS = [f"--oem 3 --psm {p} -c tessedit_char_whitelist={_WHITELIST}" for p in (7, 6)]


def available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def _rois(card: np.ndarray, upside_down: bool) -> list[np.ndarray]:
    """Bottom-left and bottom-right number areas; likely orientation first."""
    out = []
    flipped = cv2.rotate(card, cv2.ROTATE_180)
    for img in ((flipped, card) if upside_down else (card, flipped)):
        h, w = img.shape[:2]
        y0, y1 = int(PAD + 0.90 * (h - 2 * PAD)), int(PAD + 0.985 * (h - 2 * PAD))
        for x0f, x1f in ((0.02, 0.45), (0.55, 0.98)):
            x0, x1 = int(PAD + x0f * (w - 2 * PAD)), int(PAD + x1f * (w - 2 * PAD))
            out.append(img[y0:y1, x0:x1])
    return out


def _prep(roi: np.ndarray) -> list[np.ndarray]:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    big = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    big = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(big)
    _, bw = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return [big, bw, cv2.bitwise_not(bw)]


def read_numbers(card: np.ndarray, upside_down: bool = False,
                 wanted: set[str] | None = None) -> list[tuple[str, str]]:
    """
    (number, total) pairs Tesseract finds, e.g. [('136', '197')]. Stops as soon
    as a number in `wanted` (num_key form, e.g. 'TG5', '136') shows up, so the
    common case costs one or two Tesseract calls instead of 24.
    """
    if not available():
        return []
    import pytesseract

    from .catalog import num_key
    found: list[tuple[str, str]] = []
    for roi in _rois(card, upside_down):
        if roi.size == 0:
            continue
        for img in _prep(roi):
            for cfg in _CONFIGS:
                try:
                    txt = pytesseract.image_to_string(img, config=cfg)
                except Exception:
                    continue
                for m in _NUM_RE.finditer(txt.replace(" ", "").upper()):
                    pair = (m.group(1), m.group(2))
                    if pair not in found:
                        found.append(pair)
                    if wanted and num_key(pair[0]) in wanted:
                        return [pair] + [p for p in found if p != pair]
    return found


def warm_up() -> None:
    """One tiny Tesseract call, so the first real read doesn't pay for loading
    the binary and language data from a cold disk."""
    if not available():
        return
    import pytesseract
    img = np.full((60, 200), 255, np.uint8)
    cv2.putText(img, "123/197", (8, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
    pytesseract.image_to_string(img, config=_CONFIGS[0])
