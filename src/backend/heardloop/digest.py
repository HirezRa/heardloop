"""Weekly digest (FR-24): every Monday morning in the workspace time zone, once per ISO week.

What went out last week, what is scheduled this week, the strongest post and how many published posts still
have no metrics. Delivered through the normal notification path (in-app for everyone, e-mail where enabled).
Notification data carries ids and numbers only, never post text: it also reaches webhooks and mailboxes.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop.domain.workflow import PostStatus
from heardloop.models import Post, PostMetric, Workspace, WorkspaceRole
from heardloop.notify import members_with_roles, notify

log = logging.getLogger("heardloop.digest")

Clock = Callable[[], datetime]
SEND_HOUR = 8  # local time; the worker checks hourly so it goes out between 08:00 and 09:00


async def build_digest(db: AsyncSession, ws: Workspace, now: datetime) -> dict[str, object]:
    tz = ZoneInfo(ws.time_zone)
    local_now = now.astimezone(tz)
    week_start = datetime.combine(local_now.date() - timedelta(days=local_now.weekday()), time.min, tzinfo=tz)
    last_week_start = week_start - timedelta(days=7)

    published = (
        await db.execute(
            select(func.count())
            .select_from(Post)
            .where(
                Post.workspace_id == ws.id,
                Post.status == PostStatus.PUBLISHED,
                Post.published_at >= last_week_start,
                Post.published_at < week_start,
            )
        )
    ).scalar_one()
    scheduled = (
        await db.execute(
            select(func.count())
            .select_from(Post)
            .where(
                Post.workspace_id == ws.id,
                Post.status == PostStatus.SCHEDULED,
                Post.scheduled_at >= week_start,
                Post.scheduled_at < week_start + timedelta(days=7),
            )
        )
    ).scalar_one()
    waiting = (
        await db.execute(
            select(func.count())
            .select_from(Post)
            .where(Post.workspace_id == ws.id, Post.status == PostStatus.PENDING_APPROVAL)
        )
    ).scalar_one()

    # Latest measurement per published post of the last 4 weeks -> strongest post, unmeasured count.
    since = week_start - timedelta(days=28)
    latest = (
        select(PostMetric.post_id, func.max(PostMetric.captured_at).label("captured_at"))
        .group_by(PostMetric.post_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Post.id, PostMetric)
            .join(latest, latest.c.post_id == Post.id, isouter=True)
            .join(
                PostMetric,
                (PostMetric.post_id == Post.id) & (PostMetric.captured_at == latest.c.captured_at),
                isouter=True,
            )
            .where(
                Post.workspace_id == ws.id, Post.status == PostStatus.PUBLISHED, Post.published_at >= since
            )
            .order_by(Post.id, PostMetric.id.desc())
        )
    ).all()
    best: tuple[int, str] | None = None  # engagement, post id
    unmeasured = 0
    seen: set[uuid.UUID] = set()
    for post_id, metric in rows:
        if post_id in seen:  # two measurements with the same timestamp would repeat the row
            continue
        seen.add(post_id)
        if metric is None:
            unmeasured += 1
            continue
        engagement = sum(v for v in (metric.reactions, metric.comments, metric.reposts, metric.saves) if v)
        if best is None or engagement > best[0]:
            best = (engagement, str(post_id))
    return {
        "week": f"{week_start.isocalendar()[0]}-W{week_start.isocalendar()[1]:02d}",
        "published_last_week": int(published),
        "scheduled_this_week": int(scheduled),
        "waiting_approval": int(waiting),
        "best_post": {"engagement": best[0], "post_id": best[1]} if best else None,
        "unmeasured": unmeasured,
    }


async def send_weekly_digests(factory: async_sessionmaker[AsyncSession], clock: Clock) -> int:
    """Called hourly by the worker; sends at most one digest per workspace per ISO week.

    If the worker is down all Monday the week is skipped (no catch-up): a digest on Wednesday is noise.
    """
    now = clock()
    sent = 0
    async with factory() as db:
        ids = list(
            (await db.execute(select(Workspace.id).where(Workspace.weekly_digest.is_(True)))).scalars()
        )
    for ws_id in ids:
        try:  # one workspace's failure must not block the others
            async with factory() as db:
                ws = await db.get(Workspace, ws_id)
                if ws is None:
                    continue
                try:
                    tz = ZoneInfo(ws.time_zone)
                except (KeyError, ValueError):
                    continue
                local = now.astimezone(tz)
                if local.weekday() != 0 or local.hour < SEND_HOUR:
                    continue
                week = f"{local.isocalendar()[0]}-W{local.isocalendar()[1]:02d}"
                if ws.last_digest_week == week:
                    continue
                data = await build_digest(db, ws, now)
                quiet = not any(
                    (
                        data["published_last_week"],
                        data["scheduled_this_week"],
                        data["unmeasured"],
                        data["waiting_approval"],
                    )
                )
                if quiet:
                    ws.last_digest_week = week  # nothing to say this week
                    await db.commit()
                    continue
                members = await members_with_roles(db, ws.id, list(WorkspaceRole))  # active members only
                await notify(
                    db, workspace_id=ws.id, kind="digest.weekly", recipients=members, now=now, data=data
                )
                ws.last_digest_week = week
                await db.commit()
                sent += 1
                log.info("Weekly digest queued", extra={"workspace_id": str(ws.id), "week": week})
        except Exception:
            log.exception("Weekly digest failed", extra={"workspace_id": str(ws_id)})
    return sent
