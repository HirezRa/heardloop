"""Post version history list and restore (FR-13). Needs TEST_DATABASE_URL."""

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.models import AuditLogEntry, MediaAsset
from tests.test_auth_integration import World, client, login, world  # noqa: F401
from tests.test_posts_integration import _add_account, _base, _create, _h, _set_require_approval

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def mc(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:  # noqa: F811
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    from heardloop.config import get_settings

    get_settings.cache_clear()
    from heardloop.api.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as c:
        yield c


def test_list_versions_newest_first_and_approved(client: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, True))
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc, "Version one")
    url = f"{_base(world)}/{post['id']}"
    assert client.patch(url, json={"body": "Version two"}, headers=_h(client)).status_code == 200
    assert client.post(f"{url}/submit", headers=_h(client)).status_code == 200

    client.cookies.clear()
    login(client, "approver@example.com")
    assert client.post(f"{url}/approve", headers=_h(client)).status_code == 200

    versions = client.get(f"{url}/versions", headers=_h(client)).json()
    assert len(versions) == 2
    assert [v["body"] for v in versions] == ["Version two", "Version one"]
    assert versions[0]["is_current"] is True and versions[1]["is_current"] is False
    assert versions[0]["approved"] is True and versions[1]["approved"] is False
    assert versions[0]["author_name"]


def test_restore_draft_creates_new_version(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc, "Original text")
    url = f"{_base(world)}/{post['id']}"
    versions = client.get(f"{url}/versions", headers=_h(client)).json()
    first_id = versions[-1]["id"]
    assert client.patch(url, json={"body": "Edited text"}, headers=_h(client)).status_code == 200

    restored = client.post(f"{url}/versions/{first_id}/restore", headers=_h(client)).json()
    assert restored["body"] == "Original text"
    assert restored["version_count"] == 3
    assert restored["status"] == "draft"
    assert restored["warnings"] == []

    audit = asyncio.run(_audit_actions(world.workspace_id, "post.version_restored"))
    assert any(a.get("from_version_id") == first_id for a in audit)


def test_restore_scheduled_refused(client: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc, "Sched me")
    url = f"{_base(world)}/{post['id']}"
    when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert client.post(f"{url}/schedule", json={"scheduled_at": when}, headers=_h(client)).status_code == 200
    versions = client.get(f"{url}/versions", headers=_h(client)).json()
    res = client.post(f"{url}/versions/{versions[0]['id']}/restore", headers=_h(client))
    assert res.status_code == 409 and res.json()["detail"] == "post_not_editable"


def test_restore_drops_missing_media_with_warning(mc: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(mc, "editor@example.com")
    media = mc.post(
        f"/api/v1/workspaces/{world.workspace_id}/media",
        content=PNG,
        headers={**_h(mc), "Content-Type": "image/png"},
    ).json()
    post = mc.post(
        _base(world),
        json={"account_id": str(acc), "body": "With image", "media_ids": [media["id"]]},
        headers=_h(mc),
    ).json()
    url = f"{_base(world)}/{post['id']}"
    versions = mc.get(f"{url}/versions", headers=_h(mc)).json()
    first_id = versions[0]["id"]
    assert mc.patch(url, json={"body": "No image", "media_ids": []}, headers=_h(mc)).status_code == 200
    asyncio.run(_delete_media(uuid.UUID(media["id"])))

    restored = mc.post(f"{url}/versions/{first_id}/restore", headers=_h(mc)).json()
    assert restored["body"] == "With image"
    assert restored["media"] == []
    assert restored["warnings"] == ["media_missing"]


def test_restore_cross_post_version_404(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    a = _create(client, world, acc, "Post A")
    b = _create(client, world, acc, "Post B")
    a_versions = client.get(f"{_base(world)}/{a['id']}/versions", headers=_h(client)).json()
    res = client.post(f"{_base(world)}/{b['id']}/versions/{a_versions[0]['id']}/restore", headers=_h(client))
    assert res.status_code == 404 and res.json()["detail"] == "version_not_found"


async def _delete_media(media_id: uuid.UUID) -> None:
    assert URL
    engine = create_async_engine(URL)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        await db.execute(delete(MediaAsset).where(MediaAsset.id == media_id))
        await db.commit()
    await engine.dispose()


async def _audit_actions(workspace_id: uuid.UUID, action: str) -> list[dict]:  # type: ignore[type-arg]
    assert URL
    engine = create_async_engine(URL)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        rows = (
            await db.execute(
                select(AuditLogEntry.diff_json)
                .where(AuditLogEntry.workspace_id == workspace_id, AuditLogEntry.action == action)
                .order_by(AuditLogEntry.id.desc())
            )
        ).scalars()
        out = [dict(r) if r else {} for r in rows]
    await engine.dispose()
    return out
