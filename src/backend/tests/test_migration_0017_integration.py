"""Migration 0017 (app_meta.value Text) up / down / up. Needs TEST_DATABASE_URL."""

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


def test_0017_up_down_up(world: World) -> None:  # noqa: F811
    assert URL
    asyncio.run(_wipe())
    cfg = _config()
    command.upgrade(cfg, "0016")
    command.upgrade(cfg, "0017")
    cols = asyncio.run(
        _sql(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name='app_meta' AND column_name='value'"
        )
    )
    assert cols[0][0] == "text"
    command.downgrade(cfg, "0016")
    cols = asyncio.run(
        _sql(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name='app_meta' AND column_name='value'"
        )
    )
    assert cols[0][0] == 256
    command.upgrade(cfg, "0017")
    # Full-model check lives on head (0018); 0017 alone leaves the audience columns behind the models.
