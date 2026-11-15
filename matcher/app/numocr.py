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
# a number without a total, letters in front kept apart: "SVE 016" (energies) reads "SV 16" or "SV16"
_BARE_RE = re.compile(r"(?<![0-9/])([A-Z]{0,4}) ?(\d{1,3})(?![0-9/])")
_WHITELIST = "0123456789/TGSVHRCEM"
_CONFIGS = [f"--oem 3 --psm {p} -c tessedit_char_whitelist={_WHITELIST}" for p in (7, 6)]
# bare numbers (energies: small bold italic "016" beside the set logo): sparse text, a taller strip
# and adaptive thresholds read them far more often (Brian's SVE 016 close-ups: 15 of 16 vs 2 of 16)
_BARE_CONFIGS = [f"--oem 3 --psm {p} -c tessedit_char_whitelist={_WHITELIST}" for p in (11, 6)]
_BAND = (0.90, 0.985)
_XS = ((0.02, 0.45), (0.55, 0.98))
_BARE_BAND = (0.88, 0.99)


def available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def _rois(card: np.ndarray, upside_down: bool, band: tuple[float, float] = _BAND,
          both_ways: bool = True) -> list[np.ndarray]:
    """Bottom-left and bottom-right number areas; likely orientation first (only that one
    if both_ways is False — a close-up's orientation is known)."""
    out = []
    flipped = cv2.rotate(card, cv2.ROTATE_180)
    ways = (flipped, card) if upside_down else (card, flipped)
    for img in ways if both_ways else ways[:1]:
        h, w = img.shape[:2]
        y0, y1 = (min(h, max(0, int(PAD + f * (h - 2 * PAD)))) for f in band)
        for x0f, x1f in _XS:
            x0, x1 = (min(w, max(0, int(PAD + f * (w - 2 * PAD)))) for f in (x0f, x1f))
            out.append(img[y0:y1, x0:x1])
    return out


def _prep(roi: np.ndarray, bare: bool = False) -> list[np.ndarray]:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    raw = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    big = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(raw)
    _, bw = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out = [big, bw, cv2.bitwise_not(bw)]
    if bare:        # uneven light over the strip (sleeve, holo border): thresholds per neighbourhood
        out = [cv2.adaptiveThreshold(cv2.GaussianBlur(raw, (3, 3), 0), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 41, 12),
               cv2.adaptiveThreshold(cv2.GaussianBlur(raw, (5, 5), 0), 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                     cv2.THRESH_BINARY, 61, 15)] + out[:2]
    return out


def _fits(letters: str, code: str) -> bool:
    """Letters read before a number fit a printed set code ("SV" ~ "SVE")?"""
    return len(letters) >= 2 and len(code) >= 2 and (code.startswith(letters) or letters.startswith(code))


def _keys(code: str) -> list[str]:
    """num_key forms to try: as read ('TG5'), then without letters in front —
    a set code or regulation mark read into it ('SV16' → '16', 'E136' → '136')."""
    from .catalog import num_key
    out = [num_key(code)]
    digits = code.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if digits and digits != code and num_key(digits) not in out:
        out.append(num_key(digits))
    return out


def read_numbers(card: np.ndarray, upside_down: bool = False,
                 wanted: set[str] | None = None, bare: bool = False,
                 prefixes: dict[str, str] | None = None, printed: dict[str, list[tuple[str, str]]] | None = None,
                 both_ways: bool = True) -> list[tuple[str, str]]:
    """
    (number, total) pairs Tesseract finds, e.g. [('136', '197')]. Stops as soon
    as a number in `wanted` (num_key form, e.g. 'TG5', '136') shows up, so the
    common case costs one or two Tesseract calls instead of 24.
    bare: also a number printed without a total (basic energies, some promos) —
    only one in `wanted` counts, as (number, ""), since the strip has other digits too.
    prefixes: filled with the letters read in front of a bare number ({'16': 'SV'}),
    the printed set code — tells same-numbered energies of two sets apart.
    printed: per wanted number how candidates print it, (set code, digits)
    ({'16': [('SVE', '016'), ('MEE', '016')]}). With it, a bare number only counts
    with a fitting set code in front ("SV 16": confirmed at once) or digit for digit
    as printed ("016": needs a second reading) — a lone "8" out of the holo pattern doesn't.
    both_ways: also try the card upside down (not needed when its orientation is known).
    """
    if not available():
        return []
    import pytesseract

    from .catalog import num_key
    found: list[tuple[str, str]] = []
    bare_seen: dict[str, int] = {}
    for roi in _rois(card, upside_down, _BARE_BAND if bare else _BAND, both_ways):
        if roi.size == 0:
            continue
        for img in _prep(roi, bare):
            for cfg in _BARE_CONFIGS if bare else _CONFIGS:
                try:
                    txt = pytesseract.image_to_string(img, config=cfg)
                except Exception:
                    continue
                text = txt.replace(" ", "").upper()
                for m in _NUM_RE.finditer(text):
                    pair = (m.group(1), m.group(2))
                    hit = next((k for k in _keys(pair[0]) if wanted and k in wanted), None)
                    if hit is not None:
                        pair = (hit, pair[1])
                    if pair not in found:
                        found.append(pair)
                    if hit is not None:
                        return [pair] + [p for p in found if p != pair]
                if bare and wanted:
                    codes: list[str] = []          # this reading's codes, each once
                    for line in txt.upper().splitlines():
                        for variant in (line.strip(), line.replace(" ", "")):
                            for m in _BARE_RE.finditer(variant):
                                letters, digits = m.group(1), m.group(2)
                                code = next((k for k in _keys(letters + digits) if k in wanted), None)
                                if code is None:
                                    continue
                                own = bool(letters) and code == num_key(digits)
                                forms = (printed or {}).get(code)
                                coded = own and forms is not None and any(_fits(letters, sc) for sc, _ in forms)
                                if forms is not None and not coded and digits not in {d for _, d in forms}:
                                    continue
                                if prefixes is not None and own:
                                    prefixes.setdefault(code, letters)
                                if code not in codes:
                                    codes.append(code)
                                if coded:
                                    bare_seen[code] = bare_seen.get(code, 0) + 1   # number and set code: two signs
                    for code in codes:
                        if (code, "") not in found:
                            found.append((code, ""))
                        bare_seen[code] = bare_seen.get(code, 0) + 1
                        if bare_seen[code] >= 2:           # read the same way twice (or with its set code): enough
                            return [(code, "")] + [p for p in found if p != (code, "")]
    return found


def read_set_code(card: np.ndarray, codes: list[str], upside_down: bool = False) -> str | None:
    """
    Which of a few printed set codes ("SVE", "MEE") is on the card's bottom strip —
    for the same number in two sets (basic energies). The code sits in a small dark
    box with light letters: read inverted. Only a code read twice, with no other one
    seen at all, counts ("MEE" is easily made out of noise; a wrong print is worse
    than none).
    """
    if not available() or len(codes) < 2:
        return None
    import pytesseract
    letters = "".join(sorted(set("".join(codes)) | set("NIT")))      # + the "EN" after it, some noise
    count = dict.fromkeys(codes, 0)
    for roi in _rois(card, upside_down, _BARE_BAND, both_ways=False):
        if roi.size == 0:
            continue
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        big = cv2.resize(gray, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_CUBIC)
        inv = cv2.bitwise_not(cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(big))
        for psm in (11, 6):
            try:
                txt = pytesseract.image_to_string(
                    inv, config=f"--oem 3 --psm {psm} -c tessedit_char_whitelist={letters}")
            except Exception:  # noqa: BLE001, S112 — Tesseract can crash on an odd crop; the next one may do
                continue
            text = txt.upper().replace(" ", "").replace("\n", "")
            for c in codes:
                count[c] += c in text
    seen = [c for c in codes if count[c]]
    return seen[0] if len(seen) == 1 and count[seen[0]] >= 2 else None


def warm_up() -> None:
    """One tiny Tesseract call, so the first real read doesn't pay for loading
    the binary and language data from a cold disk."""
    if not available():
        return
    import pytesseract
    img = np.full((60, 200), 255, np.uint8)
    cv2.putText(img, "123/197", (8, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
    pytesseract.image_to_string(img, config=_CONFIGS[0])
