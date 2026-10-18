"""In-app notifications for the signed-in user, the e-mail preference, and the workspace webhook (FR-26)."""

import secrets
import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update

from heardloop import audit
from heardloop.api.deps import (
    CurrentPrincipal,
    Db,
    WorkspaceAccess,
    client_ip,
    require_client_header,
    require_permission,
)
from heardloop.api.schemas import Strict
from heardloop.auth.permissions import Permission
from heardloop.config import get_settings
from heardloop.delivery import webhook_secret_context
from heardloop.models import Membership, Notification, WebhookDelivery, Workspace
from heardloop.notify import enqueue_webhook
from heardloop.security.crypto import TokenProtector
from heardloop.timeutil import utcnow

router = APIRouter(tags=["notifications"], dependencies=[Depends(require_client_header)])

ManageSettings = Annotated[WorkspaceAccess, Depends(require_permission(Permission.SETTINGS_MANAGE))]


# ---- schemas (local: only used here) ----


class NotificationOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    post_id: uuid.UUID | None
    data: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    unread: int


class Preferences(Strict):
    email_notifications: bool


class PreferencesOut(BaseModel):
    email_notifications: bool
    email_available: bool  # SMTP configured on the server


class WebhookIn(Strict):
    url: str = Field(min_length=1, max_length=2048)
    include_text: bool = False

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = v.strip()
        u = urlparse(v)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("url must start with http:// or https:// and include a host")
        if u.username or u.password:
            raise ValueError("url must not contain a user name or password; use the signing secret")
        if any(c.isspace() for c in v):
            raise ValueError("url must not contain spaces")
        return v


class WebhookOut(BaseModel):
    configured: bool
    url: str | None
    include_text: bool
    secret: str | None = None  # shown once, right after it is created or rotated


class DeliveryOut(BaseModel):
    id: int
    event: str
    status: str
    attempts: int
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


# ---- my notifications ----


def _mine(principal: CurrentPrincipal):  # type: ignore[no-untyped-def]
    # Only workspaces the user still belongs to.
    return (
        select(Notification)
        .join(
            Membership,
            (Membership.workspace_id == Notification.workspace_id)
            & (Membership.user_id == Notification.user_id),
        )
        .where(Notification.user_id == principal.user.id)
    )


async def _unread(db: Db, principal: CurrentPrincipal) -> int:
    stmt = _mine(principal).where(Notification.read_at.is_(None)).subquery()
    return int((await db.execute(select(func.count()).select_from(stmt))).scalar_one())


@router.get("/api/v1/notifications")
async def list_notifications(
    principal: CurrentPrincipal,
    db: Db,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> NotificationPage:
    stmt = _mine(principal)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = (await db.execute(stmt.order_by(Notification.created_at.desc()).limit(limit))).scalars()
    items = [
        NotificationOut(
            id=n.id,
            workspace_id=n.workspace_id,
            kind=n.kind,
            post_id=n.post_id,
            data=n.data or {},
            created_at=n.created_at,
            read_at=n.read_at,
        )
        for n in rows
    ]
    return NotificationPage(items=items, unread=await _unread(db, principal))


@router.get("/api/v1/notifications/unread-count")
async def unread_count(principal: CurrentPrincipal, db: Db) -> dict[str, int]:
    return {"unread": await _unread(db, principal)}


@router.post("/api/v1/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(notification_id: uuid.UUID, principal: CurrentPrincipal, db: Db) -> None:
    result = await db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.user_id == principal.user.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
    )
    await db.commit()
    if result.rowcount == 0:  # type: ignore[attr-defined]
        exists = (
            await db.execute(
                select(Notification.id).where(
                    Notification.id == notification_id, Notification.user_id == principal.user.id
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "notification_not_found")


@router.post("/api/v1/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(principal: CurrentPrincipal, db: Db) -> None:
    await db.execute(
        update(Notification)
        .where(Notification.user_id == principal.user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    await db.commit()


@router.get("/api/v1/notifications/preferences")
async def get_preferences(principal: CurrentPrincipal) -> PreferencesOut:
    return PreferencesOut(
        email_notifications=principal.user.email_notifications,
        email_available=get_settings().email_configured,
    )


@router.put("/api/v1/notifications/preferences")
async def set_preferences(body: Preferences, principal: CurrentPrincipal, db: Db) -> PreferencesOut:
    user = principal.user
    if user.email_notifications != body.email_notifications:
        user.email_notifications = body.email_notifications
        await db.commit()
    return PreferencesOut(
        email_notifications=user.email_notifications, email_available=get_settings().email_configured
    )


# ---- workspace webhook (owners, with two-step verification) ----


def _protector(request: Request) -> TokenProtector:
    protector: TokenProtector = request.app.state.token_protector
    return protector


async def _ws(db: Db, access: WorkspaceAccess) -> Workspace:
    ws = (
        await db.execute(select(Workspace).where(Workspace.id == access.workspace_id).with_for_update())
    ).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace_not_found")
    return ws


def _webhook_out(ws: Workspace, secret: str | None = None) -> WebhookOut:
    return WebhookOut(
        configured=bool(ws.webhook_url),
        url=ws.webhook_url,
        include_text=ws.webhook_include_text,
        secret=secret,
    )


def _record(db: Db, access: WorkspaceAccess, request: Request, action: str, **changes: Any) -> None:
    audit.record(
        db,
        action=action,
        entity="workspace",
        entity_id=access.workspace_id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes=changes or None,
        now=utcnow(),
    )


@router.get("/api/v1/workspaces/{workspace_id}/webhook")
async def get_webhook(access: ManageSettings, db: Db) -> WebhookOut:
    return _webhook_out(await _ws(db, access))


@router.put("/api/v1/workspaces/{workspace_id}/webhook")
async def set_webhook(body: WebhookIn, access: ManageSettings, request: Request, db: Db) -> WebhookOut:
    """Creates a signing secret on first save (returned once). Changing the URL keeps the secret."""
    ws = await _ws(db, access)
    secret: str | None = None
    if not ws.webhook_secret_encrypted:
        secret = secrets.token_urlsafe(32)
        ws.webhook_secret_encrypted = _protector(request).protect(secret, webhook_secret_context(ws.id))
    old_host = urlparse(ws.webhook_url).hostname if ws.webhook_url else None
    if old_host and old_host != urlparse(body.url).hostname:
        # Queued events were meant for the old receiver; do not hand them to a new host.
        await db.execute(
            update(WebhookDelivery)
            .where(WebhookDelivery.workspace_id == ws.id, WebhookDelivery.status == "pending")
            .values(status="failed", last_error="webhook_changed")
        )
    ws.webhook_url = body.url
    ws.webhook_include_text = body.include_text
    # The URL can carry a token in its query string: log only the host.
    _record(
        db,
        access,
        request,
        "workspace.webhook_set",
        host=urlparse(body.url).hostname,
        include_text=body.include_text,
    )
    await db.commit()
    return _webhook_out(ws, secret)


@router.post("/api/v1/workspaces/{workspace_id}/webhook/rotate-secret")
async def rotate_secret(access: ManageSettings, request: Request, db: Db) -> WebhookOut:
    ws = await _ws(db, access)
    if not ws.webhook_url:
        raise HTTPException(status.HTTP_409_CONFLICT, "webhook_not_configured")
    secret = secrets.token_urlsafe(32)
    ws.webhook_secret_encrypted = _protector(request).protect(secret, webhook_secret_context(ws.id))
    _record(db, access, request, "workspace.webhook_secret_rotated")
    await db.commit()
    return _webhook_out(ws, secret)


@router.delete("/api/v1/workspaces/{workspace_id}/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(access: ManageSettings, request: Request, db: Db) -> None:
    ws = await _ws(db, access)
    ws.webhook_url = None
    ws.webhook_secret_encrypted = None
    ws.webhook_include_text = False
    await db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.workspace_id == ws.id, WebhookDelivery.status == "pending")
        .values(status="failed", last_error="webhook_removed")
    )
    _record(db, access, request, "workspace.webhook_removed")
    await db.commit()


@router.post("/api/v1/workspaces/{workspace_id}/webhook/test", status_code=status.HTTP_202_ACCEPTED)
async def test_webhook(access: ManageSettings, db: Db) -> dict[str, bool]:
    if not await enqueue_webhook(
        db,
        workspace_id=access.workspace_id,
        event="webhook.test",
        now=utcnow(),
        data={"message": "Hello from Heardloop"},
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "webhook_not_configured")
    await db.commit()
    return {"queued": True}


@router.get("/api/v1/workspaces/{workspace_id}/webhook/deliveries")
async def list_deliveries(access: ManageSettings, db: Db) -> list[DeliveryOut]:
    rows = (
        await db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.workspace_id == access.workspace_id)
            .order_by(WebhookDelivery.id.desc())
            .limit(20)
        )
    ).scalars()
    return [
        DeliveryOut(
            id=d.id,
            event=d.event,
            status=d.status,
            attempts=d.attempts,
            last_status_code=d.last_status_code,
            last_error=d.last_error,
            created_at=d.created_at,
            sent_at=d.sent_at,
        )
        for d in rows
    ]
