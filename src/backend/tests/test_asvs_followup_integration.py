"""ASVS L1-2 follow-up (#14) against a real PostgreSQL. Needs TEST_DATABASE_URL (throwaway database only)."""

# ruff: noqa: F811  # pytest fixtures are imported and then used as test arguments

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.domain.workflow import PostStatus
from heardloop.models import AuditLogEntry, Post, PostVersion, User
from heardloop.timeutil import utcnow
from tests.test_auth_integration import (  # noqa: F401
    HDR,
    PASSWORD,
    World,
    _query,
    client,
    login,
    next_totp,
    world,
)
from tests.test_posts_integration import _add_account, _h, _set_require_approval

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _user_id(email: str) -> uuid.UUID:
    return asyncio.run(_query(select(User.id).where(User.email == email)))[0][0]  # type: ignore[no-any-return]


def _audit(c: TestClient, world: World) -> list[dict]:  # type: ignore[type-arg]
    res = c.get(f"/api/v1/workspaces/{world.workspace_id}/audit", params={"limit": 200})
    assert res.status_code == 200, res.text
    return res.json()["items"]  # type: ignore[no-any-return]


# ---- sign-in events of people who also belong to another workspace ----


def test_owner_cannot_read_or_purge_sign_ins_of_a_member_from_another_workspace(
    client: TestClient, world: World
) -> None:
    stranger_id = _user_id("stranger@example.com")
    login(client, "stranger@example.com")  # sign-in event without a workspace, owner of the other workspace
    client.cookies.clear()

    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    members = f"/api/v1/workspaces/{world.workspace_id}/members"
    added = client.post(
        members,
        json={"email": "stranger@example.com", "display_name": "Stranger", "role": "viewer"},
        headers=h,
    )
    assert added.status_code == 201, added.text

    items = _audit(client, world)
    assert not [e for e in items if e["action"].startswith("auth.") and e["actor_id"] == str(stranger_id)]
    # Members whose only workspace this is still show up (the owner's own sign-in).
    owner_id = _user_id("owner@example.com")
    assert [e for e in items if e["action"].startswith("auth.") and e["actor_id"] == str(owner_id)]

    purge = client.post(
        f"/api/v1/workspaces/{world.workspace_id}/audit/purge", json={"password": PASSWORD}, headers=h
    )
    assert purge.status_code == 200, purge.text
    kept = asyncio.run(
        _query(
            select(AuditLogEntry.id).where(
                AuditLogEntry.actor_id == stranger_id, AuditLogEntry.workspace_id.is_(None)
            )
        )
    )
    assert kept  # the other workspace's owner keeps their sign-in history


# ---- anonymisation clears IP addresses ----


def test_anonymise_clears_ip_addresses_in_the_activity_log(client: TestClient, world: World) -> None:
    editor_id = _user_id("editor@example.com")
    login(client, "editor@example.com")
    client.cookies.clear()
    before = asyncio.run(
        _query(
            select(AuditLogEntry.id).where(
                AuditLogEntry.actor_id == editor_id, AuditLogEntry.ip_address.is_not(None)
            )
        )
    )
    assert before  # the sign-in was logged with the client address

    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    res = client.post(
        f"/api/v1/workspaces/{world.workspace_id}/members/{editor_id}/anonymise",
        json={"code": next_totp("owner@example.com")},
        headers=h,
    )
    assert res.status_code == 204, res.text
    rows = asyncio.run(_query(select(AuditLogEntry.ip_address).where(AuditLogEntry.actor_id == editor_id)))
    assert rows and all(ip is None for (ip,) in rows)  # rows kept for history, addresses gone


# ---- manual retry of a failed post respects required approval ----


async def _failed_post(world: World, account_id: uuid.UUID) -> uuid.UUID:
    assert URL
    engine = create_async_engine(URL)
    now = utcnow()
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        author = (await s.execute(select(User).where(User.email == "editor@example.com"))).scalar_one()
        post = Post(workspace_id=world.workspace_id, account_id=account_id, created_at=now, updated_at=now)
        post.transition_to(PostStatus.SCHEDULED, require_approval=False, now=now)
        post.transition_to(PostStatus.PUBLISHING, require_approval=False, now=now)
        post.mark_failed("rate_limited", now)
        post.scheduled_at = now - timedelta(hours=1)
        s.add(post)
        await s.flush()
        version = PostVersion(post_id=post.id, body="Never reviewed", author_id=author.id, created_at=now)
        s.add(version)
        await s.flush()
        post.current_version_id = version.id
        await s.commit()
        post_id = post.id
    await engine.dispose()
    return post_id


def test_retrying_a_failed_post_needs_approval_when_it_is_required(client: TestClient, world: World) -> None:
    acc = asyncio.run(_add_account(world))
    asyncio.run(_set_require_approval(world, False))
    post_id = asyncio.run(_failed_post(world, acc))
    asyncio.run(_set_require_approval(world, True))  # turned on after the post was scheduled
    try:
        login(client, "editor@example.com")
        url = f"/api/v1/workspaces/{world.workspace_id}/posts/{post_id}"
        when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        res = client.post(f"{url}/schedule", json={"scheduled_at": when}, headers=_h(client))
        assert res.status_code == 409 and res.json()["detail"] == "approval_required"
        now_res = client.post(f"{url}/publish-now", headers=_h(client))
        assert now_res.status_code == 409 and now_res.json()["detail"] == "approval_required"
        assert client.get(url).json()["status"] == "failed"
    finally:
        asyncio.run(_set_require_approval(world, False))

    # Without required approval the manual retry still works as before.
    res = client.post(f"{url}/schedule", json={"scheduled_at": when}, headers=_h(client))
    assert res.status_code == 200 and res.json()["status"] == "scheduled"


# ---- /version owner check with memberships in several workspaces ----


def test_version_update_field_for_an_owner_who_is_viewer_elsewhere(client: TestClient, world: World) -> None:
    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    added = client.post(
        f"/api/v1/workspaces/{world.workspace_id}/members",
        json={"email": "stranger@example.com", "display_name": "Stranger", "role": "viewer"},
        headers=h,
    )
    assert added.status_code == 201
    client.cookies.clear()
    login(client, "stranger@example.com")  # owner of the other workspace, viewer here
    body = client.get("/api/v1/version").json()
    assert body["update_status"] is not None  # owner payload, whatever membership row comes first
