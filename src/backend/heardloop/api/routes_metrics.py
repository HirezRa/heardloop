"""Post metrics (FR-21, FR-22) and reports (FR-23).

LinkedIn does not grant this app read access to member post analytics, so numbers are entered by hand from
the LinkedIn post page, or read from the .xlsx export LinkedIn offers on each post (`/metrics/import`).
Each entry is a dated measurement; the latest one per post feeds the reports.
If LinkedIn access is ever granted, a fetcher only has to insert rows with `source=api`.
"""

import csv
import dataclasses
import io
import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from heardloop import analytics_import, audit
from heardloop.api.deps import Db, WorkspaceAccess, client_ip, require_client_header, require_permission
from heardloop.api.schemas import (
    METRIC_FIELDS,
    POST_MAX_CHARS,
    DemographicOut,
    ImportResultOut,
    MetricsIn,
    MetricsOut,
    ReportBucket,
    ReportOut,
    ReportPost,
    TopFieldsOut,
)
from heardloop.auth.permissions import Permission
from heardloop.domain.workflow import PostStatus
from heardloop.formdata import parse_multipart
from heardloop.models import (
    Campaign,
    LinkedInAccount,
    MediaAsset,
    MetricSource,
    Post,
    PostMetric,
    PostVersion,
    User,
    Workspace,
)
from heardloop.timeutil import utcnow

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}",
    tags=["metrics"],
    dependencies=[Depends(require_client_header)],
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
WriteAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.POSTS_WRITE))]

MAX_REPORT_DAYS = 366
MAX_METRICS_PER_POST = 200
SNIPPET = 80


async def _published_post(db: Db, access: WorkspaceAccess, post_id: uuid.UUID) -> Post:
    post = await db.get(Post, post_id)
    if post is None or post.workspace_id != access.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post_not_found")
    if post.status != PostStatus.PUBLISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, "post_not_published")
    return post


def _out(m: PostMetric, name: str | None) -> MetricsOut:
    return MetricsOut(
        id=m.id,
        captured_at=m.captured_at,
        impressions=m.impressions,
        reactions=m.reactions,
        comments=m.comments,
        reposts=m.reposts,
        clicks=m.clicks,
        saves=m.saves,
        followers=m.followers,
        members_reached=m.members_reached,
        profile_viewers=m.profile_viewers,
        followers_gained=m.followers_gained,
        sends=m.sends,
        demographics=[DemographicOut(**d) for d in m.demographics] if m.demographics else None,
        top=TopFieldsOut(**m.top) if m.top else None,
        note=m.note,
        source=m.source.value,
        recorded_by_name=name,
    )


@router.get("/posts/{post_id}/metrics")
async def list_metrics(post_id: uuid.UUID, access: ReadAccess, db: Db) -> list[MetricsOut]:
    post = await db.get(Post, post_id)
    if post is None or post.workspace_id != access.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post_not_found")
    rows = await db.execute(
        select(PostMetric, User.display_name)
        .outerjoin(User, User.id == PostMetric.recorded_by)
        .where(PostMetric.post_id == post.id)
        .order_by(PostMetric.captured_at.desc())
    )
    return [_out(m, name) for m, name in rows.all()]


@router.post("/posts/{post_id}/metrics", status_code=status.HTTP_201_CREATED)
async def add_metrics(
    post_id: uuid.UUID, body: MetricsIn, access: WriteAccess, request: Request, db: Db
) -> MetricsOut:
    post = await _published_post(db, access, post_id)
    values = {f: getattr(body, f) for f in METRIC_FIELDS}
    if all(v is None for v in values.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_values")
    now = utcnow()
    captured = body.captured_at or now
    if captured > now + timedelta(minutes=5):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "captured_in_future")
    if post.published_at and captured < post.published_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "captured_before_publish")
    count = (
        await db.execute(select(func.count()).select_from(PostMetric).where(PostMetric.post_id == post.id))
    ).scalar_one()
    if count >= MAX_METRICS_PER_POST:
        raise HTTPException(status.HTTP_409_CONFLICT, "too_many_measurements")
    metric = PostMetric(
        post_id=post.id,
        captured_at=captured,
        source=MetricSource.MANUAL,
        recorded_by=access.principal.user.id,
        note=" ".join(body.note.split()) if body.note else None,
        **values,
    )
    db.add(metric)
    await db.flush()
    audit.record(
        db,
        action="post.metrics_added",
        entity="post",
        entity_id=post.id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes={k: v for k, v in values.items() if v is not None},
        now=now,
    )
    await db.commit()
    return _out(metric, access.principal.user.display_name)


@router.delete("/posts/{post_id}/metrics/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metrics(
    post_id: uuid.UUID, metric_id: uuid.UUID, access: WriteAccess, request: Request, db: Db
) -> None:
    post = await db.get(Post, post_id)
    if post is None or post.workspace_id != access.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post_not_found")
    metric = await db.get(PostMetric, metric_id)
    if metric is None or metric.post_id != post.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "metrics_not_found")
    audit.record(
        db,
        action="post.metrics_removed",
        entity="post",
        entity_id=post.id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes={"captured_at": metric.captured_at.isoformat()},
        now=utcnow(),
    )
    await db.delete(metric)
    await db.commit()


# ---- import of LinkedIn "Post analytics" exports ----

IMPORT_FORM_FIELDS = {"file", "captured_at", "create_missing", "account_id", "text"}
IMPORT_BODY_CAP = analytics_import.MAX_FILE_BYTES + 64 * 1024  # file plus the small form fields
IMPORT_NOTE = "Imported from LinkedIn export"
IMPORT_PARSE_STATUS = {
    "not_xlsx": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "unrecognised_layout": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "no_share_id": status.HTTP_422_UNPROCESSABLE_CONTENT,
}
_CLOCK_SKEW = timedelta(minutes=5)


async def _read_capped(request: Request) -> bytes:
    """The request body, read in chunks; aborts as soon as it is larger than an export can be."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > IMPORT_BODY_CAP:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > IMPORT_BODY_CAP:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large")
    return bytes(body)


def _form_text(raw: bytes | None, code: str) -> str | None:
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, code) from None


def _form_flag(raw: bytes | None) -> bool:
    value = (_form_text(raw, "invalid_create_missing") or "").strip().lower()
    if value in ("", "false", "0"):
        return False
    if value in ("true", "1"):
        return True
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_create_missing")


def _form_datetime(raw: bytes | None) -> datetime | None:
    value = (_form_text(raw, "invalid_captured_at") or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_captured_at") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_captured_at")
    return parsed.astimezone(UTC)


def _form_post_text(raw: bytes | None) -> str | None:
    value = (_form_text(raw, "invalid_text") or "").replace("\r\n", "\n").strip()
    if not value:
        return None
    if len(value) > POST_MAX_CHARS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "text_too_long")
    if any((ord(ch) < 32 and ch != "\n") or 0x7F <= ord(ch) <= 0x9F for ch in value):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_text")
    return value


def _form_uuid(raw: bytes | None) -> uuid.UUID | None:
    value = (_form_text(raw, "invalid_account_id") or "").strip()
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_account_id") from None


def _localised(naive: datetime | None, tz: ZoneInfo) -> datetime | None:
    """The export's publish time in UTC. Layout B gives a wall-clock time in the workspace's zone."""
    if naive is None:
        return None
    return (naive.replace(tzinfo=tz) if naive.tzinfo is None else naive).astimezone(UTC)


async def _post_by_urn(db: Db, workspace_id: uuid.UUID, urns: list[str]) -> Post | None:
    return (
        (
            await db.execute(
                select(Post)
                .where(Post.workspace_id == workspace_id, Post.linkedin_post_urn.in_(urns))
                .order_by(Post.created_at)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


@router.post("/metrics/import", status_code=status.HTTP_201_CREATED)
async def import_metrics(access: WriteAccess, request: Request, db: Db) -> ImportResultOut:
    """Reads one LinkedIn "Post analytics" .xlsx (multipart `file`) and stores it as a measurement.

    The file itself is never kept; only the numbers and aggregated audience fields are stored. A post that
    Heardloop did not publish can be created on the fly (`create_missing` with an `account_id`).
    """
    raw_body = await _read_capped(request)
    try:
        fields = parse_multipart(request.headers.get("content-type", ""), raw_body)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_form") from None
    if not set(fields) <= IMPORT_FORM_FIELDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_form")
    file_bytes = fields.get("file")
    if not file_bytes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "file_required")
    captured_at = _form_datetime(fields.get("captured_at"))
    create_missing = _form_flag(fields.get("create_missing"))
    account_id = _form_uuid(fields.get("account_id"))
    text = _form_post_text(fields.get("text"))
    if create_missing and account_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "account_required")

    try:
        parsed = await run_in_threadpool(analytics_import.parse_export, file_bytes)
    except analytics_import.ExportParseError as exc:
        raise HTTPException(IMPORT_PARSE_STATUS.get(exc.code, 422), exc.code) from None

    actor = access.principal.user
    ws = await db.get(Workspace, access.workspace_id)
    tz = ZoneInfo(ws.time_zone if ws else "UTC")
    now = utcnow()
    hint = _localised(parsed.published_at_hint, tz)

    if captured_at is not None:
        if captured_at > now + _CLOCK_SKEW:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "captured_in_future")
        captured = captured_at
    elif parsed.captured_on is not None:
        captured = min(datetime.combine(parsed.captured_on, time(12, 0), tzinfo=tz).astimezone(UTC), now)
    else:
        captured = now

    urns = [f"urn:li:share:{parsed.share_id}", f"urn:li:ugcPost:{parsed.share_id}"]
    post = await _post_by_urn(db, access.workspace_id, urns)
    created = False
    if post is None:
        if not create_missing:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                {
                    "code": "post_not_found",
                    "share_id": parsed.share_id,
                    "post_url": parsed.post_url,
                    "published_at_hint": hint.isoformat() if hint else None,
                },
            )
        assert account_id is not None
        account = await db.get(LinkedInAccount, account_id)
        if account is None or account.workspace_id != access.workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "account_not_found")
        published_at = min(hint or captured, now)
        # Created as published in one step: the post was published on LinkedIn long before Heardloop saw it,
        # so no workflow transition applies (it never was a draft here). Same exception as the worker's claim.
        new_post = Post(
            workspace_id=access.workspace_id,
            account_id=account.id,
            status=PostStatus.PUBLISHED,
            published_at=published_at,
            linkedin_post_urn=urns[0],
            external_source=MetricSource.LINKEDIN_EXPORT.value,
            created_at=now,
            updated_at=now,
        )
        try:
            # A savepoint: if a concurrent upload of the same post wins the unique index on
            # (workspace, URN), only this block is rolled back and the request carries on with that post.
            async with db.begin_nested():
                db.add(new_post)
                await db.flush()
                version = PostVersion(
                    post_id=new_post.id,
                    body=text or "",  # empty until the owner pastes the original text
                    media_ids=[],
                    author_id=actor.id,
                    created_at=now,
                )
                db.add(version)
                await db.flush()
                new_post.current_version_id = version.id
            post = new_post
            created = True
        except IntegrityError:
            raced = await _post_by_urn(db, access.workspace_id, urns)
            if raced is None:
                raise
            post = raced

    if post.published_at and captured < post.published_at:
        if captured_at is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "captured_before_publish")
        captured = post.published_at  # a derived time (noon of the export day) can precede an afternoon post

    if not created:
        existing = (
            await db.execute(
                select(PostMetric.captured_at).where(
                    PostMetric.post_id == post.id, PostMetric.source == MetricSource.LINKEDIN_EXPORT
                )
            )
        ).scalars()
        if any(c.astimezone(tz).date() == captured.astimezone(tz).date() for c in existing):
            raise HTTPException(status.HTTP_409_CONFLICT, "already_imported")
        count = (
            await db.execute(
                select(func.count()).select_from(PostMetric).where(PostMetric.post_id == post.id)
            )
        ).scalar_one()
        if count >= MAX_METRICS_PER_POST:
            raise HTTPException(status.HTTP_409_CONFLICT, "too_many_measurements")

    values: dict[str, Any] = {
        "impressions": parsed.metrics["impressions"],
        "reactions": parsed.metrics["reactions"],
        "comments": parsed.metrics["comments"],
        "reposts": parsed.metrics["reposts"],
        "saves": parsed.metrics["saves"],
        "clicks": parsed.metrics["link_clicks"],
        "members_reached": parsed.metrics["members_reached"],
        "profile_viewers": parsed.metrics["profile_viewers"],
        "followers_gained": parsed.metrics["followers_gained"],
        "sends": parsed.metrics["sends"],
    }
    numbers = {k: v for k, v in values.items() if v is not None}
    if not numbers:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no_values")
    metric = PostMetric(
        post_id=post.id,
        captured_at=captured,
        source=MetricSource.LINKEDIN_EXPORT,
        recorded_by=actor.id,
        note=IMPORT_NOTE,
        demographics=[dataclasses.asdict(d) for d in parsed.demographics] or None,
        top=dict(parsed.top) or None,
        **values,
    )
    db.add(metric)
    await db.flush()
    ip = client_ip(request)
    if created:
        audit.record(
            db,
            action="post.imported",
            entity="post",
            entity_id=post.id,
            actor_id=actor.id,
            workspace_id=access.workspace_id,
            ip_address=ip,
            changes={
                "share_id": parsed.share_id,
                "account_id": str(post.account_id),
                "layout": parsed.layout,
            },
            now=now,
        )
    audit.record(
        db,
        action="metrics.imported",
        entity="post",
        entity_id=post.id,
        actor_id=actor.id,
        workspace_id=access.workspace_id,
        ip_address=ip,
        changes={
            **numbers,
            "captured_at": captured.isoformat(),
            "layout": parsed.layout,
            "audience_rows": len(parsed.demographics),
        },
        now=now,
    )
    await db.commit()
    return ImportResultOut(**_out(metric, actor.display_name).model_dump(), post_id=post.id, created=created)


# ---- reports ----


def _snippet(body: str | None) -> str:
    line = " ".join((body or "").split())
    return line if len(line) <= SNIPPET else line[: SNIPPET - 1].rstrip() + "…"


def _engagement(m: PostMetric | None) -> int:
    if m is None:
        return 0
    return sum(v for v in (m.reactions, m.comments, m.reposts, m.saves) if v)


def _rate(engagement: int, impressions: int | None) -> float | None:
    return round(engagement / impressions, 4) if impressions else None


def _bucket(key: str, posts: Iterable[ReportPost], label: str | None = None) -> ReportBucket:
    items = list(posts)
    measured = [p for p in items if p.measured_at is not None]
    impressions = sum(p.impressions or 0 for p in measured)
    engagement = sum(p.engagement for p in measured)
    members_reached = sum(p.members_reached or 0 for p in measured)
    followers_gained = sum(p.followers_gained or 0 for p in measured)
    with_imp = [p for p in measured if p.impressions]
    rates = [p.engagement_rate for p in with_imp if p.engagement_rate is not None]
    return ReportBucket(
        key=key,
        label=label,
        posts=len(items),
        measured=len(measured),
        impressions=impressions,
        engagement=engagement,
        members_reached=members_reached,
        followers_gained=followers_gained,
        avg_impressions=round(impressions / len(with_imp), 1) if with_imp else None,
        avg_engagement_rate=round(sum(rates) / len(rates), 4) if rates else None,
    )


async def _report_posts(db: Db, workspace_id: uuid.UUID, lo: datetime, hi: datetime) -> list[ReportPost]:
    """Published posts in [lo, hi) with their latest measurement."""
    latest = (
        select(PostMetric.post_id, func.max(PostMetric.captured_at).label("captured_at"))
        .group_by(PostMetric.post_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                Post, PostVersion.body, PostVersion.media_ids, PostVersion.link_url, Campaign.name, PostMetric
            )
            .join(PostVersion, PostVersion.id == Post.current_version_id, isouter=True)
            .join(Campaign, Campaign.id == Post.campaign_id, isouter=True)
            .join(latest, latest.c.post_id == Post.id, isouter=True)
            .join(
                PostMetric,
                (PostMetric.post_id == Post.id) & (PostMetric.captured_at == latest.c.captured_at),
                isouter=True,
            )
            .where(
                Post.workspace_id == workspace_id,
                Post.status == PostStatus.PUBLISHED,
                Post.published_at >= lo,
                Post.published_at < hi,
            )
            .order_by(Post.published_at, PostMetric.id.desc())
        )
    ).all()
    media_ids = {mid for _, _, mids, _, _, _ in rows for mid in (mids or [])}
    mimes: dict[uuid.UUID, str] = {}
    if media_ids:
        mimes = dict(
            (
                await db.execute(
                    select(MediaAsset.id, MediaAsset.mime_type).where(MediaAsset.id.in_(media_ids))
                )
            ).all()
        )
    out: list[ReportPost] = []
    seen: set[uuid.UUID] = set()
    for post, body, mids, link_url, campaign_name, metric in rows:
        if post.published_at is None or post.id in seen:
            continue  # two measurements with the same timestamp would otherwise repeat the post
        seen.add(post.id)
        kind = "none"
        if mids:
            kind = "video" if mimes.get(mids[0], "").startswith("video/") else "image"
        elif link_url:
            kind = "link"
        engagement = _engagement(metric)
        out.append(
            ReportPost(
                post_id=post.id,
                published_at=post.published_at,
                snippet=_snippet(body),
                campaign_id=post.campaign_id,
                campaign_name=campaign_name,
                media_kind=kind,
                impressions=metric.impressions if metric else None,
                reactions=metric.reactions if metric else None,
                comments=metric.comments if metric else None,
                reposts=metric.reposts if metric else None,
                clicks=metric.clicks if metric else None,
                saves=metric.saves if metric else None,
                members_reached=metric.members_reached if metric else None,
                followers_gained=metric.followers_gained if metric else None,
                engagement=engagement,
                engagement_rate=_rate(engagement, metric.impressions) if metric else None,
                measured_at=metric.captured_at if metric else None,
            )
        )
    return out


def _range(start: date | None, end: date | None, tz: ZoneInfo) -> tuple[date, date, datetime, datetime]:
    today = datetime.now(tz).date()
    end = end or today
    start = start or (end - timedelta(days=89))
    if end < start or (end - start).days + 1 > MAX_REPORT_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_range")
    lo = datetime.combine(start, time.min, tzinfo=tz)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
    return start, end, lo, hi


@router.get("/reports")
async def report(
    access: ReadAccess,
    db: Db,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    campaign_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ReportOut:
    """Published posts in the range (default: last 90 days), latest metrics, grouped in several ways."""
    ws = await db.get(Workspace, access.workspace_id)
    tz = ZoneInfo(ws.time_zone if ws else "UTC")
    start, end, lo, hi = _range(start, end, tz)
    posts = await _report_posts(db, access.workspace_id, lo, hi)
    if campaign_id is not None:
        posts = [p for p in posts if p.campaign_id == campaign_id]

    by_week: dict[str, list[ReportPost]] = defaultdict(list)
    by_campaign: dict[str, list[ReportPost]] = defaultdict(list)
    campaign_labels: dict[str, str | None] = {}
    by_weekday: dict[str, list[ReportPost]] = defaultdict(list)
    by_hour: dict[str, list[ReportPost]] = defaultdict(list)
    by_media: dict[str, list[ReportPost]] = defaultdict(list)
    for p in posts:
        local = p.published_at.astimezone(tz)
        iso_year, iso_week, _ = local.isocalendar()
        by_week[f"{iso_year}-W{iso_week:02d}"].append(p)
        ckey = str(p.campaign_id) if p.campaign_id else "none"
        by_campaign[ckey].append(p)
        campaign_labels[ckey] = p.campaign_name
        by_weekday[str(local.weekday())].append(p)
        by_hour[str(local.hour)].append(p)
        by_media[p.media_kind].append(p)

    measured = [p for p in posts if p.measured_at is not None]
    top = sorted(measured, key=lambda p: (p.engagement, p.impressions or 0), reverse=True)[:10]
    unmeasured = [p for p in posts if p.measured_at is None][-20:]
    return ReportOut(
        start=start,
        end=end,
        time_zone=tz.key,
        totals=_bucket("total", posts),
        trend=[_bucket(k, by_week[k]) for k in sorted(by_week)],
        by_campaign=sorted(
            (_bucket(k, v, campaign_labels.get(k)) for k, v in by_campaign.items()),
            key=lambda b: (b.engagement, b.impressions),
            reverse=True,
        ),
        by_weekday=[_bucket(str(d), by_weekday.get(str(d), [])) for d in range(7)],
        by_hour=[_bucket(str(h), by_hour.get(str(h), [])) for h in range(24)],
        by_media=[_bucket(k, by_media[k]) for k in ("none", "image", "video", "link") if k in by_media],
        top_posts=top,
        unmeasured=unmeasured,
    )


@router.get("/reports/export.csv")
async def export_csv(
    access: ReadAccess,
    db: Db,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> StreamingResponse:
    """One row per published post with its latest measurement. Values are plain; formulas cannot sneak in."""
    ws = await db.get(Workspace, access.workspace_id)
    tz = ZoneInfo(ws.time_zone if ws else "UTC")
    start, end, lo, hi = _range(start, end, tz)
    posts = await _report_posts(db, access.workspace_id, lo, hi)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "published_at",
            "campaign",
            "media",
            "impressions",
            "reactions",
            "comments",
            "reposts",
            "clicks",
            "saves",
            "members_reached",
            "followers_gained",
            "engagement",
            "engagement_rate",
            "measured_at",
            "text",
            "post_id",
        ]
    )
    for p in posts:
        writer.writerow(
            [
                p.published_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                _csv_safe(p.campaign_name or ""),
                p.media_kind,
                p.impressions if p.impressions is not None else "",
                p.reactions if p.reactions is not None else "",
                p.comments if p.comments is not None else "",
                p.reposts if p.reposts is not None else "",
                p.clicks if p.clicks is not None else "",
                p.saves if p.saves is not None else "",
                p.members_reached if p.members_reached is not None else "",
                p.followers_gained if p.followers_gained is not None else "",
                p.engagement if p.measured_at else "",
                p.engagement_rate if p.engagement_rate is not None else "",
                p.measured_at.astimezone(tz).strftime("%Y-%m-%d %H:%M") if p.measured_at else "",
                _csv_safe(p.snippet),
                str(p.post_id),
            ]
        )
    data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so spreadsheets open Hebrew correctly
    name = f"heardloop-posts-{start.isoformat()}-{end.isoformat()}.csv"
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR at the start of a cell)."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
