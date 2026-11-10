from fastapi import Depends, HTTPException, status

from app.auth_user import AuthState, AuthUser, resolve_auth


# Seit Issue #1 auf allen Fach-Routern verdrahtet (api/v1/__init__.py).
# Auth-frei bleiben nur der Login (Passwort + OIDC-Einstieg/Callback), /health
# und der /images-StaticFiles-Mount (<img> kann keine Authorization-Header senden).
# Mehrbenutzer: das Token trägt die Benutzer-ID; get_db schneidet die Session
# auf diesen Benutzer zu (app/tenancy.py).
def require_auth(auth: AuthState = Depends(resolve_auth)) -> AuthUser:
    if auth.user is None:
        detail = auth.error or "Nicht eingeloggt"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    return auth.user


def require_admin(user: AuthUser = Depends(require_auth)) -> AuthUser:
    """Instanz-weite Einstellungen/Jobs (API-Keys, Katalog-Sync, Backup …)."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur für Administratoren")
    return user
