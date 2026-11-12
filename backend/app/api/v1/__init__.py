from fastapi import APIRouter, Depends

from app.api.deps import require_auth
from app.api.v1 import (auth, cards, catalog, collections, data, decks, friends, prices, scan, scan_drafts, scan_quality,
                        sealed, settings, sets, users)

router = APIRouter(prefix="/api/v1")

# Auth-frei: nur der Login selbst (Passwort, Login-Konfiguration, OIDC-Einstieg
# und -Callback; /auth/me trägt require_auth an der Route). /health und der /images-StaticFiles-Mount
# liegen außerhalb dieses Routers (main.py) und bleiben ebenfalls offen —
# <img>-Tags können keine Authorization-Header senden (Issue #1).
router.include_router(auth.router)

# Alle Fach-Router erzwingen ein gültiges JWT (require_auth, Issue #1).
for protected in (
    cards.router,
    catalog.router,
    collections.router,
    data.router,
    decks.router,
    friends.router,
    friends.shared_router,
    prices.router,
    scan.router,
    scan_drafts.router,
    scan_quality.router,   # nur Admins
    sealed.router,
    settings.router,
    sets.router,
    users.router,   # zusätzlich require_admin je Route
):
    router.include_router(protected, dependencies=[Depends(require_auth)])
