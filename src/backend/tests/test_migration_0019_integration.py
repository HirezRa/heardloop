"""Migration 0019 (posts.linkedin_activity_urn) up / check / down / up. Needs TEST_DATABASE_URL."""

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from heardloop.models import Base
from tests.test_auth_integration import World, world  # noqa: F401

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

BACKEND = Path(__file__).resolve().parents[1]


def _config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


async def _sql(statement: str) -> list[tuple[object, ...]]:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        result = await conn.execute(text(statement))
        rows = [tuple(r) for r in result.all()] if result.returns_rows else []
    await engine.dispose()
    return rows


async def _wipe() -> None:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


def test_0019_up_check_down_up(world: World) -> None:  # noqa: F811
    assert URL
    asyncio.run(_wipe())
    cfg = _config()
    command.upgrade(cfg, "0018")
    command.upgrade(cfg, "0019")
    command.check(cfg)
    cols = asyncio.run(
        _sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='posts' AND column_name='linkedin_activity_urn'"
        )
    )
    assert cols == [(1,)]
    indexes = asyncio.run(_sql("SELECT indexname FROM pg_indexes WHERE tablename='posts'"))
    assert "ux_posts_workspace_activity_urn" in {i[0] for i in indexes}
    command.downgrade(cfg, "0018")
    cols = asyncio.run(
        _sql(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='posts' AND column_name='linkedin_activity_urn'"
        )
    )
    assert cols == []
    command.upgrade(cfg, "0019")
    command.check(cfg)
