"""Posts: drafts, versions, approval, scheduling and publish-now (FR-08, FR-10, FR-14, FR-17, FR-18)."""

import asyncio
import re
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from heardloop import audit
from heardloop.ai_assist import AssistError, assist
from heardloop.api.deps import Db, WorkspaceAccess, client_ip, require_client_header, require_permission
from heardloop.api.post_approval import version_approved
from heardloop.api.routes_media import media_url
from heardloop.api.schemas import (
    AssistOut,
    AssistRequest,
    HashtagIn,
    HashtagsOut,
    LinkCard,
    LinkPreviewOut,
    LinkPreviewRequest,
    MediaRef,
    PostCreate,
    PostOut,
    PostUpdate,
    PostVersionOut,
    RejectRequest,
    RestoreOut,
    ScheduleRequest,
)
from heardloop.auth.permissions import Permission
from heardloop.config import get_settings
from heardloop.domain.workflow import InvalidTransitionError, PostStatus
from heardloop.linkpreview import LinkPreviewError, fetch_preview
from heardloop.models import (
    Approval,
    ApprovalDecision,
    Campaign,
    CampaignStatus,
    Hashtag,
    LinkedInAccount,
    LinkedInAccountStatus,
    MediaAsset,
    Post,
    PostVersion,
    PublishAttempt,
    User,
    Workspace,
    WorkspaceRole,
)
from heardloop.notify import members_with_roles, notify, post_author
from heardloop.timeutil import utcnow

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/posts",
    tags=["posts"],
    dependencies=[Depends(require_client_header)],
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
WriteAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.POSTS_WRITE))]
ApproveAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.POSTS_APPROVE))]

# Same rule as the editor: '#' not glued to a word/another '#', tag starts with a letter or digit, may
# contain Hebrew points (U+0591-05C7) and underscores.
_HASHTAG = re.compile(r"(?<![\w#])#([^\W_][\w\u0591-\u05C7\u0300-\u036F]*)", re.UNICODE)
MIN_LEAD = timedelta(seconds=60)
MAX_AHEAD = timedelta(days=365)
EDITABLE = {
    PostStatus.DRAFT,
    PostStatus.PENDING_APPROVAL,
    PostStatus.APPROVED,
    PostStatus.SCHEDULED,
    PostStatus.FAILED,
}
# Restore is stricter than edit: a scheduled post must be unscheduled (or edited) first.
RESTORE_EDITABLE = {
    PostStatus.DRAFT,
    PostStatus.PENDING_APPROVAL,
    PostStatus.APPROVED,
    PostStatus.FAILED,
}
VERSION_LIST_CAP = 200  # older versions stay in the database; only the newest N are listed


def _link_of(version: PostVersion | None) -> LinkCard | None:
    if version is None or not version.link_url or not version.link_title:
        return None
    return LinkCard(url=version.link_url, title=version.link_title, description=version.link_description)


def _linkedin_url(urn: str | None) -> str | None:
    return f"https://www.linkedin.com/feed/update/{urn}/" if urn else None


async def _out(db: Db, post: Post) -> PostOut:
    version = await db.get(PostVersion, post.current_version_id) if post.current_version_id else None
    mimes: dict[uuid.UUID, str] = {}
    if version and version.media_ids:
        rows = await db.execute(
            select(MediaAsset.id, MediaAsset.mime_type).where(MediaAsset.id.in_(list(version.media_ids)))
        )
        mimes = dict(rows.all())
    account = await db.get(LinkedInAccount, post.account_id)
    count = (
        await db.execute(select(func.count()).select_from(PostVersion).where(PostVersion.post_id == post.id))
    ).scalar_one()
    review = (
        await db.execute(
            select(Approval.comment)
            .where(Approval.post_id == post.id, Approval.decision == ApprovalDecision.REJECTED)
            .order_by(Approval.decided_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return PostOut(
        id=post.id,
        status=post.status.value,
        account_id=post.account_id,
        account_name=account.display_name if account else "",
        account_needs_reconnect_reason=account.needs_reconnect_reason if account else None,
        campaign_id=post.campaign_id,
        body=version.body if version else "",
        media=[
            MediaRef(id=m, url=media_url(post.workspace_id, m), mime_type=mimes.get(m))
            for m in (version.media_ids if version else [])
        ],
        link=_link_of(version),
        version_count=count,
        scheduled_at=post.scheduled_at,
        published_at=post.published_at,
        linkedin_post_urn=post.linkedin_post_urn,
        linkedin_url=_linkedin_url(post.linkedin_post_urn),
        external_source=post.external_source,
        last_error=post.last_error,
        last_review_comment=review if post.status == PostStatus.DRAFT else None,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


async def _require_approval(db: Db, workspace_id: uuid.UUID) -> bool:
    ws = await db.get(Workspace, workspace_id)
    return bool(ws and ws.require_approval)


async def _account(db: Db, workspace_id: uuid.UUID, account_id: uuid.UUID) -> LinkedInAccount:
    acc = await db.get(LinkedInAccount, account_id)
    if acc is None or acc.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account_not_found")
    return acc


async def _campaign(db: Db, workspace_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
    """For assigning a post. KEY SHARE lock: a concurrent campaign delete waits for us instead of
    making our insert/update fail on the foreign key."""
    campaign = (
        await db.execute(
            select(Campaign)
            .where(Campaign.id == campaign_id, Campaign.workspace_id == workspace_id)
            .with_for_update(key_share=True)
        )
    ).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign_not_found")
    if campaign.status == CampaignStatus.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "campaign_archived")
    return campaign


def _resolve_local(local: datetime, tz: ZoneInfo) -> datetime:
    """Wall-clock time in `tz` -> aware datetime. Rejects times skipped or repeated by a clock change."""
    first = local.replace(tzinfo=tz, fold=0)
    second = local.replace(tzinfo=tz, fold=1)
    # A skipped time does not survive a round trip through UTC; check that before ambiguity,
    # because fold=0/1 also differ inside a gap.
    if first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != local:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "nonexistent_local_time")
    if first.utcoffset() != second.utcoffset():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ambiguous_local_time")
    return first


async def _check_media(
    db: Db, workspace_id: uuid.UUID, media_ids: list[uuid.UUID], link: LinkCard | None = None
) -> list[uuid.UUID]:
    for media_id in media_ids:
        asset = await db.get(MediaAsset, media_id)
        if asset is None or asset.workspace_id != workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found")
        if link is not None and asset.mime_type.startswith("video/"):
            # LinkedIn: a post is a video OR an article (link card), never both.
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "video_with_link")
    return list(media_ids)


async def _locked_post(db: Db, access: WorkspaceAccess, post_id: uuid.UUID) -> Post:
    post = (
        await db.execute(
            select(Post).where(Post.id == post_id, Post.workspace_id == access.workspace_id).with_for_update()
        )
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post_not_found")
    return post


def _move(post: Post, to: PostStatus, *, require_approval: bool, now: datetime) -> None:
    try:
        post.transition_to(to, require_approval=require_approval, now=now)
    except InvalidTransitionError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"invalid_transition:{post.status.value}->{to.value}"
        ) from None


def _record(
    db: Db, access: WorkspaceAccess, request: Request, action: str, post: Post, **changes: object
) -> None:
    audit.record(
        db,
        action=action,
        entity="post",
        entity_id=post.id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes=changes or None,
        now=utcnow(),
    )


@router.get("")
async def list_posts(
    access: ReadAccess,
    db: Db,
    status_filter: Annotated[PostStatus | None, Query(alias="status")] = None,
    campaign_id: Annotated[uuid.UUID | None, Query()] = None,
    order: Annotated[Literal["newest", "oldest"], Query()] = "newest",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[PostOut]:
    stmt = select(Post).where(Post.workspace_id == access.workspace_id)
    if status_filter is not None:
        stmt = stmt.where(Post.status == status_filter)
    if campaign_id is not None:
        stmt = stmt.where(Post.campaign_id == campaign_id)
    sort = Post.updated_at.asc() if order == "oldest" else Post.updated_at.desc()
    stmt = stmt.order_by(sort, Post.id).limit(limit)
    return [await _out(db, p) for p in (await db.execute(stmt)).scalars()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_post(body: PostCreate, access: WriteAccess, request: Request, db: Db) -> PostOut:
    await _account(db, access.workspace_id, body.account_id)
    if body.campaign_id is not None:
        await _campaign(db, access.workspace_id, body.campaign_id)
    now = utcnow()
    post = Post(
        workspace_id=access.workspace_id,
        account_id=body.account_id,
        campaign_id=body.campaign_id,
        created_at=now,
        updated_at=now,
    )
    db.add(post)
    await db.flush()
    media_ids = await _check_media(db, access.workspace_id, body.media_ids, body.link)
    version = PostVersion(
        post_id=post.id,
        body=body.body,
        media_ids=media_ids,
        link_url=body.link.url if body.link else None,
        link_title=body.link.title if body.link else None,
        link_description=body.link.description if body.link else None,
        author_id=access.principal.user.id,
        created_at=now,
    )
    db.add(version)
    await db.flush()
    post.current_version_id = version.id
    _record(db, access, request, "post.created", post)
    await db.commit()
    return await _out(db, post)


_PREVIEW_SLOTS = asyncio.Semaphore(4)  # concurrent outbound fetches per API process
_PREVIEW_WINDOW = timedelta(minutes=1)
_PREVIEW_PER_WINDOW = 20
_preview_calls: dict[uuid.UUID, deque[datetime]] = {}


def _preview_allowed(user_id: uuid.UUID, now: datetime) -> bool:
    calls = _preview_calls.setdefault(user_id, deque())
    while calls and calls[0] < now - _PREVIEW_WINDOW:
        calls.popleft()
    if len(calls) >= _PREVIEW_PER_WINDOW:
        return False
    calls.append(now)
    return True


@router.post("/link-preview")
async def link_preview(body: LinkPreviewRequest, access: WriteAccess, request: Request) -> LinkPreviewOut:
    """Fetches a page's title, description and image so the editor can pre-fill a link card.

    Server-side fetch of a user URL: no private or internal hosts, size- and time-limited, rate-limited.
    """
    if not _preview_allowed(access.principal.user.id, utcnow()):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_requests")
    try:
        async with _PREVIEW_SLOTS:
            preview = await fetch_preview(request.app.state.http, body.url)
    except LinkPreviewError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"link_preview_failed:{exc.code}"
        ) from None
    return LinkPreviewOut(
        url=preview.url,
        title=preview.title,
        description=preview.description,
        image_url=preview.image_url,
        site_name=preview.site_name,
    )


_ASSIST_SLOTS = asyncio.Semaphore(2)  # a local model handles few requests at once
_ASSIST_WINDOW = timedelta(minutes=1)
_ASSIST_PER_WINDOW = 10
_assist_calls: dict[uuid.UUID, deque[datetime]] = {}


def _assist_allowed(user_id: uuid.UUID, now: datetime) -> bool:
    calls = _assist_calls.setdefault(user_id, deque())
    while calls and calls[0] < now - _ASSIST_WINDOW:
        calls.popleft()
    if len(calls) >= _ASSIST_PER_WINDOW:
        return False
    calls.append(now)
    return True


@router.post("/assist")
async def ai_assist(body: AssistRequest, access: WriteAccess, request: Request) -> AssistOut:
    """Optional AI writing help on the editor's text (or selection). Nothing is stored or published.

    Off unless the owner set AI_BASE_URL and AI_MODEL (503 `ai_not_configured`). The text goes to that server
    only; the response is a suggestion the editor can take or leave. Rate-limited per user.
    """
    settings = get_settings()
    if not settings.ai_configured:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ai_not_configured")
    if not _assist_allowed(access.principal.user.id, utcnow()):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_requests")
    try:
        async with _ASSIST_SLOTS:
            result = await assist(request.app.state.http, settings, body.action, body.text, body.tone)
    except AssistError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ai_failed:{exc.code}") from None
    return AssistOut(text=result.text, hashtags=result.hashtags)


RECOMMENDED_HASHTAGS = 5  # LinkedIn's own guidance is 3-5; more reads as spam


async def _recent_hashtags(db: Db, workspace_id: uuid.UUID, exclude: set[str]) -> list[str]:
    bodies = (
        await db.execute(
            select(PostVersion.body)
            .join(Post, Post.id == PostVersion.post_id)
            .where(Post.workspace_id == workspace_id)
            .order_by(PostVersion.created_at.desc())
            .limit(300)
        )
    ).scalars()
    counts: dict[str, int] = {}
    spelling: dict[str, str] = {}
    for text in bodies:
        for m in _HASHTAG.finditer(text):
            key = m.group(1).casefold()
            if key in exclude:
                continue
            counts[key] = counts.get(key, 0) + 1
            spelling.setdefault(key, m.group(1))
    ranked = sorted(counts, key=lambda k: (-counts[k], k))
    return [spelling[k] for k in ranked[:30]]


@router.get("/hashtags")
async def hashtags(access: ReadAccess, db: Db) -> HashtagsOut:
    """Saved library first, then hashtags used in recent posts (most used first) for quick picking."""
    saved_rows = list(
        (
            await db.execute(
                select(Hashtag).where(Hashtag.workspace_id == access.workspace_id).order_by(Hashtag.tag_key)
            )
        ).scalars()
    )
    saved = [h.tag for h in saved_rows]
    recent = await _recent_hashtags(db, access.workspace_id, {h.tag_key for h in saved_rows})
    return HashtagsOut(saved=saved, recent=recent, recommended_max=RECOMMENDED_HASHTAGS)


@router.post("/hashtags", status_code=status.HTTP_201_CREATED)
async def save_hashtag(body: HashtagIn, access: WriteAccess, db: Db) -> HashtagsOut:
    key = body.tag.casefold()
    exists = (
        await db.execute(
            select(Hashtag.id).where(Hashtag.workspace_id == access.workspace_id, Hashtag.tag_key == key)
        )
    ).scalar_one_or_none()
    if exists is None:
        count = (
            await db.execute(
                select(func.count()).select_from(Hashtag).where(Hashtag.workspace_id == access.workspace_id)
            )
        ).scalar_one()
        if count >= 200:
            raise HTTPException(status.HTTP_409_CONFLICT, "too_many_hashtags")
        # Insert-or-ignore: two people saving the same tag at once must not become a 500.
        await db.execute(
            pg_insert(Hashtag)
            .values(
                id=uuid.uuid4(),
                workspace_id=access.workspace_id,
                tag=body.tag,
                tag_key=key,
                created_at=utcnow(),
            )
            .on_conflict_do_nothing(constraint="uq_hashtags_workspace_tag")
        )
        await db.commit()
    return await hashtags(access, db)


@router.delete("/hashtags/{tag}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hashtag(tag: str, access: WriteAccess, db: Db) -> None:
    await db.execute(
        delete(Hashtag).where(Hashtag.workspace_id == access.workspace_id, Hashtag.tag_key == tag.casefold())
    )
    await db.commit()


@router.get("/{post_id}")
async def get_post(post_id: uuid.UUID, access: ReadAccess, db: Db) -> PostOut:
    post = await db.get(Post, post_id)
    if post is None or post.workspace_id != access.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post_not_found")
    return await _out(db, post)


@router.patch("/{post_id}")
async def update_post(
    post_id: uuid.UUID, body: PostUpdate, access: WriteAccess, request: Request, db: Db
) -> PostOut:
    post = await _locked_post(db, access, post_id)
    # A published post's text and media are frozen; its campaign (planning metadata) may still change.
    # A post created by an import is already live and was never written here, so its text may be filled in
    # later (it stays published); media, link card and account stay frozen.
    imported_live = post.external_source is not None and post.status == PostStatus.PUBLISHED
    allowed_when_frozen = {"body", "campaign_id"} if imported_live else {"campaign_id"}
    if post.status not in EDITABLE and not body.model_fields_set <= allowed_when_frozen:
        raise HTTPException(status.HTTP_409_CONFLICT, "post_not_editable")
    now = utcnow()
    current = await db.get(PostVersion, post.current_version_id) if post.current_version_id else None
    changed = False
    # The campaign is planning metadata: changing it does not need a new review.
    campaign_changed = "campaign_id" in body.model_fields_set and body.campaign_id != post.campaign_id
    if campaign_changed:
        new_campaign = (
            await _campaign(db, access.workspace_id, body.campaign_id)
            if body.campaign_id is not None
            else None
        )
        old_campaign = (
            await db.get(Campaign, post.campaign_id) if post.campaign_id else None
        )  # may be archived
        post.campaign_id = body.campaign_id
        post.updated_at = now
        # Names make the activity log readable; ids would mean nothing to a person.
        _record(
            db,
            access,
            request,
            "post.campaign_changed",
            post,
            campaign_id={
                "from": old_campaign.name if old_campaign else None,
                "to": new_campaign.name if new_campaign else None,
            },
        )
    if body.account_id is not None and body.account_id != post.account_id:
        await _account(db, access.workspace_id, body.account_id)
        post.account_id = body.account_id
        changed = True
    new_body = body.body if body.body is not None else (current.body if current else "")
    new_link = body.link if "link" in body.model_fields_set else _link_of(current)
    link_changed = new_link != _link_of(current)
    new_media = await _check_media(
        db,
        access.workspace_id,
        body.media_ids if body.media_ids is not None else (list(current.media_ids) if current else []),
        new_link,
    )
    if current is None or new_body != current.body or new_media != list(current.media_ids) or link_changed:
        if not new_body.strip() and post.external_source is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "body_required")
        version = PostVersion(
            post_id=post.id,
            body=new_body,
            media_ids=new_media,
            link_url=new_link.url if new_link else None,
            link_title=new_link.title if new_link else None,
            link_description=new_link.description if new_link else None,
            author_id=access.principal.user.id,
            created_at=now,
        )
        db.add(version)
        await db.flush()
        post.current_version_id = version.id
        changed = True
    if changed and imported_live:
        # No approval and no status change: the post is already on LinkedIn. The text is not logged.
        post.updated_at = now
        _record(db, access, request, "post.imported_text_set", post, has_text=bool(new_body.strip()))
    elif changed:
        if post.status != PostStatus.DRAFT:
            # Any edit after review/scheduling goes back to draft and needs approval/scheduling again.
            _move(
                post,
                PostStatus.DRAFT,
                require_approval=await _require_approval(db, access.workspace_id),
                now=now,
            )
            post.scheduled_at = None
        _record(db, access, request, "post.edited", post)
    if changed or campaign_changed:
        await db.commit()
    return await _out(db, post)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: uuid.UUID, access: WriteAccess, request: Request, db: Db) -> None:
    post = await _locked_post(db, access, post_id)
    if post.status in (PostStatus.SCHEDULED, PostStatus.PUBLISHING, PostStatus.PUBLISHED):
        raise HTTPException(status.HTTP_409_CONFLICT, "post_not_deletable")
    _record(db, access, request, "post.deleted", post)
    await db.delete(post)
    await db.commit()


@router.post("/{post_id}/submit")
async def submit(post_id: uuid.UUID, access: WriteAccess, request: Request, db: Db) -> PostOut:
    post = await _locked_post(db, access, post_id)
    if not await _require_approval(db, access.workspace_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "approval_not_required")
    now = utcnow()
    _move(post, PostStatus.PENDING_APPROVAL, require_approval=True, now=now)
    _record(db, access, request, "post.submitted", post)
    reviewers = await members_with_roles(
        db, access.workspace_id, [WorkspaceRole.OWNER, WorkspaceRole.APPROVER]
    )
    await notify(
        db,
        workspace_id=access.workspace_id,
        kind="post.submitted",
        recipients=reviewers,
        now=now,
        post=post,
        data={"actor_name": access.principal.user.display_name},
        actor_id=access.principal.user.id,
    )
    await db.commit()
    return await _out(db, post)


async def _decide(
    post_id: uuid.UUID,
    access: WorkspaceAccess,
    request: Request,
    db: Db,
    decision: ApprovalDecision,
    comment: str | None,
) -> PostOut:
    post = await _locked_post(db, access, post_id)
    now = utcnow()
    to = PostStatus.APPROVED if decision == ApprovalDecision.APPROVED else PostStatus.DRAFT
    _move(post, to, require_approval=True, now=now)
    assert post.current_version_id is not None
    db.add(
        Approval(
            post_id=post.id,
            version_id=post.current_version_id,
            reviewer_id=access.principal.user.id,
            decision=decision,
            comment=comment,
            decided_at=now,
        )
    )
    _record(db, access, request, f"post.{decision.value}", post)
    data: dict[str, str | None] = {"actor_name": access.principal.user.display_name}
    if decision == ApprovalDecision.REJECTED:
        data["comment"] = comment
    await notify(
        db,
        workspace_id=access.workspace_id,
        kind=f"post.{decision.value}",
        recipients=[await post_author(db, post)],
        now=now,
        post=post,
        data=data,
        actor_id=access.principal.user.id,
    )
    await db.commit()
    return await _out(db, post)


@router.post("/{post_id}/approve")
async def approve(post_id: uuid.UUID, access: ApproveAccess, request: Request, db: Db) -> PostOut:
    return await _decide(post_id, access, request, db, ApprovalDecision.APPROVED, None)


@router.post("/{post_id}/reject")
async def reject(
    post_id: uuid.UUID, body: RejectRequest, access: ApproveAccess, request: Request, db: Db
) -> PostOut:
    return await _decide(post_id, access, request, db, ApprovalDecision.REJECTED, body.comment.strip())


async def _schedule(post: Post, when: datetime, access: WorkspaceAccess, db: Db) -> None:
    account = await _account(db, access.workspace_id, post.account_id)
    if account.status != LinkedInAccountStatus.ACTIVE or not account.access_token_encrypted:
        raise HTTPException(status.HTTP_409_CONFLICT, "account_not_connected")
    if account.access_token_expires_at <= when:
        raise HTTPException(status.HTTP_409_CONFLICT, "token_expires_before_schedule")
    now = utcnow()
    if post.status == PostStatus.SCHEDULED:
        # Reschedule (e.g. calendar drag): same status, new time. The row lock keeps the worker out;
        # a post the worker already claimed is PUBLISHING and cannot be moved.
        # Approval may have been turned on after this post was scheduled: then the current version
        # must have been approved before it can be moved or published now.
        if await _require_approval(db, access.workspace_id) and not await version_approved(db, post):
            raise HTTPException(status.HTTP_409_CONFLICT, "approval_required")
        post.scheduled_at = when
        post.updated_at = now
        post.attempt_count = 0
        post.next_attempt_at = None
        return
    if post.status == PostStatus.FAILED:
        # Manual retry: the user confirmed in the UI that the post is not on LinkedIn.
        # Approval may have been turned on after the post was scheduled (same rule as rescheduling and
        # as `retry-failed` on the account): a version nobody approved is not published.
        if await _require_approval(db, access.workspace_id) and not await version_approved(db, post):
            raise HTTPException(status.HTTP_409_CONFLICT, "approval_required")
        await db.execute(
            update(PublishAttempt)
            .where(PublishAttempt.post_id == post.id, PublishAttempt.finished_at.is_(None))
            .values(finished_at=now, error_code="abandoned_by_user")
        )
    _move(
        post, PostStatus.SCHEDULED, require_approval=await _require_approval(db, access.workspace_id), now=now
    )
    post.scheduled_at = when
    post.attempt_count = 0
    post.next_attempt_at = None
    post.locked_until = None


@router.post("/{post_id}/schedule")
async def schedule(
    post_id: uuid.UUID, body: ScheduleRequest, access: WriteAccess, request: Request, db: Db
) -> PostOut:
    post = await _locked_post(db, access, post_id)
    now = utcnow()
    if (body.scheduled_at is None) == (body.local_time is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "give_scheduled_at_or_local_time")
    if body.local_time is not None:
        ws = await db.get(Workspace, access.workspace_id)
        tz = ZoneInfo(ws.time_zone if ws else "UTC")
        when = _resolve_local(body.local_time, tz).astimezone(now.tzinfo)
    else:
        assert body.scheduled_at is not None
        when = body.scheduled_at.astimezone(now.tzinfo)
    if when < now + MIN_LEAD or when > now + MAX_AHEAD:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "scheduled_at_out_of_range")
    previous = post.scheduled_at if post.status == PostStatus.SCHEDULED else None
    await _schedule(post, when, access, db)
    if previous is not None:
        _record(
            db,
            access,
            request,
            "post.rescheduled",
            post,
            scheduled_at={"from": previous.isoformat(), "to": when.isoformat()},
        )
    else:
        _record(db, access, request, "post.scheduled", post, scheduled_at=when.isoformat())
    await db.commit()
    return await _out(db, post)


@router.post("/{post_id}/publish-now")
async def publish_now(post_id: uuid.UUID, access: WriteAccess, request: Request, db: Db) -> PostOut:
    """Schedules for 'now'; the worker picks it up within one poll (about 15 seconds)."""
    post = await _locked_post(db, access, post_id)
    await _schedule(post, utcnow(), access, db)
    _record(db, access, request, "post.publish_now", post)
    await db.commit()
    return await _out(db, post)


@router.post("/{post_id}/unschedule")
async def unschedule(post_id: uuid.UUID, access: WriteAccess, request: Request, db: Db) -> PostOut:
    post = await _locked_post(db, access, post_id)
    needs_approval = await _require_approval(db, access.workspace_id)
    _move(
        post,
        PostStatus.APPROVED if needs_approval else PostStatus.DRAFT,
        require_approval=needs_approval,
        now=utcnow(),
    )
    post.scheduled_at = None
    _record(db, access, request, "post.unscheduled", post)
    await db.commit()
    return await _out(db, post)


@router.get("/{post_id}/versions")
async def list_versions(post_id: uuid.UUID, access: ReadAccess, db: Db) -> list[PostVersionOut]:
    """Newest first. At most VERSION_LIST_CAP rows; older versions are kept but not returned."""
    post = await db.get(Post, post_id)
    if post is None or post.workspace_id != access.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post_not_found")
    rows = list(
        (
            await db.execute(
                select(PostVersion, User.display_name)
                .join(User, User.id == PostVersion.author_id)
                .where(PostVersion.post_id == post.id)
                .order_by(PostVersion.created_at.desc(), PostVersion.id.desc())
                .limit(VERSION_LIST_CAP)
            )
        ).all()
    )
    approved_ids: set[uuid.UUID] = set()
    if rows:
        approved_ids = set(
            (
                await db.execute(
                    select(Approval.version_id).where(
                        Approval.post_id == post.id,
                        Approval.decision == ApprovalDecision.APPROVED,
                        Approval.version_id.in_([v.id for v, _ in rows]),
                    )
                )
            ).scalars()
        )
    return [
        PostVersionOut(
            id=version.id,
            created_at=version.created_at,
            author_id=version.author_id,
            author_name=author_name,
            body=version.body,
            media_ids=list(version.media_ids or []),
            link=_link_of(version),
            is_current=version.id == post.current_version_id,
            approved=version.id in approved_ids,
        )
        for version, author_name in rows
    ]


@router.post("/{post_id}/versions/{version_id}/restore")
async def restore_version(
    post_id: uuid.UUID, version_id: uuid.UUID, access: WriteAccess, request: Request, db: Db
) -> RestoreOut:
    post = await _locked_post(db, access, post_id)
    imported_live = post.external_source is not None and post.status == PostStatus.PUBLISHED
    if post.status not in RESTORE_EDITABLE and not imported_live:
        raise HTTPException(status.HTTP_409_CONFLICT, "post_not_editable")
    source = await db.get(PostVersion, version_id)
    if source is None or source.post_id != post.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version_not_found")
    now = utcnow()
    warnings: list[str] = []
    kept_media: list[uuid.UUID] = []
    for media_id in source.media_ids or []:
        asset = await db.get(MediaAsset, media_id)
        if asset is None or asset.workspace_id != access.workspace_id:
            warnings.append("media_missing")
            continue
        kept_media.append(media_id)
    # One warning entry is enough even if several media ids were gone.
    if "media_missing" in warnings:
        warnings = ["media_missing"]
    link = _link_of(source)
    if kept_media and link is not None:
        # Same rule as edit: video + link card is refused. Drop the link if the kept media is video.
        asset = await db.get(MediaAsset, kept_media[0])
        if asset and asset.mime_type.startswith("video/"):
            link = None
    version = PostVersion(
        post_id=post.id,
        body=source.body,
        media_ids=kept_media,
        link_url=link.url if link else None,
        link_title=link.title if link else None,
        link_description=link.description if link else None,
        author_id=access.principal.user.id,
        created_at=now,
    )
    db.add(version)
    await db.flush()
    post.current_version_id = version.id
    post.updated_at = now
    if imported_live:
        _record(db, access, request, "post.version_restored", post, from_version_id=str(version_id))
    else:
        if post.status != PostStatus.DRAFT:
            _move(
                post,
                PostStatus.DRAFT,
                require_approval=await _require_approval(db, access.workspace_id),
                now=now,
            )
            post.scheduled_at = None
        _record(db, access, request, "post.version_restored", post, from_version_id=str(version_id))
    await db.commit()
    out = await _out(db, post)
    return RestoreOut(**out.model_dump(), warnings=warnings)
