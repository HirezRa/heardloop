"""Shared fixtures. Keep TestClient start-up free of Docker-only DNS lookups."""

import base64
import os
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _skip_own_service_dns(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """API lifespan resolves compose host names (db, api, …); on the host that times out every test.

    Importing `heardloop.api.main` builds the app and reads settings, so a throwaway token key is set
    first when the environment has none. The real `delivery.refresh_blocked_service_ips` stays available
    for unit tests that call it directly.
    """
    if not os.environ.get("HEARDLOOP_TOKEN_KEY"):
        monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", base64.b64encode(os.urandom(32)).decode())
    if not os.environ.get("DATABASE_URL"):
        monkeypatch.setenv(
            "DATABASE_URL",
            os.environ.get("TEST_DATABASE_URL") or "postgresql+asyncpg://ci@127.0.0.1:1/none",
        )
    from heardloop.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "heardloop.api.main.refresh_blocked_service_ips",
        AsyncMock(return_value=None),
    )
    yield
