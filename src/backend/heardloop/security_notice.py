"""Notices to a person about changes to their own sign-in security (ASVS V2.2.3).

One in-app notification (and an e-mail when they have e-mail on) per event, written in the same
transaction as the change. They go to the person only: never to the workspace webhook, never to owners.
Notifications belong to a workspace, so the person's first workspace (by name) carries it; someone who is
in no workspace gets no in-app notice (they cannot open the app anyway).
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from heardloop.config import get_settings
from heardloop.models import Membership, Notification, Workspace

PASSWORD_CHANGED = "security.password_changed"  # noqa: S105  # an event name, not a secret
MFA_ENABLED = "security.mfa_enabled"
MFA_DISABLED = "security.mfa_disabled"
RECOVERY_CODES_REGENERATED = "security.recovery_codes_regenerated"
SESSIONS_REVOKED = "security.sessions_revoked"

SECURITY_KINDS = (PASSWORD_CHANGED, MFA_ENABLED, MFA_DISABLED, RECOVERY_CODES_REGENERATED, SESSIONS_REVOKED)


async def notify_security(
    db: AsyncSession, user_id: uuid.UUID, kind: str, now: datetime, *, count: int | None = None
) -> None:
    if kind not in SECURITY_KINDS:
        raise ValueError(kind)
    workspace_id = (
        await db.execute(
            select(Membership.workspace_id)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(Membership.user_id == user_id)
            .order_by(Workspace.name, Workspace.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if workspace_id is None:
        return
    db.add(
        Notification(
            workspace_id=workspace_id,
            user_id=user_id,
            kind=kind,
            data={"count": count} if count is not None else {},
            created_at=now,
            email_status="pending" if get_settings().email_configured else "skipped",
        )
    )
