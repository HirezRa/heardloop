"""Audit log writer (FR-25). Never put secrets, tokens or full post bodies into `changes`."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop.models import AuditLogEntry


def record(
    db: AsyncSession,
    *,
    action: str,
    entity: str,
    now: datetime,
    entity_id: str | uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    changes: dict[str, Any] | None = None,
) -> None:
    """Adds an entry to the current unit of work; the caller commits."""
    db.add(
        AuditLogEntry(
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            actor_id=actor_id,
            workspace_id=workspace_id,
            ip_address=ip_address,
            diff_json=changes,
            at=now,
        )
    )


async def clear_old_ip_addresses(
    factory: async_sessionmaker[AsyncSession], now: datetime, *, retention_days: int
) -> int:
    """Worker, hourly: entries older than `retention_days` keep everything except the IP address.

    Off when `retention_days` is 0 (AUDIT_IP_RETENTION_DAYS). Sessions store an IP too, but they are deleted
    a day after they expire, so the activity log is the only long-lived place.
    """
    if retention_days <= 0:
        return 0
    async with factory() as db:
        result = await db.execute(
            update(AuditLogEntry)
            .where(
                AuditLogEntry.at < now - timedelta(days=retention_days), AuditLogEntry.ip_address.is_not(None)
            )
            .values(ip_address=None)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
