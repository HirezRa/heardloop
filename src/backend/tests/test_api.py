import asyncio
import base64
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

DB_URL = os.environ.get("TEST_DATABASE_URL")
# One key for this module: lifespan keycheck writes AppMeta on a shared testdb.
_TEST_KEY = base64.b64encode(b"heardloop-test-api-key-32bytes!!").decode()


def _clear_key_fingerprint() -> None:
    """Other suites (e.g. keycheck integration) may have stored a different fingerprint."""
    if not DB_URL:
        return
    from heardloop.keycheck import META_KEY
    from heardloop.models import AppMeta

    async def _run() -> None:
        engine = create_async_engine(DB_URL)
        async with engine.begin() as conn:
            # Fresh test databases may not have the table yet; nothing to clear then.
            await conn.run_sync(AppMeta.__table__.create, checkfirst=True)
            await conn.execute(delete(AppMeta).where(AppMeta.key == META_KEY))
        await engine.dispose()

    asyncio.run(_run())


def _app(monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    monkeypatch.setenv("DATABASE_URL", DB_URL or "postgresql+asyncpg://user@127.0.0.1:1/none")
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", _TEST_KEY)
    monkeypatch.setenv("APP_ENV", "production")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from heardloop.config import get_settings

    get_settings.cache_clear()
    _clear_key_fingerprint()
    from heardloop.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return _app(monkeypatch)


def test_meta_hides_environment_in_production(client: TestClient) -> None:
    with client:
        res = client.get("/api/v1/meta")
    assert res.status_code == 200
    assert res.json()["name"] == "Heardloop"
    assert res.json()["environment"] is None


def test_security_headers_and_no_docs_in_production(client: TestClient) -> None:
    with client:
        res = client.get("/api/docs")
    assert res.status_code == 404
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"


@pytest.mark.skipif(not DB_URL, reason="TEST_DATABASE_URL not set")
def test_health_with_database(client: TestClient) -> None:
    with client:
        res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json() == {"status": "healthy", "database": "up"}


def test_startup_fails_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keycheck must not start the API with an unverified key (retries then raises)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user@127.0.0.1:1/none")
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", _TEST_KEY)
    from unittest.mock import AsyncMock

    from heardloop.config import get_settings
    from heardloop.keycheck import TokenKeyUnverifiedError

    get_settings.cache_clear()
    monkeypatch.setattr("heardloop.keycheck.asyncio.sleep", AsyncMock())
    from heardloop.api.main import create_app

    with pytest.raises(TokenKeyUnverifiedError), TestClient(create_app()):
        pass


def test_health_unhealthy_when_database_drops_after_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a successful keycheck, health still reports 503 if the database is gone."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user@127.0.0.1:1/none")
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", _TEST_KEY)
    from unittest.mock import AsyncMock

    from heardloop.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr("heardloop.api.main.ensure_token_key", AsyncMock())
    from heardloop.api.main import create_app

    with TestClient(create_app()) as c:
        res = c.get("/api/v1/health")
    assert res.status_code == 503
    assert res.json()["status"] == "unhealthy"


@pytest.mark.parametrize(("value", "expected"), [(None, "en"), ("", "en"), ("HE", "he"), ("en", "en")])
def test_meta_reports_ui_language(monkeypatch: pytest.MonkeyPatch, value: str | None, expected: str) -> None:
    if value is None:
        monkeypatch.delenv("UI_LANGUAGE", raising=False)
        client = _app(monkeypatch)
    else:
        client = _app(monkeypatch, UI_LANGUAGE=value)
    with client:
        assert client.get("/api/v1/meta").json()["ui_language"] == expected


def test_invalid_ui_language_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from heardloop.config import Settings

    with pytest.raises(ValidationError):
        Settings(DATABASE_URL="x", HEARDLOOP_TOKEN_KEY="y", UI_LANGUAGE="fr")  # type: ignore[call-arg]  # noqa: S106


def test_smtp_none_with_login_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from heardloop.config import Settings

    monkeypatch.setenv("SMTP_SECURITY", "none")
    monkeypatch.setenv("SMTP_USERNAME", "mailer")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
