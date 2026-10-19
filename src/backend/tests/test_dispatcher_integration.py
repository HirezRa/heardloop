"""Runs against a real PostgreSQL. Skipped unless TEST_DATABASE_URL is set (CI sets it).

Example: TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/heardloop_test
Never point this at a database with real data - it drops and recreates all tables.
"""

import base64
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from heardloop.config import get_settings
from heardloop.domain.workflow import PostStatus
from heardloop.models import (
    AuditLogEntry,
    Base,
    LinkedInAccount,
    LinkedInAccountStatus,
    LinkedInAccountType,
    Membership,
    Notification,
    Post,
    PostVersion,
    User,
    Workspace,
    WorkspaceRole,
)
from heardloop.publishing.base import PublishRequest, PublishResult
from heardloop.security.crypto import TokenProtector
from heardloop.worker import dispatcher

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
TOKEN = "test-token-value"  # noqa: S105  # fake, test-only
PROTECTOR = TokenProtector(base64.b64encode(os.urandom(32)).decode())


async def run_once(factory, publisher, clock):  # type: ignore[no-untyped-def]
    return await dispatcher.run_once(factory, publisher, clock, PROTECTOR)


@pytest.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert URL
    # The notification path reads Settings; give it the minimum so it really runs here.
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    # Leave a clean DB so host alembic upgrade/check after pytest can run.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _scheduled_post(
    factory: async_sessionmaker[AsyncSession],
    at: datetime,
    *,
    body: str = "Hello LinkedIn",
    account_status: LinkedInAccountStatus = LinkedInAccountStatus.ACTIVE,
    token_expires: datetime | None = None,
    link: tuple[str, str, str | None] | None = None,
) -> uuid.UUID:
    async with factory() as s:
        ws = Workspace(name="Test", time_zone="Asia/Jerusalem", created_at=NOW)
        author = User(email=f"{uuid.uuid4().hex}@example.com", display_name="Author", created_at=NOW)
        s.add_all([ws, author])
        await s.flush()
        s.add(Membership(user_id=author.id, workspace_id=ws.id, role=WorkspaceRole.EDITOR))
        acc_id = uuid.uuid4()
        acc = LinkedInAccount(
            id=acc_id,
            workspace_id=ws.id,
            type=LinkedInAccountType.PERSON,
            urn="urn:li:person:example",
            display_name="Example",
            access_token_encrypted=PROTECTOR.protect(TOKEN, f"linkedin-account:{acc_id}"),
            access_token_expires_at=token_expires or NOW + timedelta(days=60),
            connected_at=NOW,
            status=account_status,
        )
        s.add(acc)
        await s.flush()
        post = Post(workspace_id=ws.id, account_id=acc.id, created_at=NOW, updated_at=NOW)
        post.transition_to(PostStatus.SCHEDULED, require_approval=False, now=NOW)
        post.scheduled_at = at
        s.add(post)
        await s.flush()
        version = PostVersion(post_id=post.id, body=body, author_id=author.id, created_at=NOW)
        if link:
            version.link_url, version.link_title, version.link_description = link
        s.add(version)
        await s.flush()
        post.current_version_id = version.id
        await s.commit()
        return post.id


class FakePublisher:
    def __init__(self, *results: PublishResult) -> None:
        self.results = list(results)
        self.calls = 0

    async def publish(self, request: PublishRequest) -> PublishResult:
        self.calls += 1
        self.last = request
        return self.results.pop(0)


async def _status(factory: async_sessionmaker[AsyncSession], post_id: uuid.UUID) -> Post:
    async with factory() as s:
        post = await s.get(Post, post_id)
        assert post is not None
        return post


async def test_future_post_is_not_claimed(factory: async_sessionmaker[AsyncSession]) -> None:
    post_id = await _scheduled_post(factory, NOW + timedelta(minutes=10))
    publisher = FakePublisher()
    assert await run_once(factory, publisher, lambda: NOW) == 0
    assert (await _status(factory, post_id)).status is PostStatus.SCHEDULED


async def test_due_post_is_published_once(factory: async_sessionmaker[AsyncSession]) -> None:
    post_id = await _scheduled_post(factory, NOW - timedelta(minutes=1))
    publisher = FakePublisher(PublishResult.published("urn:li:share:1"))
    assert await run_once(factory, publisher, lambda: NOW) == 1
    assert await run_once(factory, publisher, lambda: NOW) == 0
    post = await _status(factory, post_id)
    assert post.status is PostStatus.PUBLISHED
    assert post.linkedin_post_urn == "urn:li:share:1"
    assert publisher.calls == 1


async def test_transient_errors_retry_then_fail(factory: async_sessionmaker[AsyncSession]) -> None:
    post_id = await _scheduled_post(factory, NOW)
    err = PublishResult.transient_error("rate_limited", "429", 429)
    publisher = FakePublisher(err, err, err)
    clock = NOW
    for step in (timedelta(0), timedelta(minutes=1), timedelta(minutes=5)):
        clock += step
        assert await run_once(factory, publisher, lambda c=clock: c) == 1
    post = await _status(factory, post_id)
    assert post.status is PostStatus.FAILED
    assert post.attempt_count == 3
    assert publisher.calls == 3


async def test_retry_waits_for_backoff(factory: async_sessionmaker[AsyncSession]) -> None:
    await _scheduled_post(factory, NOW)
    publisher = FakePublisher(PublishResult.transient_error("timeout", "t"))
    await run_once(factory, publisher, lambda: NOW)
    assert await run_once(factory, publisher, lambda: NOW + timedelta(seconds=30)) == 0


async def test_request_carries_decrypted_token_and_text(factory: async_sessionmaker[AsyncSession]) -> None:
    await _scheduled_post(factory, NOW, body="Line one\nLine two")
    publisher = FakePublisher(PublishResult.published("urn:li:share:9"))
    await run_once(factory, publisher, lambda: NOW)
    assert publisher.last.access_token == TOKEN
    assert publisher.last.text == "Line one\nLine two"
    assert publisher.last.author_urn == "urn:li:person:example"
    assert TOKEN not in repr(publisher.last)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"body": "   "}, "no_content"),
        ({"account_status": LinkedInAccountStatus.REVOKED}, "account_not_connected"),
        ({"token_expires": NOW - timedelta(minutes=1)}, "token_expired"),
    ],
)
async def test_unpublishable_posts_fail_without_calling_linkedin(
    factory: async_sessionmaker[AsyncSession], kwargs: dict[str, object], code: str
) -> None:
    post_id = await _scheduled_post(factory, NOW, **kwargs)  # type: ignore[arg-type]
    publisher = FakePublisher()
    await run_once(factory, publisher, lambda: NOW)
    post = await _status(factory, post_id)
    assert post.status is PostStatus.FAILED
    assert post.last_error is not None and post.last_error.startswith(code)
    assert publisher.calls == 0


async def test_image_is_loaded_and_uploaded_urn_cached(
    factory: async_sessionmaker[AsyncSession],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    from heardloop import media
    from heardloop.config import get_settings
    from heardloop.models import MediaAsset

    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", URL or "")
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()

    post_id = await _scheduled_post(factory, NOW)
    png = b"\x89PNG\r\n\x1a\n" + b"\x01" * 16
    async with factory() as s:
        post = await s.get(Post, post_id)
        assert post is not None
        kind = media.detect_image(png)
        assert kind is not None
        asset = MediaAsset(
            workspace_id=post.workspace_id,
            storage_key=media.save(tmp_path, post.workspace_id, png, kind),
            mime_type=kind.mime,
            size_bytes=len(png),
            sha256=media.sha256(png),
            created_at=NOW,
        )
        s.add(asset)
        await s.flush()
        version = await s.get(PostVersion, post.current_version_id)
        assert version is not None
        version.media_ids = [asset.id]
        await s.commit()
        asset_id = asset.id

    ok = PublishResult.published("urn:li:share:5")
    publisher = FakePublisher(
        PublishResult(
            ok.success, ok.transient, ok.post_urn, ok.http_status, uploaded_image_urn="urn:li:image:X1"
        )
    )
    await run_once(factory, publisher, lambda: NOW)
    assert publisher.last.image is not None and publisher.last.image.data == png
    async with factory() as s:
        cached = await s.get(MediaAsset, asset_id)
        assert cached is not None
        assert (
            cached.linkedin_asset_urn == "urn:li:image:X1"
            and cached.linkedin_owner_urn == "urn:li:person:example"
        )
    get_settings.cache_clear()


async def test_link_card_is_passed_to_publisher(factory: async_sessionmaker[AsyncSession]) -> None:
    await _scheduled_post(factory, NOW, link=("https://example.com/a", "Title", None))
    publisher = FakePublisher(PublishResult.published("urn:li:share:10"))
    await run_once(factory, publisher, lambda: NOW)
    assert publisher.last.link is not None
    assert (publisher.last.link.url, publisher.last.link.title) == ("https://example.com/a", "Title")
    assert publisher.last.link.description is None
    async with factory() as s:
        kinds = list((await s.execute(select(Notification.kind))).scalars())
    assert kinds == ["post.published"]  # the author is told; notifications are created after the commit


async def test_publish_success_writes_audit(factory: async_sessionmaker[AsyncSession]) -> None:
    post_id = await _scheduled_post(factory, NOW)
    await run_once(factory, FakePublisher(PublishResult.published("urn:li:share:audit")), lambda: NOW)
    async with factory() as s:
        row = (
            await s.execute(select(AuditLogEntry).where(AuditLogEntry.action == "post.published"))
        ).scalar_one()
    assert row.actor_id is None
    assert row.entity_id == str(post_id)
    assert row.diff_json == {"linkedin_post_urn": "urn:li:share:audit", "attempt": 1}


async def test_token_unreadable_writes_audit_and_flags_account(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    post_id = await _scheduled_post(factory, NOW)
    async with factory() as s:
        post = await s.get(Post, post_id)
        assert post is not None
        # Owners receive account.reconnect_needed; the seeded world only has an editor.
        owner = User(email=f"{uuid.uuid4().hex}@example.com", display_name="Owner", created_at=NOW)
        s.add(owner)
        await s.flush()
        s.add(Membership(user_id=owner.id, workspace_id=post.workspace_id, role=WorkspaceRole.OWNER))
        acc = await s.get(LinkedInAccount, post.account_id)
        assert acc is not None
        # Ciphertext from another key cannot be decrypted with PROTECTOR.
        other = TokenProtector(base64.b64encode(os.urandom(32)).decode())
        acc.access_token_encrypted = other.protect(TOKEN, f"linkedin-account:{acc.id}")
        await s.commit()
        account_id = acc.id
        workspace_id = post.workspace_id

    await run_once(factory, FakePublisher(), lambda: NOW)
    post = await _status(factory, post_id)
    assert post.status is PostStatus.FAILED
    assert post.last_error is not None and post.last_error.startswith("token_unreadable")

    async with factory() as s:
        audit_row = (
            await s.execute(select(AuditLogEntry).where(AuditLogEntry.action == "post.publish_failed"))
        ).scalar_one()
        acc = await s.get(LinkedInAccount, account_id)
        assert acc is not None
        notifs = list(
            (
                await s.execute(select(Notification).where(Notification.kind == "account.reconnect_needed"))
            ).scalars()
        )
    assert audit_row.actor_id is None
    assert audit_row.workspace_id == workspace_id
    assert audit_row.diff_json["reason"] == "token_unreadable"
    assert audit_row.diff_json["will_retry"] is False
    assert acc.needs_reconnect_reason == "token_unreadable"
    assert acc.needs_reconnect_at == NOW
    assert len(notifs) >= 1
    assert notifs[0].data["account_id"] == str(account_id)
    assert notifs[0].data["reason"] == "token_unreadable"

    # Second failure keeps the earliest reason and does not duplicate the notification within 24 h.
    async with factory() as s:
        post = await s.get(Post, post_id)
        assert post is not None
        post.transition_to(PostStatus.SCHEDULED, require_approval=False, now=NOW)
        post.scheduled_at = NOW
        post.attempt_count = 0
        post.last_error = None
        post.locked_until = None
        await s.commit()
    later = NOW + timedelta(minutes=5)
    await run_once(factory, FakePublisher(), lambda: later)
    async with factory() as s:
        acc = await s.get(LinkedInAccount, account_id)
        assert acc is not None
        assert acc.needs_reconnect_reason == "token_unreadable"
        assert acc.needs_reconnect_at == NOW
        n_count = (
            (await s.execute(select(Notification).where(Notification.kind == "account.reconnect_needed")))
            .scalars()
            .all()
        )
    assert len(list(n_count)) == len(notifs)
