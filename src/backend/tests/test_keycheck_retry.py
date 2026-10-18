"""keycheck retries when the database is unreachable, then fails startup."""

import base64
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop.keycheck import (
    KEYCHECK_ATTEMPTS,
    TokenKeyUnverifiedError,
    ensure_token_key,
    fingerprint,
)


def _key() -> str:
    return base64.b64encode(b"0" * 32).decode()


@pytest.mark.asyncio
async def test_ensure_token_key_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    key = _key()
    current = fingerprint(key)

    async def _fake_check(factory: async_sessionmaker[AsyncSession], fp: str) -> None:
        assert fp == current
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("db down")

    monkeypatch.setattr("heardloop.keycheck._check", _fake_check)
    sleep = AsyncMock()
    monkeypatch.setattr("heardloop.keycheck.asyncio.sleep", sleep)

    factory = AsyncMock()  # never opened; _check is faked
    await ensure_token_key(factory, key)  # type: ignore[arg-type]
    assert calls["n"] == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_ensure_token_key_fails_after_all_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _always_down(factory: async_sessionmaker[AsyncSession], fp: str) -> None:
        raise OSError("db down")

    monkeypatch.setattr("heardloop.keycheck._check", _always_down)
    sleep = AsyncMock()
    monkeypatch.setattr("heardloop.keycheck.asyncio.sleep", sleep)

    with pytest.raises(TokenKeyUnverifiedError):
        await ensure_token_key(AsyncMock(), _key())  # type: ignore[arg-type]
    assert sleep.await_count == KEYCHECK_ATTEMPTS - 1
