"""Migration 0013 (empty imported text, one post per URN) on a seeded database. Needs TEST_DATABASE_URL.

Drops and recreates all tables - throwaway databases only.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.domain.workflow import PostStatus
from heardloop.models import Post, PostVersion, User
from tests.test_auth_integration import World, world  # noqa: F401
from tests.test_posts_integration import _add_account

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

SENTINEL = "Imported from LinkedIn (text not available)"
BACKEND = Path(__file__).resolve().parents[1]


def _config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


async def _add_post(
    world: World,  # noqa: F811
    acc: uuid.UUID,
    body: str,
    *,
    external: bool,
    urn: str | None,
) -> uuid.UUID:
    assert URL
    engine = create_async_engine(URL)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        now = datetime.now(UTC)
        post = Post(
            workspace_id=world.workspace_id,
            account_id=acc,
            status=PostStatus.PUBLISHED,
            published_at=now,
            linkedin_post_urn=urn,
            external_source="linkedin_export" if external else None,
            created_at=now,
            updated_at=now,
        )
        db.add(post)
        await db.flush()
        # An older version that still carries the sentinel must stay untouched: only the current one is data.
        author = (await db.execute(select(User.id).limit(1))).scalar_one()
        old = PostVersion(post_id=post.id, body=SENTINEL, media_ids=[], author_id=author, created_at=now)
        current = PostVersion(post_id=post.id, body=body, media_ids=[], author_id=author, created_at=now)
        db.add_all([old, current])
        await db.flush()
        post.current_version_id = current.id
        await db.commit()
        post_id = post.id
    await engine.dispose()
    return post_id


async def _sql(statement: str, **params: object) -> list[tuple[object, ...]]:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        result = await conn.execute(text(statement), params)
        rows = [tuple(r) for r in result.all()] if result.returns_rows else []
    await engine.dispose()
    return rows


def _bodies(post_ids: list[uuid.UUID]) -> list[str]:
    rows = asyncio.run(
        _sql(
            "SELECT p.id, v.body FROM posts p JOIN post_versions v ON v.id = p.current_version_id "
            "WHERE p.id = ANY(CAST(:ids AS uuid[]))",
            ids=[str(p) for p in post_ids],
        )
    )
    by_id = {str(r[0]): str(r[1]) for r in rows}
    return [by_id[str(p)] for p in post_ids]


def _at_0012() -> None:
    """The schema as 0012 left it: no unique URN index, alembic stamped at 0012."""
    asyncio.run(_sql("DROP INDEX ux_posts_workspace_urn"))
    command.stamp(_config(), "0012")


def _cleanup() -> None:
    asyncio.run(_sql("DROP TABLE IF EXISTS alembic_version"))


def test_up_and_down_on_seeded_sentinel_rows(world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    imported_sentinel = asyncio.run(
        _add_post(world, acc, SENTINEL, external=True, urn="urn:li:share:7114500000000000001")
    )
    imported_real = asyncio.run(
        _add_post(world, acc, "Real text", external=True, urn="urn:li:share:7114500000000000002")
    )
    imported_empty = asyncio.run(
        _add_post(world, acc, "", external=True, urn="urn:li:share:7114500000000000003")
    )
    normal_sentinel = asyncio.run(_add_post(world, acc, SENTINEL, external=False, urn=None))
    ids = [imported_sentinel, imported_real, imported_empty, normal_sentinel]
    try:
        _at_0012()
        command.upgrade(_config(), "0013")
        # Only imported posts whose current text is the sentinel are emptied.
        assert _bodies(ids) == ["", "Real text", "", SENTINEL]
        old_versions = asyncio.run(
            _sql("SELECT count(*) FROM post_versions WHERE body = :sentinel", sentinel=SENTINEL)
        )
        assert old_versions == [(5,)]  # the four older versions and the normal post's current one remain
        index = asyncio.run(
            _sql("SELECT indexdef FROM pg_indexes WHERE indexname = 'ux_posts_workspace_urn'")
        )
        assert len(index) == 1 and "UNIQUE" in str(index[0][0]) and "IS NOT NULL" in str(index[0][0])

        command.downgrade(_config(), "0012")
        # An imported post with no text gets the old sentinel back (including the one that was empty before).
        assert _bodies(ids) == [SENTINEL, "Real text", SENTINEL, SENTINEL]
        assert asyncio.run(_sql("SELECT 1 FROM pg_indexes WHERE indexname = 'ux_posts_workspace_urn'")) == []

        command.upgrade(_config(), "0013")  # and up again
        assert _bodies(ids) == ["", "Real text", "", SENTINEL]
    finally:
        _cleanup()


def test_duplicate_urns_stop_the_migration_with_a_clear_message(world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    urn = "urn:li:share:7114500000000000009"
    first = asyncio.run(_add_post(world, acc, SENTINEL, external=True, urn=None))
    asyncio.run(_add_post(world, acc, "Other", external=True, urn=None))
    try:
        asyncio.run(_sql("DROP INDEX ux_posts_workspace_urn"))
        asyncio.run(_sql("UPDATE posts SET linkedin_post_urn = :urn", urn=urn))
        command.stamp(_config(), "0012")
        with pytest.raises(RuntimeError, match="ux_posts_workspace_urn"):
            command.upgrade(_config(), "0013")
        # The whole migration rolled back: the sentinel is still there and the revision did not move.
        assert _bodies([first]) == [SENTINEL]
        assert asyncio.run(_sql("SELECT version_num FROM alembic_version")) == [("0012",)]
    finally:
        _cleanup()
