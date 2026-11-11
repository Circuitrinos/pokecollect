"""Sprachfassung eines TCGdex-Kartenbilds.

TCGdex legt dieselbe westliche Karte je Sprache unter einem eigenen Pfad ab:
https://assets.tcgdex.net/<sprache>/<serie>/<set>/<nummer>/high.webp — die
Kartennummern sind in allen westlichen Sprachen gleich, deshalb genügt es,
das Sprachstück zu tauschen. Japanische/asiatische Karten haben andere IDs;
für sie gibt es keinen Tausch.
"""
from __future__ import annotations

import re
from typing import Optional

# App-Sprachcode → TCGdex-Sprachcode (westliche Fassungen derselben Karte)
WEST: dict[str, str] = {"EN": "en", "DE": "de", "FR": "fr", "ES": "es", "IT": "it", "PT": "pt"}
_BACK = {v: k for k, v in WEST.items()}
_RX = re.compile(r"^(https://assets\.tcgdex\.net/)([a-z]{2}(?:-[a-z]{2})?)(/.+)$", re.I)


def image_lang(url: Optional[str]) -> Optional[str]:
    """App-Sprachcode des TCGdex-Bilds (z. B. "DE") oder None (kein westliches TCGdex-Bild)."""
    m = _RX.match(url or "")
    return _BACK.get(m.group(2).lower()) if m else None


def with_lang(url: Optional[str], lang: str) -> Optional[str]:
    """Dasselbe Bild in einer anderen westlichen Sprache; None, wenn das nicht geht."""
    m = _RX.match(url or "")
    code = WEST.get((lang or "").upper())
    if not m or not code or m.group(2).lower() not in _BACK:
        return None
    return f"{m.group(1)}{code}{m.group(3)}"
