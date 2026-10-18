"""Calendar view (FR-16): scheduled and published posts between two dates in the workspace time zone.

Moving a post is done with the normal schedule endpoint (`POST .../posts/{id}/schedule` with `local_time`),
so every rule for scheduling (lead time, token expiry, approval) applies to drag-and-drop too.
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_, select

from heardloop.api.deps import Db, WorkspaceAccess, require_client_header, require_permission
from heardloop.api.schemas import CalendarItem, CalendarOut
from heardloop.auth.permissions import Permission, has_permission
from heardloop.domain.workflow import PostStatus
from heardloop.models import Campaign, LinkedInAccount, Post, PostVersion, Workspace

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/calendar",
    tags=["calendar"],
    dependencies=[Depends(require_client_header)],
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]

MAX_DAYS = 62  # a month view with leading/trailing weeks fits easily
MAX_ITEMS = 1000
SNIPPET = 90
SHOWN = (PostStatus.SCHEDULED, PostStatus.PUBLISHING, PostStatus.PUBLISHED, PostStatus.FAILED)


def _snippet(body: str) -> str:
    line = " ".join(body.split())
    return line if len(line) <= SNIPPET else line[: SNIPPET - 1].rstrip() + "…"


@router.get("")
async def calendar(
    access: ReadAccess,
    db: Db,
    start: Annotated[date, Query(description="first day, inclusive (workspace time zone)")],
    end: Annotated[date, Query(description="last day, inclusive (workspace time zone)")],
) -> CalendarOut:
    if end < start or (end - start).days + 1 > MAX_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_range")
    ws = await db.get(Workspace, access.workspace_id)
    tz = ZoneInfo(ws.time_zone if ws else "UTC")
    lo = datetime.combine(start, time.min, tzinfo=tz)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)

    published = and_(Post.status == PostStatus.PUBLISHED, Post.published_at >= lo, Post.published_at < hi)
    planned = and_(
        Post.status.in_([s for s in SHOWN if s != PostStatus.PUBLISHED]),
        Post.scheduled_at >= lo,
        Post.scheduled_at < hi,
    )
    rows = (
        await db.execute(
            select(Post, PostVersion.body, LinkedInAccount.display_name, Campaign.name, Campaign.color)
            .join(PostVersion, PostVersion.id == Post.current_version_id, isouter=True)
            .join(LinkedInAccount, LinkedInAccount.id == Post.account_id, isouter=True)
            .join(Campaign, Campaign.id == Post.campaign_id, isouter=True)
            .where(Post.workspace_id == access.workspace_id, or_(published, planned))
            .order_by(Post.scheduled_at, Post.published_at)
            .limit(MAX_ITEMS + 1)
        )
    ).all()

    truncated = len(rows) > MAX_ITEMS
    rows = rows[:MAX_ITEMS]
    can_write = has_permission(access.role, Permission.POSTS_WRITE)
    items: list[CalendarItem] = []
    for post, body, account_name, campaign_name, color in rows:
        at = post.published_at if post.status == PostStatus.PUBLISHED else post.scheduled_at
        if at is None:
            continue
        items.append(
            CalendarItem(
                post_id=post.id,
                status=post.status.value,
                at=at,
                local_time=at.astimezone(tz).strftime("%Y-%m-%dT%H:%M"),
                snippet=_snippet(body or ""),
                account_name=account_name or "",
                campaign_id=post.campaign_id,
                campaign_name=campaign_name,
                color=color,
                can_reschedule=can_write and post.status == PostStatus.SCHEDULED,
            )
        )
    items.sort(key=lambda i: i.at)
    return CalendarOut(time_zone=tz.key, start=start, end=end, items=items, truncated=truncated)
