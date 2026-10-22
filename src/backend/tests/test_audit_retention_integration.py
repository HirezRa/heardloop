"""Activity-log IP retention (AUDIT_IP_RETENTION_DAYS). Needs TEST_DATABASE_URL (throwaway database only)."""

# ruff: noqa: F811  # pytest fixtures are imported and then used as test arguments

import asyncio
import os
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.audit import clear_old_ip_addresses
from heardloop.models import AuditLogEntry
from heardloop.timeutil import utcnow
from tests.test_auth_integration import World, _query, world  # noqa: F401

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

OLD_IP = "198.51.100.7"  # privacy-allow  # documentation range
NEW_IP = "198.51.100.8"  # privacy-allow


def test_old_entries_lose_only_their_ip_address(world: World) -> None:
    async def go(days: int) -> int:
        assert URL
        engine = create_async_engine(URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        now = utcnow()
        async with factory() as db:
            db.add_all(
                [
                    AuditLogEntry(
                        action="test.old", entity="x", ip_address=OLD_IP, at=now - timedelta(days=40)
                    ),
                    AuditLogEntry(
                        action="test.new", entity="x", ip_address=NEW_IP, at=now - timedelta(days=5)
                    ),
                ]
            )
            await db.commit()
        cleared = await clear_old_ip_addresses(factory, now, retention_days=days)
        await engine.dispose()
        return cleared

    assert asyncio.run(go(0)) == 0  # off by default: nothing changes
    rows = dict(
        asyncio.run(
            _query(
                select(AuditLogEntry.action, AuditLogEntry.ip_address).where(
                    AuditLogEntry.action.like("test.%")
                )
            )
        )
    )
    assert rows == {"test.old": OLD_IP, "test.new": NEW_IP}

    assert asyncio.run(go(30)) >= 1
    rows = dict(
        asyncio.run(
            _query(
                select(AuditLogEntry.action, AuditLogEntry.ip_address).where(
                    AuditLogEntry.action.like("test.%"), AuditLogEntry.ip_address.is_not(None)
                )
            )
        )
    )
    assert OLD_IP not in rows.values() and NEW_IP in rows.values()  # the entries stay, only old addresses go
    kept = asyncio.run(_query(select(AuditLogEntry.id).where(AuditLogEntry.action == "test.old")))
    assert kept
