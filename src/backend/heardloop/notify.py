"""Notifications (FR-26): in-app rows, e-mail and the workspace webhook, written as an outbox.

`notify()` runs inside the caller's transaction, so a notification exists only if the action that caused
it was committed. The worker delivers e-mails and webhooks later (see `heardloop/delivery.py`).

Privacy: notification data never contains post text or secrets. The webhook gets the post text and
reviewer comments only when the workspace owner turned that on (decided at send time, see delivery.py).
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from heardloop.config import get_settings
from heardloop.models import (
    Membership,
    Notification,
    Post,
    PostVersion,
    User,
    WebhookDelivery,
    Workspace,
    WorkspaceRole,
)

KINDS = (
    "post.submitted",
    "post.approved",
    "post.rejected",
    "post.published",
    "post.failed",
    "account.expiring",
    "account.reconnect_needed",
    "digest.weekly",
    "backup.stale",
    "backup.failed",
    "app.update_available",
)

# Publish failure codes the UI maps in publishErrors.ts. Keep in sync: a test reads that file.
# Dynamic LinkedIn codes (http_4xx:…) fall back to the raw code in the UI.
KNOWN_CODES: frozenset[str] = frozenset(
    {
        "no_content",
        "account_not_connected",
        "token_expired",
        "token_unreadable",
        "media_missing",
        "video_with_link",
        "unknown_outcome",
        "timeout_unknown_outcome",
        "network_error",
        "network",  # alias used in UI copy
        "token_invalid",
        "not_permitted",
        "api_version_retired",
        "no_post_id",
        "linkedin_not_configured",
        "linkedin_4xx",
        "linkedin_5xx",
        "video_upload_timeout",
        "image_upload_timeout",
        "video_processing",
        "video_processing_failed",
        "rate_limited",
        "abandoned_by_user",
    }
)

# Publish failures that mean the LinkedIn account must be reconnected before retrying.
RECONNECT_CODES: frozenset[str] = frozenset(
    {"account_not_connected", "token_expired", "token_unreadable", "token_invalid"}
)


async def members_with_roles(
    db: AsyncSession, workspace_id: uuid.UUID, roles: Iterable[WorkspaceRole]
) -> list[uuid.UUID]:
    rows = await db.execute(
        select(Membership.user_id)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == workspace_id, Membership.role.in_(list(roles)), User.is_active)
    )
    return [r for (r,) in rows.all()]


async def post_author(db: AsyncSession, post: Post) -> uuid.UUID | None:
    """The person who created the post (author of its first version)."""
    return (
        await db.execute(
            select(PostVersion.author_id)
            .where(PostVersion.post_id == post.id)
            .order_by(PostVersion.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()


def post_link(post_id: uuid.UUID) -> str:
    return f"{get_settings().app_base_url}/?section=posts&post={post_id}"


async def notify(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    recipients: Iterable[uuid.UUID | None],
    now: datetime,
    post: Post | None = None,
    data: dict[str, Any] | None = None,
    actor_id: uuid.UUID | None = None,
) -> None:
    """Adds one notification per recipient (minus the actor) and one webhook delivery if configured."""
    data = data or {}
    email_status = "pending" if get_settings().email_configured else "skipped"
    # Only current members (e.g. the author may have left the workspace since writing the post).
    members = set(
        (
            await db.execute(select(Membership.user_id).where(Membership.workspace_id == workspace_id))
        ).scalars()
    )
    seen: set[uuid.UUID] = set()
    for user_id in recipients:
        if user_id is None or user_id == actor_id or user_id in seen or user_id not in members:
            continue
        seen.add(user_id)
        db.add(
            Notification(
                workspace_id=workspace_id,
                user_id=user_id,
                kind=kind,
                post_id=post.id if post else None,
                data=data,
                created_at=now,
                email_status=email_status,
            )
        )
    await enqueue_webhook(db, workspace_id=workspace_id, event=kind, now=now, post=post, data=data)


async def enqueue_webhook(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    event: str,
    now: datetime,
    post: Post | None = None,
    data: dict[str, Any] | None = None,
) -> bool:
    ws = await db.get(Workspace, workspace_id)
    if ws is None or not ws.webhook_url:
        return False
    payload: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "event": event,
        "occurred_at": now.isoformat(),
        "workspace_id": str(workspace_id),
        "data": data or {},
    }
    if post is not None:
        payload["post"] = {
            "id": str(post.id),
            "status": post.status.value,
            "campaign_id": str(post.campaign_id) if post.campaign_id else None,
            "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
            "published_at": post.published_at.isoformat() if post.published_at else None,
            "linkedin_post_urn": post.linkedin_post_urn,
            "url": post_link(post.id),
        }
        # Post text is added at send time, and only if the owner still allows it then.
    db.add(
        WebhookDelivery(
            workspace_id=workspace_id,
            event=event,
            payload=payload,
            status="pending",
            next_attempt_at=now,
            created_at=now,
        )
    )
    return True


def error_code(last_error: str | None) -> str:
    """Short publish failure code from `last_error` (`code: details` -> `code`).

    Known codes (see `KNOWN_CODES`, mirrored in `src/frontend/src/publishErrors.ts`):
    no_content, account_not_connected, token_expired, token_unreadable, media_missing,
    video_with_link, unknown_outcome, timeout_unknown_outcome, network_error, network,
    token_invalid, not_permitted, api_version_retired, no_post_id, linkedin_not_configured,
    linkedin_4xx, linkedin_5xx, video_upload_timeout, image_upload_timeout, video_processing,
    video_processing_failed, rate_limited, abandoned_by_user.
    LinkedIn may also produce `http_<status>` or `http_<status>:<serviceCode>` (not listed).
    """
    return (last_error or "unknown").split(":", 1)[0][:64]
