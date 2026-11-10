"""
Einstellungs-Zugriff für Services (DRY): einzelne Werte mit Fallback auf die
DEFAULTS, ohne dass ein Service den Settings-Router importieren muss.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.setting import AppSetting
from app.models.user import UserSetting
from app.schemas.setting import DEFAULTS, USER_KEYS


def get_setting(db: Session, key: str) -> str:
    """
    Wert einer Einstellung; leere/fehlende Werte fallen auf DEFAULTS zurück.
    Persönliche Einstellungen (USER_KEYS) zuerst aus dem Wert des angemeldeten
    Benutzers, dann aus der Instanz-Vorgabe.
    """
    uid = db.info.get("user_id")
    if uid is not None and key in USER_KEYS:
        own = db.get(UserSetting, (uid, key))
        if own is not None:   # eigener Wert gilt — auch bewusst leer („kein Zustand")
            return own.value or DEFAULTS.get(key, "")
    row = db.get(AppSetting, key)
    if row and row.value not in (None, ""):
        return row.value
    return DEFAULTS.get(key, "")
