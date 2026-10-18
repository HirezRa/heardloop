"""Notifications: in-app rows, e-mail outbox, webhook outbox and delivery. Needs TEST_DATABASE_URL."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop import delivery
from heardloop.models import LinkedInAccount, Membership, Notification, User, WebhookDelivery
from heardloop.security.crypto import TokenProtector
from tests.test_auth_integration import World, _query, client, login, world  # noqa: F401
from tests.test_posts_integration import _add_account, _base, _create, _h

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _factory():  # type: ignore[no-untyped-def]
    assert URL
    engine = create_async_engine(URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _user_id(email: str) -> uuid.UUID:
    return (await _query(select(User.id).where(User.email == email)))[0][0]  # type: ignore[no-any-return]


def _owner_login(c: TestClient) -> None:
    login(c, "owner@example.com")  # the seeded owner has two-step verification on


def _submit(c: TestClient, world: World, acc: uuid.UUID) -> dict:  # type: ignore[type-arg]  # noqa: F811
    post = _create(c, world, acc)
    assert c.post(f"{_base(world)}/{post['id']}/submit", headers=_h(c)).status_code == 200
    return post


def test_submit_notifies_reviewers_not_the_author(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _submit(client, world, acc)

    rows = asyncio.run(_query(select(Notification.user_id, Notification.kind, Notification.data)))
    recipients = {r[0] for r in rows}
    owner, approver, editor = (
        asyncio.run(_user_id(e)) for e in ("owner@example.com", "approver@example.com", "editor@example.com")
    )
    assert recipients == {owner, approver} and editor not in recipients
    assert all(r[1] == "post.submitted" for r in rows)
    # Never the post text in notification data.
    assert all("Hello LinkedIn" not in json.dumps(r[2]) for r in rows)

    client.cookies.clear()
    login(client, "approver@example.com")
    page = client.get("/api/v1/notifications").json()
    assert page["unread"] == 1 and page["items"][0]["post_id"] == post["id"]
    assert page["items"][0]["data"]["actor_name"]
    nid = page["items"][0]["id"]
    assert client.post(f"/api/v1/notifications/{nid}/read", headers=_h(client)).status_code == 204
    assert client.get("/api/v1/notifications/unread-count").json() == {"unread": 0}


def test_reject_notifies_author_with_comment(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _submit(client, world, acc)
    client.cookies.clear()
    login(client, "approver@example.com")
    url = f"{_base(world)}/{post['id']}/reject"
    assert client.post(url, json={"comment": "Shorter please"}, headers=_h(client)).status_code == 200
    client.cookies.clear()
    login(client, "editor@example.com")
    items = client.get("/api/v1/notifications").json()["items"]
    assert items[0]["kind"] == "post.rejected" and items[0]["data"]["comment"] == "Shorter please"


def test_cannot_read_or_mark_other_users_notifications(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    _submit(client, world, acc)
    nid = asyncio.run(_query(select(Notification.id)))[0][0]
    client.cookies.clear()
    login(client, "viewer@example.com")
    assert client.get("/api/v1/notifications").json()["items"] == []
    assert client.post(f"/api/v1/notifications/{nid}/read", headers=_h(client)).status_code == 404


def test_email_preference(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "editor@example.com")
    assert client.get("/api/v1/notifications/preferences").json()["email_notifications"] is True
    res = client.put(
        "/api/v1/notifications/preferences", json={"email_notifications": False}, headers=_h(client)
    )
    assert res.status_code == 200 and res.json()["email_notifications"] is False
    assert asyncio.run(
        _query(select(User.email_notifications).where(User.email == "editor@example.com"))
    ) == [(False,)]


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = fail

    async def send(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise OSError("smtp down")
        self.sent.append((to, subject, body))


def test_email_delivery(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    _submit(client, world, acc)

    async def go() -> tuple[FakeSender, list]:  # type: ignore[type-arg]
        engine, factory = _factory()
        async with engine.begin() as conn:  # pretend SMTP was configured when they were created
            await conn.execute(update(Notification).values(email_status="pending"))
            await conn.execute(
                update(User).where(User.email == "owner@example.com").values(email_notifications=False)
            )
        sender = FakeSender()
        await delivery.deliver_emails(factory, sender, lambda: datetime.now(UTC))
        async with engine.connect() as conn:
            rows = (await conn.execute(select(Notification.email_status))).all()
        await engine.dispose()
        return sender, rows

    sender, rows = asyncio.run(go())
    assert [s[0] for s in sender.sent] == ["approver@example.com"]  # owner opted out
    assert "waiting for your approval" in sender.sent[0][1]
    assert "Hello LinkedIn" not in sender.sent[0][2]  # no post text in e-mails
    assert sorted(r[0] for r in rows) == ["sent", "skipped"]


def test_email_failure_retries_then_gives_up(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    _submit(client, world, acc)

    async def go() -> list:  # type: ignore[type-arg]
        engine, factory = _factory()
        async with engine.begin() as conn:
            await conn.execute(update(Notification).values(email_status="pending"))
        for _ in range(delivery.EMAIL_MAX_ATTEMPTS * 2):  # one failure ends a cycle; two e-mails queued
            await delivery.deliver_emails(factory, FakeSender(fail=True), lambda: datetime.now(UTC))
        async with engine.connect() as conn:
            rows = (await conn.execute(select(Notification.email_status, Notification.email_attempts))).all()
        await engine.dispose()
        return rows

    assert all(r == ("failed", delivery.EMAIL_MAX_ATTEMPTS) for r in asyncio.run(go()))


def test_webhook_config_owner_only(client: TestClient, world: World) -> None:  # noqa: F811
    base = f"/api/v1/workspaces/{world.workspace_id}/webhook"
    login(client, "approver@example.com")
    assert client.get(base).status_code == 403
    client.cookies.clear()

    _owner_login(client)
    for bad in ("ftp://example.com/x", "https://user:pw@example.com/hook", "not a url"):
        assert client.put(base, json={"url": bad}, headers=_h(client)).status_code == 422
    first = client.put(base, json={"url": "https://hooks.example.com/in"}, headers=_h(client)).json()
    assert first["configured"] and first["secret"]
    again = client.put(
        base, json={"url": "https://hooks.example.com/v2", "include_text": True}, headers=_h(client)
    ).json()
    assert again["secret"] is None and again["include_text"] is True  # the secret is shown only once
    rotated = client.post(f"{base}/rotate-secret", headers=_h(client)).json()
    assert rotated["secret"] and rotated["secret"] != first["secret"]
    assert client.post(f"{base}/test", headers=_h(client)).status_code == 202
    assert client.delete(base, headers=_h(client)).status_code == 204
    assert client.post(f"{base}/test", headers=_h(client)).status_code == 409
    statuses = asyncio.run(_query(select(WebhookDelivery.status, WebhookDelivery.last_error)))
    assert statuses == [("failed", "webhook_removed")]


def test_webhook_delivery_signed_and_retried(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acc = asyncio.run(_add_account(world))
    base = f"/api/v1/workspaces/{world.workspace_id}/webhook"
    _owner_login(client)
    secret = client.put(base, json={"url": "https://hooks.example.com/in"}, headers=_h(client)).json()[
        "secret"
    ]
    client.cookies.clear()
    login(client, "editor@example.com")
    _submit(client, world, acc)

    seen: list[httpx.Request] = []
    replies = iter([503, 200])

    async def allow(url: str) -> None:  # no DNS in tests
        return None

    monkeypatch.setattr(delivery, "check_destination", allow)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(next(replies))

    async def go() -> list:  # type: ignore[type-arg]
        engine, factory = _factory()
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        protector = TokenProtector(world.token_key)
        now = datetime.now(UTC)
        await delivery.deliver_webhooks(factory, http, protector, lambda: now)
        # Second try only after the backoff.
        await delivery.deliver_webhooks(factory, http, protector, lambda: now + timedelta(seconds=30))
        await delivery.deliver_webhooks(factory, http, protector, lambda: now + timedelta(minutes=2))
        await http.aclose()
        async with engine.connect() as conn:
            rows = (await conn.execute(select(WebhookDelivery.status, WebhookDelivery.attempts))).all()
        await engine.dispose()
        return rows

    rows = asyncio.run(go())
    assert rows == [("sent", 2)] and len(seen) == 2
    req = seen[-1]
    ts = req.headers["X-Heardloop-Timestamp"]
    assert req.headers["X-Heardloop-Signature"] == delivery.sign(secret, ts, req.content)
    payload = json.loads(req.content)
    assert payload["event"] == "post.submitted" and "text" not in payload["post"]


def test_expiring_token_reminder_once(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world, expires_in=timedelta(days=3)))

    async def go() -> tuple[int, int, list]:  # type: ignore[type-arg]
        engine, factory = _factory()
        clock = lambda: datetime.now(UTC)  # noqa: E731
        first = await delivery.remind_expiring_tokens(factory, clock)
        second = await delivery.remind_expiring_tokens(factory, clock)
        async with engine.connect() as conn:
            rows = (await conn.execute(select(Notification.kind, Notification.data))).all()
            await conn.execute(select(LinkedInAccount.id))
        await engine.dispose()
        return first, second, rows

    first, second, rows = asyncio.run(go())
    assert (first, second) == (1, 0)
    assert len(rows) == 1 and rows[0][0] == "account.expiring" and rows[0][1]["account_id"] == str(acc)


@pytest.mark.parametrize(
    "url",
    [
        "http://db:5432/",
        "http://localhost:8000/x",
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest",  # privacy-allow
        "http://[::1]/",
    ],
)
def test_webhook_destination_guard_blocks(url: str) -> None:
    assert asyncio.run(delivery.check_destination(url)) == "blocked_destination"


def test_webhook_destination_guard_allows_private_network() -> None:
    # n8n on the same Docker network or LAN is the main use case.
    url = "http://203.0.113.5:5678/webhook/x"  # privacy-allow
    assert asyncio.run(delivery.check_destination(url)) is None


def test_removed_member_gets_nothing(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc)

    async def remove_editor() -> None:
        engine, _ = _factory()
        async with engine.begin() as conn:
            uid = (await conn.execute(select(User.id).where(User.email == "editor@example.com"))).scalar_one()
            await conn.execute(delete(Membership).where(Membership.user_id == uid))
        await engine.dispose()

    client.post(f"{_base(world)}/{post['id']}/submit", headers=_h(client))
    asyncio.run(remove_editor())
    client.cookies.clear()
    login(client, "approver@example.com")
    client.post(f"{_base(world)}/{post['id']}/reject", json={"comment": "No"}, headers=_h(client))
    kinds = [r[0] for r in asyncio.run(_query(select(Notification.kind)))]
    assert "post.rejected" not in kinds  # the author left, nobody is told


def test_webhook_text_and_comment_only_when_allowed(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acc = asyncio.run(_add_account(world))
    base = f"/api/v1/workspaces/{world.workspace_id}/webhook"
    login(client, "owner@example.com")
    client.put(base, json={"url": "https://hooks.example.com/in"}, headers=_h(client))
    client.cookies.clear()
    login(client, "editor@example.com")
    post = _submit(client, world, acc)
    client.cookies.clear()
    login(client, "approver@example.com")
    client.post(f"{_base(world)}/{post['id']}/reject", json={"comment": "Shorter"}, headers=_h(client))
    client.cookies.clear()
    login(client, "owner@example.com")

    bodies: list[dict] = []  # type: ignore[type-arg]

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(204)

    async def allow(url: str) -> None:
        return None

    monkeypatch.setattr(delivery, "check_destination", allow)

    async def send_all() -> None:
        engine, factory = _factory()
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await delivery.deliver_webhooks(
            factory, http, TokenProtector(world.token_key), lambda: datetime.now(UTC)
        )
        await http.aclose()
        await engine.dispose()

    # Turned on after the events were queued: the decision is made at send time.
    client.put(base, json={"url": "https://hooks.example.com/in", "include_text": True}, headers=_h(client))
    asyncio.run(send_all())
    rejected = next(b for b in bodies if b["event"] == "post.rejected")
    assert rejected["post"]["text"] == "Hello LinkedIn" and rejected["data"]["comment"] == "Shorter"
    assert rejected["data"]["actor_name"] == "Approver"  # names travel only together with the text

    bodies.clear()
    client.put(base, json={"url": "https://hooks.example.com/in", "include_text": False}, headers=_h(client))
    client.post(f"{_base(world)}/{post['id']}/submit", headers=_h(client))
    client.post(f"{_base(world)}/{post['id']}/reject", json={"comment": "Again"}, headers=_h(client))
    asyncio.run(send_all())
    assert bodies and all("text" not in b.get("post", {}) and "comment" not in b["data"] for b in bodies)
    # Without include_text the payload keeps ids but carries no names either.
    assert all("actor_name" not in b["data"] and "account_name" not in b["data"] for b in bodies)
    assert all(b["post"]["id"] == post["id"] for b in bodies if "post" in b)


def test_changing_webhook_host_drops_queued_events(client: TestClient, world: World) -> None:  # noqa: F811
    base = f"/api/v1/workspaces/{world.workspace_id}/webhook"
    login(client, "owner@example.com")
    client.put(base, json={"url": "https://hooks.example.com/in"}, headers=_h(client))
    client.post(f"{base}/test", headers=_h(client))
    client.put(base, json={"url": "https://other.example.org/in"}, headers=_h(client))
    rows = asyncio.run(_query(select(WebhookDelivery.status, WebhookDelivery.last_error)))
    assert rows == [("failed", "webhook_changed")]


def test_destination_guard_private_toggle() -> None:
    url = "http://203.0.113.5:5678/webhook/x"  # privacy-allow
    assert asyncio.run(delivery.check_destination(url)) is None
    assert asyncio.run(delivery.check_destination(url, allow_private=False)) == "blocked_destination"
    assert asyncio.run(delivery.check_destination("http://[::ffff:127.0.0.1]/")) == "blocked_destination"
    assert asyncio.run(delivery.check_destination("http://[::1/")) == "blocked_destination"
