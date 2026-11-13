"""Logdateien (app/logsetup.py), Anfrage-Protokoll (main.py) und Browser-Fehler (client-log)."""
import importlib
import logging

import pytest
from starlette.requests import Request

from app import logsetup
from app import main as app_main
from app.api.v1 import client_log

API = "/api/v1"


@pytest.fixture()
def fresh_logsetup(tmp_path, monkeypatch):
    """logsetup frisch geladen, Handler danach wieder ab (andere Tests schreiben nicht mit)."""
    before = {name: list(logging.getLogger(name).handlers) for name in ("", "uvicorn", "uvicorn.access")}
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    mod = importlib.reload(logsetup)
    yield mod, tmp_path / "logs"
    for name, handlers in before.items():
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if h not in handlers:
                lg.removeHandler(h)
                h.close()
    importlib.reload(logsetup)


def test_log_files_rotate_and_errors_get_their_own_file(fresh_logsetup):
    mod, d = fresh_logsetup
    assert mod.setup("testsvc") == d and mod.setup("testsvc") == d          # zweimal harmlos
    logging.getLogger("app.x").info("nur info")
    logging.getLogger("app.x").warning("eine warnung")
    try:
        raise ValueError("kaputt")
    except ValueError:
        logging.getLogger("app.x").exception("mit traceback")
    logging.getLogger("uvicorn.access").info("GET /x 200")
    for h in logging.getLogger().handlers:
        h.flush()
    main, errors = (d / "testsvc.log").read_text(), (d / "testsvc-errors.log").read_text()
    assert "nur info" in main and "eine warnung" in main
    assert "nur info" not in errors and "eine warnung" in errors and "ValueError: kaputt" in errors
    assert "GET /x 200" in (d / "testsvc-access.log").read_text()
    handler = next(h for h in logging.getLogger().handlers if getattr(h, "baseFilename", "").endswith("testsvc-errors.log"))
    assert handler.maxBytes == 10 * 1024 * 1024 and handler.backupCount == 5


def test_without_a_mounted_folder_only_the_console(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_DIR", raising=False)
    mod = importlib.reload(logsetup)
    monkeypatch.setattr(mod, "Path", lambda p: tmp_path / "missing")
    assert mod.setup("testsvc") is None
    importlib.reload(logsetup)


def test_uvicorn_does_not_write_the_same_error_twice():
    f = logsetup._NotTwice()
    exc = RuntimeError("x")
    exc._pokecollect_logged = True
    rec = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "Exception in ASGI application", None,
                            (RuntimeError, exc, None))
    assert not f.filter(rec)
    rec.name = "app.request"
    assert f.filter(rec)


def test_request_log_has_path_user_and_id_and_hides_secrets(client, caplog):
    from fastapi import APIRouter

    r = client.get(f"{API}/cards", params={"limit": 1})
    assert len(r.headers["X-Request-ID"]) >= 8
    assert client.get(f"{API}/cards", params={"limit": 1}, headers={"X-Request-ID": "abc123"}).headers["X-Request-ID"] == "abc123"

    boom = APIRouter()

    @boom.get("/_test_boom")
    def _boom():
        raise RuntimeError("kaputt beim Test")

    app_main.app.include_router(boom)
    try:
        with caplog.at_level(logging.ERROR, logger="app.request"), pytest.raises(RuntimeError):
            client.get("/_test_boom", params={"token": "geheim", "q": "pikachu"}, headers={"X-Request-ID": "rid42"})
    finally:
        app_main.app.router.routes = [rt for rt in app_main.app.router.routes if getattr(rt, "path", "") != "/_test_boom"]
    rec = next(r for r in caplog.records if r.name == "app.request")
    text = rec.getMessage()
    assert "GET /_test_boom?token=…&q=pikachu" in text and "Benutzer admin" in text and "rid42" in text
    assert "geheim" not in text and rec.exc_info and "kaputt beim Test" in str(rec.exc_info[1])


def test_what_hides_secrets_in_the_query():
    scope = {"type": "http", "method": "POST", "path": "/api/v1/auth/oidc/callback", "query_string": b"code=abc&state=xyz&next=/decks",
             "headers": []}
    assert app_main._what(Request(scope)) == "POST /api/v1/auth/oidc/callback?code=…&state=…&next=/decks"


def test_browser_errors_go_to_the_log_with_a_limit(client, anon_client, caplog):
    client_log._recent.clear()
    body = {"kind": "error", "message": "TypeError: x is undefined", "stack": "at Deck (page.js:1:2)",
            "url": "/decks/4", "version": "1.15.0"}
    with caplog.at_level(logging.WARNING, logger="web.client"):
        assert client.post(f"{API}/client-log", json=body).status_code == 204
    rec = next(r for r in caplog.records if r.name == "web.client")
    assert "TypeError: x is undefined" in rec.getMessage() and "/decks/4" in rec.getMessage() and "admin" in rec.getMessage()
    assert "page.js:1:2" in rec.getMessage()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="web.client"):
        for _ in range(client_log.PER_MINUTE + 5):
            client.post(f"{API}/client-log", json=body)
    assert len([r for r in caplog.records if r.name == "web.client"]) == client_log.PER_MINUTE - 1
    assert anon_client.post(f"{API}/client-log", json=body).status_code == 401
    assert client.post(f"{API}/client-log", json={**body, "message": "x" * 3000}).status_code == 422
    client_log._recent.clear()
