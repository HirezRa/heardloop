"""Posts API: drafts, versions, approval, scheduling, publish-now. Needs TEST_DATABASE_URL."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.models import LinkedInAccount, LinkedInAccountType, Post, Workspace
from heardloop.security.crypto import TokenProtector
from tests.test_auth_integration import HDR, World, _query, client, login, world  # noqa: F401

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


async def _add_account(world: World, *, expires_in: timedelta = timedelta(days=60)) -> uuid.UUID:  # noqa: F811
    assert URL
    engine = create_async_engine(URL)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        acc_id = uuid.uuid4()
        now = datetime.now(UTC)
        db.add(
            LinkedInAccount(
                id=acc_id,
                workspace_id=world.workspace_id,
                type=LinkedInAccountType.PERSON,
                urn="urn:li:person:example",
                display_name="Example Member",
                access_token_encrypted=TokenProtector(world.token_key).protect(
                    "t", f"linkedin-account:{acc_id}"
                ),
                access_token_expires_at=now + expires_in,
                connected_at=now,
            )
        )
        await db.commit()
    await engine.dispose()
    return acc_id


async def _set_require_approval(world: World, value: bool) -> None:  # noqa: F811
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(
            update(Workspace).where(Workspace.id == world.workspace_id).values(require_approval=value)
        )
    await engine.dispose()


def _h(c: TestClient) -> dict[str, str]:
    return {**HDR, "X-CSRF-Token": c.get("/api/v1/auth/me").json()["csrf_token"]}


def _base(world: World) -> str:  # noqa: F811
    return f"/api/v1/workspaces/{world.workspace_id}/posts"


def _create(c: TestClient, world: World, acc: uuid.UUID, body: str = "Hello LinkedIn") -> dict:  # type: ignore[type-arg]  # noqa: F811
    res = c.post(_base(world), json={"account_id": str(acc), "body": body}, headers=_h(c))
    assert res.status_code == 201, res.text
    return res.json()  # type: ignore[no-any-return]


def test_approval_flow_then_schedule(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    assert (
        post["status"] == "draft" and post["version_count"] == 1 and post["account_name"] == "Example Member"
    )

    # With approval required, an editor cannot schedule a draft directly.
    when = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    url = f"{_base(world)}/{post['id']}"
    assert client.post(f"{url}/schedule", json={"scheduled_at": when}, headers=_h(client)).status_code == 409
    assert client.post(f"{url}/submit", headers=_h(client)).json()["status"] == "pending_approval"
    assert client.post(f"{url}/approve", headers=_h(client)).status_code == 403  # editors cannot approve

    client.cookies.clear()
    login(client, "approver@example.com")
    rejected = client.post(f"{url}/reject", json={"comment": "Shorter please"}, headers=_h(client)).json()
    assert rejected["status"] == "draft" and rejected["last_review_comment"] == "Shorter please"
    client.post(f"{url}/submit", headers=_h(client))
    assert client.post(f"{url}/approve", headers=_h(client)).json()["status"] == "approved"

    scheduled = client.post(f"{url}/schedule", json={"scheduled_at": when}, headers=_h(client)).json()
    assert scheduled["status"] == "scheduled" and scheduled["scheduled_at"] is not None

    # Editing a scheduled post sends it back to draft (needs approval again).
    edited = client.patch(url, json={"body": "Hello LinkedIn, v2"}, headers=_h(client)).json()
    assert edited["status"] == "draft" and edited["version_count"] == 2 and edited["scheduled_at"] is None


def test_without_approval_publish_now_and_unschedule(client: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    url = f"{_base(world)}/{post['id']}"
    now_post = client.post(f"{url}/publish-now", headers=_h(client)).json()
    assert now_post["status"] == "scheduled"
    assert client.post(f"{url}/unschedule", headers=_h(client)).json()["status"] == "draft"
    assert client.post(f"{url}/submit", headers=_h(client)).status_code == 409  # approval not required


@pytest.mark.parametrize(
    ("offset", "code"),
    [(timedelta(seconds=5), 422), (timedelta(days=400), 422)],
)
def test_schedule_time_limits(client: TestClient, world: World, offset: timedelta, code: int) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    when = (datetime.now(UTC) + offset).isoformat()
    res = client.post(
        f"{_base(world)}/{post['id']}/schedule", json={"scheduled_at": when}, headers=_h(client)
    )
    assert res.status_code == code


def test_naive_datetime_rejected(client: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    res = client.post(
        f"{_base(world)}/{post['id']}/schedule",
        json={"scheduled_at": "2030-01-01T10:00:00"},
        headers=_h(client),
    )
    assert res.status_code == 422


def test_token_expiring_before_schedule_is_refused(client: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world, expires_in=timedelta(hours=1)))
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    when = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    res = client.post(
        f"{_base(world)}/{post['id']}/schedule", json={"scheduled_at": when}, headers=_h(client)
    )
    assert res.status_code == 409 and res.json()["detail"] == "token_expires_before_schedule"


@pytest.mark.parametrize("body", ["   ", "x" * 3001])
def test_body_validation(client: TestClient, world: World, body: str) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    res = client.post(_base(world), json={"account_id": str(acc), "body": body}, headers=_h(client))
    assert res.status_code == 422


def test_permissions_and_isolation(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "viewer@example.com")
    assert client.get(_base(world)).status_code == 200
    res = client.post(_base(world), json={"account_id": str(acc), "body": "x"}, headers=_h(client))
    assert res.status_code == 403

    client.cookies.clear()
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    client.cookies.clear()
    login(client, "stranger@example.com")
    assert client.get(f"{_base(world)}/{post['id']}").status_code == 404
    other = f"/api/v1/workspaces/{world.other_workspace_id}/posts"
    assert client.get(f"{other}/{post['id']}").status_code == 404  # cannot reach it via own workspace either


def test_delete_draft_only(client: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    url = f"{_base(world)}/{post['id']}"
    client.post(f"{url}/publish-now", headers=_h(client))
    assert client.delete(url, headers=_h(client)).status_code == 409
    client.post(f"{url}/unschedule", headers=_h(client))
    assert client.delete(url, headers=_h(client)).status_code == 204
    assert asyncio.run(_query(select(Post.id))) == []


def test_link_card_create_update_remove(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    res = client.post(
        _base(world),
        json={
            "account_id": str(acc),
            "body": "Read this",
            "link": {"url": "https://example.com/a", "title": "  A   title ", "description": None},
        },
        headers=_h(client),
    )
    assert res.status_code == 201, res.text
    post = res.json()
    assert post["link"] == {"url": "https://example.com/a", "title": "A title", "description": None}
    url = f"{_base(world)}/{post['id']}"

    # Changing only the card makes a new version; leaving `link` out keeps it.
    upd = client.patch(
        url, json={"link": {"url": "https://example.com/b", "title": "B"}}, headers=_h(client)
    ).json()
    assert upd["link"]["url"] == "https://example.com/b" and upd["version_count"] == 2
    same = client.patch(url, json={"body": "Read this now"}, headers=_h(client)).json()
    assert same["link"]["url"] == "https://example.com/b" and same["version_count"] == 3
    gone = client.patch(url, json={"link": None}, headers=_h(client)).json()
    assert gone["link"] is None and gone["version_count"] == 4

    for bad in (
        {"url": "ftp://x/y", "title": "t"},
        {"url": "https://u:p@example.com/", "title": "t"},
        {"url": "https://example.com/", "title": ""},
    ):
        assert (
            client.post(
                _base(world), json={"account_id": str(acc), "body": "x", "link": bad}, headers=_h(client)
            ).status_code
            == 422
        )


def test_hashtag_suggestions(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    # Non-Latin hashtag via escapes so this file stays English-only for check_english.
    he_tag = "\u05e9\u05d9\u05d5\u05d5\u05e7"
    _create(client, world, acc, body=f"Launch day #Launch #{he_tag}")
    _create(client, world, acc, body="Again #launch and#notatag")
    tags = client.get(f"{_base(world)}/hashtags").json()["recent"]
    assert [t.casefold() for t in tags] == ["launch", he_tag]  # most recent spelling wins
    client.cookies.clear()
    login(client, "stranger@example.com")
    assert client.get(f"{_base(world)}/hashtags").status_code == 404


def test_link_preview_endpoint(client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from heardloop import linkpreview
    from heardloop.api import routes_posts

    async def fake_fetch(http: object, url: str) -> linkpreview.LinkPreview:
        if "fail" in url:
            raise linkpreview.LinkPreviewError("blocked_destination")
        return linkpreview.LinkPreview(url=url, title="T", description="D", image_url=None, site_name="S")

    monkeypatch.setattr(routes_posts, "fetch_preview", fake_fetch)
    login(client, "viewer@example.com")
    assert (
        client.post(
            f"{_base(world)}/link-preview", json={"url": "https://example.com/"}, headers=_h(client)
        ).status_code
        == 403
    )
    client.cookies.clear()
    login(client, "editor@example.com")
    ok = client.post(f"{_base(world)}/link-preview", json={"url": "https://example.com/"}, headers=_h(client))
    assert ok.status_code == 200 and ok.json()["title"] == "T"
    bad = client.post(
        f"{_base(world)}/link-preview", json={"url": "https://example.com/fail"}, headers=_h(client)
    )
    assert bad.status_code == 422 and bad.json()["detail"] == "link_preview_failed:blocked_destination"
