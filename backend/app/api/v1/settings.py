import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_auth
from app.auth_user import AuthUser
from app.database import get_db
from app.models.setting import AppSetting
from app.models.user import UserSetting
from app.services.regulation import DEFAULT_MIN_MARK, norm_mark
from app.schemas.setting import DEFAULTS, SECRET_KEYS, USER_KEYS, PasswordChange, SettingsResponse, SettingsUpdate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _get_all(db: Session, user_id: int | None = None) -> dict[str, str]:
    rows = {r.key: r.value for r in db.query(AppSetting).all()}
    merged = {k: rows.get(k, v) for k, v in DEFAULTS.items()}
    if user_id is not None:
        # Persönliche Werte überdecken die Instanz-Vorgabe (Mehrbenutzer).
        for r in db.query(UserSetting).filter(UserSetting.user_id == user_id).all():
            if r.key in USER_KEYS and r.value is not None:
                merged[r.key] = r.value
    return merged


def _set_user(db: Session, user_id: int, key: str, value: str):
    row = db.get(UserSetting, (user_id, key))
    if row:
        row.value = value
    else:
        db.add(UserSetting(user_id=user_id, key=key, value=value))


def _set(db: Session, key: str, value: str):
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def _int_or(value: str, default: int) -> int:
    """
    Zahl aus einer Einstellung lesen — unlesbare Werte fallen auf den Default
    zurück (#55). Die Tabelle hält alles als Text; lag dort je Müll (Restore
    eines Alt-Backups, manueller DB-Eingriff, ein früherer 500er-Pfad), starb
    sonst JEDER Settings-Aufruf inklusive der Einstellungs-Seite.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        log.warning("Einstellung unlesbar (%r) – nutze Default %s", value, default)
        return default


def _mask(value: str) -> str:
    """Maske für Secrets: "•••• " + letzte 4 Zeichen, leer wenn nicht gesetzt."""
    return f"•••• {value[-4:]}" if value else ""


def _to_response(raw: dict[str, str], is_admin: bool = True) -> SettingsResponse:
    # Secrets nie im Klartext ausliefern (Issue #1) — nur gesetzt-Flag + Maske;
    # Nicht-Admins sehen nicht einmal die Maske.
    secret_fields: dict[str, bool | str] = {}
    for key in SECRET_KEYS:
        value = raw[key] or ""
        secret_fields[f"{key}_set"] = bool(value)
        secret_fields[f"{key}_masked"] = _mask(value) if is_admin else ""
    return SettingsResponse(
        placeholder_images_enabled=raw["placeholder_images_enabled"] == "true",
        cards_per_page=_int_or(raw["cards_per_page"], 48),
        default_sort=raw["default_sort"] or "pokedex_nr",
        default_sort_reverse=raw["default_sort_reverse"] == "true",
        price_update_enabled=raw["price_update_enabled"] == "true",
        price_update_hour=_int_or(raw["price_update_hour"], 3),
        price_source=raw["price_source"] or "30d_avg",
        default_language=raw["default_language"] or "DE",
        default_condition=raw["default_condition"] or "",
        gemini_model=raw["gemini_model"] or "gemini-2.5-flash",
        gemini_daily_limit=_int_or(raw["gemini_daily_limit"], 0),
        scan_reader_provider=raw["scan_reader_provider"] or "gemini",
        openai_model=raw["openai_model"] or "gpt-4o-mini",
        openrouter_model=raw["openrouter_model"] or "google/gemini-2.5-flash",
        catalog_image_cache_level=raw["catalog_image_cache_level"] or "urls",
        standard_min_mark=norm_mark(raw["standard_min_mark"]) or DEFAULT_MIN_MARK,
        **secret_fields,
    )


@router.get("", response_model=SettingsResponse)
def get_settings(user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    return _to_response(_get_all(db, user.id), user.is_admin)


@router.put("", response_model=SettingsResponse)
def update_settings(data: SettingsUpdate, user: AuthUser = Depends(require_auth), db: Session = Depends(get_db)):
    updates = data.model_dump(exclude_unset=True)
    instance_keys = [k for k in updates if k not in USER_KEYS]
    if instance_keys and not user.is_admin:
        raise HTTPException(status_code=403, detail="Nur Admins dürfen Instanz-Einstellungen ändern")
    for key, value in updates.items():
        text_value = str(value).lower() if isinstance(value, bool) else str(value)
        if key in USER_KEYS:
            _set_user(db, user.id, key, text_value)
        else:
            _set(db, key, text_value)
    db.commit()
    return _to_response(_get_all(db, user.id), user.is_admin)


@router.post("/change-password", dependencies=[Depends(require_admin)])
def change_password(data: PasswordChange, db: Session = Depends(get_db)):
    stored_hash_row = db.get(AppSetting, "app_password_hash")
    current_hash = (
        stored_hash_row.value if stored_hash_row
        else os.getenv("APP_PASSWORD_HASH", "")
    )
    if not current_hash or not pwd_context.verify(data.current_password, current_hash):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort falsch")
    new_hash = pwd_context.hash(data.new_password)
    _set(db, "app_password_hash", new_hash)
    db.commit()
    return {"detail": "Passwort geändert"}
