"""Account reconnect fields, notification dedupe, retry-failed. Needs TEST_DATABASE_URL."""

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.domain.workflow import PostStatus
from heardloop.models import AuditLogEntry, LinkedInAccount, Notification, Post, PostVersion, User
from heardloop.timeutil import utcnow
from tests.test_auth_integration import HDR, World, _query, login, world  # noqa: F401
from tests.test_posts_integration import _set_require_approval

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

FAKE_TOKEN = "fake-linkedin-access-token"  # noqa: S105


def _linkedin(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/accessToken"):
        return httpx.Response(
            200,
            json={
                "access_token": FAKE_TOKEN,
                "expires_in": 5184000,
                "scope": "openid,profile,w_member_social",
            },
        )
    if req.url.path.endswith("/userinfo"):
        return httpx.Response(200, json={"sub": "member123", "name": "Example Member"})
    return httpx.Response(404)


@pytest.fixture
def li(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:  # noqa: F811
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "client-id-example")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "client-secret-example")
    monkeypatch.setenv("LINKEDIN_REDIRECT_URI", "https://testserver/api/v1/linkedin/callback")
    from heardloop.config import get_settings

    get_settings.cache_clear()
    from heardloop.api.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as c:
        asyncio.run(c.app.state.http.aclose())  # type: ignore[attr-defined]
        c.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(_linkedin))  # type: ignore[attr-defined]
        yield c


def _connect(c: TestClient, world: World) -> str:  # noqa: F811
    csrf = login(c, "owner@example.com")
    res = c.post(
        f"/api/v1/workspaces/{world.workspace_id}/linkedin/connect", headers={**HDR, "X-CSRF-Token": csrf}
    )
    assert res.status_code == 200, res.text
    return parse_qs(urlparse(res.json()["authorization_url"]).query)["state"][0]


def _account_id(c: TestClient, world: World) -> str:  # noqa: F811
    return c.get(f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts").json()[0]["id"]


async def _with_db(coro):  # type: ignore[no-untyped-def]
    eng = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with factory() as s:
            return await coro(s)
    finally:
        await eng.dispose()


def test_reconnect_clears_fields_and_retry_failed(li: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    state = _connect(li, world)
    li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    acc_id = _account_id(li, world)
    now = utcnow()

    async def _seed(s):  # type: ignore[no-untyped-def]
        author = (await s.execute(select(User).where(User.email == "editor@example.com"))).scalar_one()
        acc = await s.get(LinkedInAccount, uuid.UUID(acc_id))
        assert acc is not None
        acc.needs_reconnect_reason = "token_unreadable"
        acc.needs_reconnect_at = now - timedelta(hours=1)
        post = Post(workspace_id=world.workspace_id, account_id=acc.id, created_at=now, updated_at=now)
        post.transition_to(PostStatus.SCHEDULED, require_approval=False, now=now)
        post.transition_to(PostStatus.PUBLISHING, require_approval=False, now=now)
        post.mark_failed("token_unreadable: fix it and schedule the post again", now)
        post.scheduled_at = now - timedelta(hours=2)
        s.add(post)
        await s.flush()
        version = PostVersion(post_id=post.id, body="Retry me", author_id=author.id, created_at=now)
        s.add(version)
        await s.flush()
        post.current_version_id = version.id
        s.add(
            Notification(
                workspace_id=world.workspace_id,
                user_id=author.id,
                kind="account.reconnect_needed",
                data={"account_id": acc_id, "reason": "token_unreadable"},
                created_at=now - timedelta(hours=1),
                email_status="skipped",
            )
        )
        await s.commit()
        return post.id

    post_id = asyncio.run(_with_db(_seed))

    accounts = li.get(f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts").json()
    assert accounts[0]["needs_reconnect_reason"] == "token_unreadable"

    state = _connect(li, world)
    li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    accounts = li.get(f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts").json()
    assert accounts[0]["needs_reconnect_reason"] is None
    assert accounts[0]["needs_reconnect_at"] is None
    actions = {
        row[0]
        for row in asyncio.run(_query(select(AuditLogEntry.action).where(AuditLogEntry.entity_id == acc_id)))
    }
    assert "linkedin.account_reconnected" in actions

    csrf = login(li, "approver@example.com")
    res = li.post(
        f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts/{acc_id}/retry-failed",
        headers={**HDR, "X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"count": 1, "skipped_unapproved": 0}

    detail = li.get(f"/api/v1/workspaces/{world.workspace_id}/posts/{post_id}").json()
    assert detail["status"] == "scheduled"
    assert detail["last_error"] is None
    audits = asyncio.run(
        _query(
            select(AuditLogEntry.diff_json).where(
                AuditLogEntry.action == "post.rescheduled", AuditLogEntry.entity_id == str(post_id)
            )
        )
    )
    assert audits and audits[0][0].get("by") == "retry_failed"

    csrf = login(li, "viewer@example.com")
    res = li.post(
        f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts/{acc_id}/retry-failed",
        headers={**HDR, "X-CSRF-Token": csrf},
    )
    assert res.status_code == 403


def test_retry_failed_409_while_account_needs_reconnect(li: TestClient, world: World) -> None:  # noqa: F811
    state = _connect(li, world)
    li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    acc_id = _account_id(li, world)

    async def _flag(s):  # type: ignore[no-untyped-def]
        acc = await s.get(LinkedInAccount, uuid.UUID(acc_id))
        assert acc is not None
        acc.needs_reconnect_reason = "token_unreadable"
        acc.needs_reconnect_at = utcnow()
        await s.commit()

    asyncio.run(_with_db(_flag))
    csrf = login(li, "approver@example.com")
    res = li.post(
        f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts/{acc_id}/retry-failed",
        headers={**HDR, "X-CSRF-Token": csrf},
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "account_needs_reconnect"


def test_retry_failed_skips_unapproved_when_approval_required(li: TestClient, world: World) -> None:  # noqa: F811
    state = _connect(li, world)
    li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    acc_id = _account_id(li, world)
    now = utcnow()
    asyncio.run(_set_require_approval(world, True))

    async def _seed(s):  # type: ignore[no-untyped-def]
        author = (await s.execute(select(User).where(User.email == "editor@example.com"))).scalar_one()
        acc = await s.get(LinkedInAccount, uuid.UUID(acc_id))
        assert acc is not None
        # Leave the account healthy (as after a reconnect) so the endpoint can run.
        post = Post(workspace_id=world.workspace_id, account_id=acc.id, created_at=now, updated_at=now)
        post.transition_to(PostStatus.SCHEDULED, require_approval=False, now=now)
        post.transition_to(PostStatus.PUBLISHING, require_approval=False, now=now)
        post.mark_failed("token_expired: reconnect", now)
        post.scheduled_at = now - timedelta(hours=1)
        s.add(post)
        await s.flush()
        version = PostVersion(post_id=post.id, body="Needs review", author_id=author.id, created_at=now)
        s.add(version)
        await s.flush()
        post.current_version_id = version.id
        await s.commit()
        return post.id

    post_id = asyncio.run(_with_db(_seed))
    csrf = login(li, "approver@example.com")
    res = li.post(
        f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts/{acc_id}/retry-failed",
        headers={**HDR, "X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"count": 0, "skipped_unapproved": 1}
    detail = li.get(f"/api/v1/workspaces/{world.workspace_id}/posts/{post_id}").json()
    assert detail["status"] == "failed"
    asyncio.run(_set_require_approval(world, False))


def test_disconnect_clears_reconnect_fields(li: TestClient, world: World) -> None:  # noqa: F811
    state = _connect(li, world)
    li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    acc_id = _account_id(li, world)

    async def _flag(s):  # type: ignore[no-untyped-def]
        acc = await s.get(LinkedInAccount, uuid.UUID(acc_id))
        assert acc is not None
        acc.needs_reconnect_reason = "token_expired"
        acc.needs_reconnect_at = utcnow()
        await s.commit()

    asyncio.run(_with_db(_flag))
    csrf = login(li, "owner@example.com")
    assert (
        li.delete(
            f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts/{acc_id}",
            headers={**HDR, "X-CSRF-Token": csrf},
        ).status_code
        == 204
    )
    acc = li.get(f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts").json()[0]
    assert acc["needs_reconnect_reason"] is None
    assert acc["status"] == "revoked"
