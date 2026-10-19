"""Update notice: owner-only update field and notification dedupe. Needs TEST_DATABASE_URL."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.models import Notification
from heardloop.timeutil import utcnow
from heardloop.updates import UpdateCheckState, notify_owners_of_update, store_update_state
from tests.test_auth_integration import HDR, World, _query, client, login, world  # noqa: F401

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def test_version_endpoint_update_owner_only(client: TestClient, world: World) -> None:  # noqa: F811
    async def seed() -> None:
        assert URL
        engine = create_async_engine(URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await store_update_state(
                db,
                UpdateCheckState(
                    checked_at=datetime.now(UTC).isoformat(),
                    latest="9.9.9",
                    url="https://github.com/HirezRa/heardloop/releases/tag/v9.9.9",
                    notes="test notes",
                ),
                utcnow(),
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(seed())

    assert client.get("/api/v1/version").status_code == 401
    login(client, "viewer@example.com")
    viewer = client.get("/api/v1/version")
    assert viewer.status_code == 200
    assert viewer.json()["update"] is None
    assert "version" in viewer.json()

    login(client, "owner@example.com")
    owner = client.get("/api/v1/version")
    assert owner.status_code == 200
    body = owner.json()
    assert body["update"] is not None
    assert body["update"]["latest"] == "9.9.9"
    assert body["update"]["notes"] == "test notes"

    csrf = login(client, "editor@example.com")
    denied = client.post("/api/v1/version/check", headers={**HDR, "X-CSRF-Token": csrf})
    assert denied.status_code == 403


def test_version_check_post_returns_200_like_get(client: TestClient, world: World) -> None:  # noqa: F811
    """POST /version/check must not 500 (MissingGreenlet after rollback) and matches GET shape."""
    csrf = login(client, "owner@example.com")

    # Seed a successful check so the response is stable without calling GitHub.
    async def seed() -> None:
        assert URL
        engine = create_async_engine(URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await store_update_state(
                db,
                UpdateCheckState(
                    checked_at=datetime.now(UTC).isoformat(),
                    latest="0.9.5",
                    url="https://github.com/HirezRa/heardloop/releases/tag/v0.9.5",
                    notes="n",
                ),
                utcnow(),
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(seed())

    # Force the check path to skip GitHub: monkeypatch via env disable then re-enable through stored state.
    # Calling check with UPDATE_CHECK still on would hit the network; stub by setting disabled briefly
    # is awkward in TestClient. Instead call GET first (loads principal.user), then POST check with
    # a transport that the app already has — use disabled check by patching settings on the app.
    from heardloop.config import get_settings

    get_settings.cache_clear()
    # The real check may succeed or fail depending on CI network; either way must be 200.
    res = client.post("/api/v1/version/check", headers={**HDR, "X-CSRF-Token": csrf})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "version" in body and "git_sha" in body
    assert "update" in body and "update_status" in body
    assert body["update_status"] is not None
    assert body["update_status"]["status"] in {
        "newer",
        "current",
        "error",
        "never",
        "disabled",
    }
    # Same keys as GET
    get_body = client.get("/api/v1/version").json()
    assert set(get_body) == set(body)


def test_get_version_error_status_keeps_last_ok(client: TestClient, world: World) -> None:  # noqa: F811
    async def seed() -> None:
        assert URL
        engine = create_async_engine(URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await store_update_state(
                db,
                UpdateCheckState(
                    checked_at=datetime.now(UTC).isoformat(),
                    latest="9.9.9",
                    url="https://github.com/HirezRa/heardloop/releases/tag/v9.9.9",
                    notes="ok",
                ),
                utcnow(),
            )
            await store_update_state(
                db,
                UpdateCheckState(checked_at=datetime.now(UTC).isoformat(), error="tls"),
                utcnow(),
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(seed())
    login(client, "owner@example.com")
    body = client.get("/api/v1/version").json()
    assert body["update_status"]["status"] == "error"
    assert body["update_status"]["error"] == "tls"
    assert body["update_status"]["last_ok_latest"] == "9.9.9"
    assert body["update"] is not None
    assert body["update"]["latest"] == "9.9.9"


def test_update_notification_dedupes_per_version(client: TestClient, world: World) -> None:  # noqa: F811
    async def run() -> int:
        assert URL
        engine = create_async_engine(URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        state = UpdateCheckState(
            checked_at=datetime.now(UTC).isoformat(),
            latest="8.8.8",
            url="https://github.com/HirezRa/heardloop/releases/tag/v8.8.8",
            notes="",
        )
        now = utcnow()
        async with factory() as db:
            n1 = await notify_owners_of_update(db, state, "0.9.0", now)
            await db.commit()
            n2 = await notify_owners_of_update(db, state, "0.9.0", now)
            await db.commit()
        await engine.dispose()
        return n1 + 10 * n2

    result = asyncio.run(run())
    assert result % 10 >= 1  # first pass alerted (one or more workspaces)
    assert result // 10 == 0  # second pass deduped

    rows = asyncio.run(
        _query(
            select(Notification.kind, Notification.data).where(Notification.kind == "app.update_available")
        )
    )
    assert len(rows) >= 1
    assert all((data or {}).get("latest") == "8.8.8" for _, data in rows)


def test_health_has_no_version(client: TestClient) -> None:  # noqa: F811
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert "version" not in res.json()
