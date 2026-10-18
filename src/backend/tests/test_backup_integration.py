"""Backup status endpoint permissions and the once-a-day staleness alert. Needs TEST_DATABASE_URL."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop import backup
from heardloop.config import get_settings
from heardloop.models import Notification, User, WebhookDelivery, Workspace
from tests.test_auth_integration import World, _query, client, login, world  # noqa: F401

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _factory():  # type: ignore[no-untyped-def]
    assert URL
    engine = create_async_engine(URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _manifest(finished: datetime, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "set": "heardloop-20261018T010000Z",
        "started_at": finished.isoformat(),
        "finished_at": finished.isoformat(),
        "status": "ok",
        "error": None,
        "files": [{"name": "heardloop-20261018T010000Z.dump", "bytes": 10, "sha256": "ab" * 32}],
        "db_bytes": 2048,
        "media_bytes": 4096,
        "encrypted": True,
        "pg_dump_version": "17.6",
    }
    base.update(overrides)
    return base


def _use_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: object | None) -> Path:
    path = tmp_path / "latest.json"
    if content is not None:
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    monkeypatch.setenv("BACKUP_MANIFEST_PATH", str(path))
    get_settings.cache_clear()
    return path


# ---- endpoint ----


def test_backup_status_is_owner_only(client: TestClient, world: World) -> None:  # noqa: F811
    url = f"/api/v1/workspaces/{world.workspace_id}/settings/backup"
    assert client.get(url).status_code == 401
    for email in ("viewer@example.com", "editor@example.com", "approver@example.com"):
        client.cookies.clear()
        login(client, email)
        assert client.get(url).status_code == 403, email
    client.cookies.clear()
    login(client, "stranger@example.com")
    assert client.get(url).status_code == 404
    client.cookies.clear()
    login(client, "owner@example.com")
    assert client.get(url).status_code == 200


def test_backup_status_not_configured_without_a_file(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _use_manifest(monkeypatch, tmp_path, None)
    login(client, "owner@example.com")
    body = client.get(f"/api/v1/workspaces/{world.workspace_id}/settings/backup").json()
    assert body["configured"] is False and body["status"] is None and body["finished_at"] is None

    monkeypatch.setenv("BACKUP_MANIFEST_PATH", "")
    get_settings.cache_clear()
    assert (
        client.get(f"/api/v1/workspaces/{world.workspace_id}/settings/backup").json()["configured"] is False
    )


def test_backup_status_ok_and_stale_and_failed(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    url = f"/api/v1/workspaces/{world.workspace_id}/settings/backup"
    login(client, "owner@example.com")
    now = datetime.now(UTC)

    _use_manifest(monkeypatch, tmp_path, _manifest(now - timedelta(hours=2)))
    body = client.get(url).json()
    assert body["configured"] is True and body["status"] == "ok" and body["stale"] is False
    assert (body["db_bytes"], body["media_bytes"], body["encrypted"]) == (2048, 4096, True)
    assert 1.9 <= body["age_hours"] <= 2.2 and body["error"] is None
    # Only status, times and sizes: no file names, hashes or paths.
    assert set(body) == {
        "configured",
        "status",
        "stale",
        "finished_at",
        "age_hours",
        "db_bytes",
        "media_bytes",
        "encrypted",
        "error",
    }

    _use_manifest(monkeypatch, tmp_path, _manifest(now - timedelta(hours=40)))
    body = client.get(url).json()
    assert body["status"] == "ok" and body["stale"] is True

    _use_manifest(monkeypatch, tmp_path, _manifest(now, status="failed", error="pg_dump failed"))
    body = client.get(url).json()
    assert body["status"] == "failed" and body["error"] == "pg_dump failed" and body["stale"] is False


@pytest.mark.parametrize(
    "content",
    ["not json", "x" * (backup.MAX_MANIFEST_BYTES + 1), "[]"],
    ids=["garbage", "oversized", "wrong-shape"],
)
def test_backup_status_unreadable_manifest(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str,
) -> None:
    _use_manifest(monkeypatch, tmp_path, content)
    login(client, "owner@example.com")
    body = client.get(f"/api/v1/workspaces/{world.workspace_id}/settings/backup").json()
    assert body["configured"] is True and body["status"] == "unreadable"


# ---- worker: staleness alert once per day ----


async def _notifications() -> list[tuple[str, str, dict]]:  # type: ignore[type-arg]
    rows = await _query(
        select(User.email, Notification.kind, Notification.data).join(User, User.id == Notification.user_id)
    )
    return [(r[0], r[1], r[2]) for r in rows]


def test_stale_backup_notifies_owners_once_a_day(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    t0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    _use_manifest(monkeypatch, tmp_path, _manifest(t0 - timedelta(hours=40)))
    clock = {"now": t0}

    async def check() -> int:
        engine, factory = _factory()
        try:
            return await backup.check_backup_alerts(factory, lambda: clock["now"], t0 - timedelta(days=5))
        finally:
            await engine.dispose()

    # Two workspaces, one owner each. Editors, approvers and viewers are not told.
    assert asyncio.run(check()) == 2
    rows = asyncio.run(_notifications())
    assert sorted(r[0] for r in rows) == ["owner@example.com", "stranger@example.com"]
    assert {r[1] for r in rows} == {"backup.stale"}
    assert rows[0][2]["reason"] == "old" and rows[0][2]["age_hours"] == 40.0
    assert "heardloop-2026" not in json.dumps([r[2] for r in rows])

    # An hour later, 23 hours later: nothing more.
    clock["now"] = t0 + timedelta(hours=1)
    assert asyncio.run(check()) == 0
    clock["now"] = t0 + timedelta(hours=23, minutes=59)
    assert asyncio.run(check()) == 0
    assert len(asyncio.run(_notifications())) == 2

    # After 24 hours the problem is still there: one more alert per workspace.
    clock["now"] = t0 + timedelta(hours=24, minutes=1)
    assert asyncio.run(check()) == 2
    assert len(asyncio.run(_notifications())) == 4


def test_failed_backup_alert_and_webhook_event(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    _use_manifest(monkeypatch, tmp_path, _manifest(now, status="failed", error="pg_dump failed"))

    async def go() -> tuple[int, list[tuple[str, dict]], list[tuple[str, dict]]]:  # type: ignore[type-arg]
        engine, factory = _factory()
        async with factory() as db:
            await db.execute(
                update(Workspace)
                .where(Workspace.id == world.workspace_id)
                .values(webhook_url="https://hooks.example.com/x")
            )
            await db.commit()
        alerted = await backup.check_backup_alerts(factory, lambda: now, now)
        async with engine.connect() as conn:
            notes = (await conn.execute(select(Notification.kind, Notification.data))).all()
            hooks = (await conn.execute(select(WebhookDelivery.event, WebhookDelivery.payload))).all()
        await engine.dispose()
        return alerted, [(n[0], n[1]) for n in notes], [(h[0], h[1]) for h in hooks]

    alerted, notes, hooks = asyncio.run(go())
    assert alerted == 2
    assert {n[0] for n in notes} == {"backup.failed"} and notes[0][1]["error"] == "pg_dump failed"
    # Only the workspace with a webhook gets an event, with status fields only.
    assert len(hooks) == 1 and hooks[0][0] == "backup.failed"
    assert hooks[0][1]["data"] == {
        "reason": "failed",
        "finished_at": now.isoformat(),
        "error": "pg_dump failed",
    }


def test_healthy_or_young_missing_manifest_sends_nothing(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)

    async def check(started: datetime) -> int:
        engine, factory = _factory()
        try:
            return await backup.check_backup_alerts(factory, lambda: now, started)
        finally:
            await engine.dispose()

    _use_manifest(monkeypatch, tmp_path, _manifest(now - timedelta(hours=3)))
    assert asyncio.run(check(now - timedelta(days=9))) == 0

    _use_manifest(monkeypatch, tmp_path / "gone", None)  # file does not exist
    assert asyncio.run(check(now - timedelta(hours=2))) == 0  # stack is young: first backup is at night
    assert asyncio.run(_notifications()) == []

    assert asyncio.run(check(now - timedelta(hours=37))) == 2  # missing for more than 36 hours
    assert {r[1] for r in asyncio.run(_notifications())} == {"backup.stale"}

    monkeypatch.setenv("BACKUP_MANIFEST_PATH", "")  # feature off: never alerts
    get_settings.cache_clear()
    before = len(asyncio.run(_notifications()))
    assert asyncio.run(check(now - timedelta(days=30))) == 0
    assert len(asyncio.run(_notifications())) == before
