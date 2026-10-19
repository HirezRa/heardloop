"""create_owner bootstrap: time zone validation and workspace default."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.devtools.create_owner import create_owner, detect_local_time_zone, resolve_time_zone
from heardloop.models import Base, User, Workspace

URL = os.environ.get("TEST_DATABASE_URL")

PASSWORD = "a long enough passphrase"  # noqa: S105  # test-only


def test_resolve_time_zone_rejects_unknown() -> None:
    with pytest.raises(SystemExit, match="Unknown time zone"):
        resolve_time_zone("Not/AZone")


def test_resolve_time_zone_accepts_iana() -> None:
    assert resolve_time_zone("UTC") == "UTC"
    assert resolve_time_zone("Europe/London") == "Europe/London"


def test_detect_local_time_zone_is_iana_or_utc() -> None:
    from heardloop.timeutil import available_time_zones

    assert detect_local_time_zone() in available_time_zones()


@pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
def test_create_owner_sets_time_zone() -> None:
    assert URL

    async def run() -> None:
        engine = create_async_engine(URL)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
            email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
            await create_owner(
                email,
                "Owner",
                "Acme",
                PASSWORD,
                time_zone="Europe/London",
            )
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                user = (await db.execute(select(User).where(User.email == email))).scalar_one()
                ws = (await db.execute(select(Workspace).where(Workspace.name == "Acme"))).scalar_one()
                assert user.display_name == "Owner"
                assert ws.time_zone == "Europe/London"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_workspace_model_default_is_utc() -> None:
    assert Workspace.__table__.c.time_zone.default.arg == "UTC"
