"""Audit log writer (FR-25). Never put secrets, tokens or full post bodies into `changes`."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
