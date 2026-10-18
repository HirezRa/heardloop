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
