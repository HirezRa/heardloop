"""Workspace settings (FR-15, FR-27) and the owner-only audit log view (FR-25)."""

import asyncio
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import Text, cast, delete, or_, select

from heardloop import audit
from heardloop.api.deps import Db, WorkspaceAccess, client_ip, require_client_header, require_permission
from heardloop.api.schemas import (
    AuditEntryOut,
    AuditPage,
    AuditPurgeOut,
    AuditPurgeRequest,
    BackupStatusOut,
    WorkspaceSettingsOut,
    WorkspaceSettingsUpdate,
)
from heardloop.auth.permissions import Permission, has_permission
from heardloop.auth.service import register_failure, revoke_other_sessions
from heardloop.backup import age_hours, health, read_manifest
from heardloop.config import get_settings as get_app_settings
from heardloop.models import AuditLogEntry, Membership, User, Workspace
from heardloop.security.passwords import verify_password_async
from heardloop.timeutil import utcnow

router = APIRouter(
    prefix="/api/v1/workspaces", tags=["settings"], dependencies=[Depends(require_client_header)]
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
ManageSettings = Annotated[WorkspaceAccess, Depends(require_permission(Permission.SETTINGS_MANAGE))]
ReadAudit = Annotated[WorkspaceAccess, Depends(require_permission(Permission.AUDIT_READ))]


def _out(ws: Workspace, access: WorkspaceAccess) -> WorkspaceSettingsOut:
    return WorkspaceSettingsOut(
        workspace_id=ws.id,
        name=ws.name,
        time_zone=ws.time_zone,
        require_approval=ws.require_approval,
        weekly_digest=ws.weekly_digest,
        can_edit=has_permission(access.role, Permission.SETTINGS_MANAGE),
    )


async def _workspace(db: Db, access: WorkspaceAccess, *, lock: bool = False) -> Workspace:
    stmt = select(Workspace).where(Workspace.id == access.workspace_id)
    if lock:
        stmt = stmt.with_for_update()
    ws = (await db.execute(stmt)).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace_not_found")
    return ws


@router.get("/{workspace_id}/settings")
async def get_settings(access: ReadAccess, db: Db) -> WorkspaceSettingsOut:
    return _out(await _workspace(db, access), access)


@router.get("/{workspace_id}/settings/backup")
async def get_backup_status(access: ManageSettings) -> BackupStatusOut:
    """Owner only. The newest backup as the backup service recorded it (manifest of at most 64 KB)."""
    read = await asyncio.to_thread(read_manifest, get_app_settings().backup_manifest_path)
    now = utcnow()
    if read.state in ("disabled", "missing"):
        return BackupStatusOut(configured=False)
    if read.manifest is None:
        return BackupStatusOut(configured=True, status="unreadable", error="unreadable_manifest")
    m = read.manifest
    return BackupStatusOut(
        configured=True,
        status=m.status,
        stale=health(read, now) == "stale",
        finished_at=m.finished_at,
        age_hours=age_hours(m, now),
        db_bytes=m.db_bytes,
        media_bytes=m.media_bytes,
        encrypted=m.encrypted,
        error=m.error,
    )


@router.patch("/{workspace_id}/settings")
async def update_settings(
    body: WorkspaceSettingsUpdate, access: ManageSettings, request: Request, db: Db
) -> WorkspaceSettingsOut:
    ws = await _workspace(db, access, lock=True)
    changes: dict[str, Any] = {}
    for field in ("name", "time_zone", "require_approval", "weekly_digest"):
        new = getattr(body, field)
        old = getattr(ws, field)
        if new is not None and new != old:
            changes[field] = {"from": old, "to": new}
            setattr(ws, field, new)
    if changes:
        audit.record(
            db,
            action="workspace.settings_changed",
            entity="workspace",
            entity_id=ws.id,
            actor_id=access.principal.user.id,
            workspace_id=ws.id,
            ip_address=client_ip(request),
            changes=changes,
            now=utcnow(),
        )
        await db.commit()
    return _out(ws, access)


@router.get("/{workspace_id}/audit")
async def list_audit(
    access: ReadAudit,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    category: Annotated[str | None, Query(max_length=32, pattern=r"^[a-z_]+$")] = None,
    actor_id: Annotated[uuid.UUID | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> AuditPage:
    """Newest first. Workspace events, plus sign-in events of this workspace's members.

    Filters: `category` = action prefix (post, campaign, template, auth, member, workspace,
    linkedin, media, owner), `actor_id`, `since`/`until` (ISO 8601 with offset),
    `q` = text in the action name or the details.
    The log is append-only: there is no delete endpoint on purpose.
    """
    members = select(Membership.user_id).where(Membership.workspace_id == access.workspace_id)
    stmt = (
        select(AuditLogEntry, User.display_name)
        .outerjoin(User, User.id == AuditLogEntry.actor_id)
        .where(
            or_(
                AuditLogEntry.workspace_id == access.workspace_id,
                (AuditLogEntry.workspace_id.is_(None)) & AuditLogEntry.actor_id.in_(members),
            )
        )
        .order_by(AuditLogEntry.id.desc())
        .limit(limit + 1)
    )
    if before_id is not None:
        stmt = stmt.where(AuditLogEntry.id < before_id)
    if category:
        stmt = stmt.where(AuditLogEntry.action.like(f"{category}.%"))
    if actor_id is not None:
        stmt = stmt.where(AuditLogEntry.actor_id == actor_id)
    if since is not None:
        stmt = stmt.where(AuditLogEntry.at >= since)
    if until is not None:
        stmt = stmt.where(AuditLogEntry.at <= until)
    if q and q.strip():
        # Literal match: % and _ are not wildcards, backslash is the escape character.
        literal = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = f"%{literal}%"
        stmt = stmt.where(
            or_(
                AuditLogEntry.action.ilike(needle, escape="\\"),
                AuditLogEntry.entity_id.ilike(needle, escape="\\"),
                cast(AuditLogEntry.diff_json, Text).ilike(needle, escape="\\"),
                User.display_name.ilike(needle, escape="\\"),
            )
        )
    rows = (await db.execute(stmt)).all()
    page = rows[:limit]
    items = [
        AuditEntryOut(
            id=e.id,
            at=e.at,
            action=e.action,
            entity=e.entity,
            entity_id=e.entity_id,
            actor_id=e.actor_id,
            actor_name=name,
            ip_address=e.ip_address,
            changes=e.diff_json,
        )
        for e, name in page
    ]
    return AuditPage(items=items, next_before_id=page[-1][0].id if len(rows) > limit else None)


@router.post("/{workspace_id}/audit/purge")
async def purge_audit(body: AuditPurgeRequest, access: ReadAudit, request: Request, db: Db) -> AuditPurgeOut:
    """Deletes this workspace's activity-log entries (all, or those before `before`).

    Owner with two-step verification only, password re-entered. Wrong passwords count toward the same
    lockout as sign-in (423 while locked, even for the right password) and are logged as `audit.purge_denied`.
    The purge itself is written to the log
    with the number of entries removed, so the history is never silently blank. Members' sign-in events
    (stored without a workspace) are included, since they are what this log shows.
    """
    now = utcnow()
    # Row lock: parallel password guesses are serialized, so the lockout counter cannot be raced.
    user = (
        await db.execute(select(User).where(User.id == access.principal.user.id).with_for_update())
    ).scalar_one()
    if user.locked_until is not None and user.locked_until > now:
        raise HTTPException(
            status.HTTP_423_LOCKED, "account_locked"
        )  # the caller is signed in: no need to hide it
    matches, _ = await verify_password_async(user.password_hash, body.password)
    if not matches:
        # Same counter and lockout as the sign-in form: a stolen session cannot guess the password here.
        register_failure(user, now)
        if user.locked_until is not None:
            # Other devices are signed out; this session stays so the owner sees "account locked".
            await revoke_other_sessions(db, user.id, keep=access.principal.session.id)
        audit.record(
            db,
            action="audit.purge_denied",
            entity="workspace",
            entity_id=access.workspace_id,
            actor_id=user.id,
            workspace_id=access.workspace_id,
            ip_address=client_ip(request),
            now=now,
        )
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid_password")
    user.failed_login_count = 0
    user.locked_until = None
    members = select(Membership.user_id).where(Membership.workspace_id == access.workspace_id)
    stmt = delete(AuditLogEntry).where(
        or_(
            AuditLogEntry.workspace_id == access.workspace_id,
            (AuditLogEntry.workspace_id.is_(None)) & AuditLogEntry.actor_id.in_(members),
        )
    )
    if body.before is not None:
        stmt = stmt.where(AuditLogEntry.at < body.before)
    result = await db.execute(stmt.execution_options(synchronize_session=False))
    deleted = int(result.rowcount or 0)  # type: ignore[attr-defined]
    audit.record(
        db,
        action="audit.purged",
        entity="workspace",
        entity_id=access.workspace_id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes={"deleted": deleted, "before": body.before.isoformat() if body.before else None},
        now=utcnow(),
    )
    await db.commit()
    return AuditPurgeOut(deleted=deleted)
