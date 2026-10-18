"""Migration 0016 (post_templates) up / check / down / up. Needs TEST_DATABASE_URL.

Drops and recreates tables - throwaway databases only.
"""

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


def test_0016_up_check_down_up(world: World) -> None:  # noqa: F811
    """world fixture builds the current schema; stamp 0015, then walk 0016."""
    assert URL
    # Drop everything and rebuild through alembic so "check" compares cleanly.
    asyncio.run(_wipe())
    cfg = _config()
    command.upgrade(cfg, "0015")
    command.upgrade(cfg, "0016")
    tables = asyncio.run(_sql("SELECT to_regclass('public.post_templates')"))
    assert tables[0][0] == "post_templates"
    command.downgrade(cfg, "0015")
    tables = asyncio.run(_sql("SELECT to_regclass('public.post_templates')"))
    assert tables[0][0] is None
    command.upgrade(cfg, "0016")
    tables = asyncio.run(_sql("SELECT to_regclass('public.post_templates')"))
    assert tables[0][0] == "post_templates"
    # Full-model check lives on head (0017); 0016 alone leaves app_meta.value behind the models.


async def _wipe() -> None:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()
