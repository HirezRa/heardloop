"""HEARDLOOP_TOKEN_KEY fingerprint guard. Needs TEST_DATABASE_URL."""

import base64
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from heardloop.keycheck import TokenKeyMismatchError, ensure_token_key, fingerprint
from heardloop.models import AppMeta, Base

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_first_start_stores_then_same_key_passes(factory: async_sessionmaker[AsyncSession]) -> None:
    key = _key()
    await ensure_token_key(factory, key)
    await ensure_token_key(factory, key)
    async with factory() as db:
        row = await db.get(AppMeta, "token_key_fingerprint")
    assert row is not None and row.value == fingerprint(key)
    assert key not in row.value


async def test_different_key_refuses_to_start(factory: async_sessionmaker[AsyncSession]) -> None:
    await ensure_token_key(factory, _key())
    other = _key()
    with pytest.raises(TokenKeyMismatchError) as info:
        await ensure_token_key(factory, other)
    assert other not in str(info.value)


def test_fingerprint_is_stable_and_not_the_key() -> None:
    key = _key()
    assert fingerprint(key) == fingerprint(key) != fingerprint(_key())
    assert len(fingerprint(key)) == 32
