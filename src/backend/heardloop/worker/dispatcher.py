"""Scheduled publishing without a broker.

Every poll the worker claims due posts with SELECT ... FOR UPDATE SKIP LOCKED and a short lease
(locked_until), so two workers can never publish the same post (FR-18). Retries use next_attempt_at
(FR-19: 3 attempts, 1 / 5 / 15 minutes).

Safety rule: if a previous attempt started but never finished (worker crashed mid-call), we do NOT
retry automatically - LinkedIn may already have the post. The post goes to FAILED with a clear reason.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop import audit, media
from heardloop.config import get_settings
from heardloop.domain.workflow import PostStatus
from heardloop.models import (
    AuditLogEntry,
    LinkedInAccount,
    LinkedInAccountStatus,
    MediaAsset,
    Notification,
    Post,
    PostVersion,
    PublishAttempt,
    WorkspaceRole,
)
from heardloop.notify import RECONNECT_CODES, error_code, members_with_roles, notify, post_author
from heardloop.publishing.base import (
    MAX_ATTEMPTS,
    ImageAttachment,
    LinkAttachment,
    Publisher,
    PublishRequest,
    PublishResult,
    VideoAttachment,
    delay_after,
)
from heardloop.security.crypto import TokenDecryptionError, TokenProtector

log = logging.getLogger("heardloop.worker")

LEASE = timedelta(minutes=5)
VIDEO_LEASE = timedelta(minutes=30)  # upload of up to 200 MB in 4 MB parts plus LinkedIn processing
BATCH_SIZE = 5  # an image post can take ~2 minutes; keep a batch well inside the lease
RECONNECT_NOTIFY_WINDOW = timedelta(hours=24)

Clock = Callable[[], datetime]


async def _notify_safely(session: AsyncSession, post: Post, now: datetime) -> None:
    post_id = str(post.id)  # read before a possible rollback expires the instance
    try:
        await _notify_outcome(session, post, now)
        await session.commit()
    except Exception:
        await session.rollback()
        log.exception("Could not create notifications", extra={"post_id": post_id})


async def _notify_outcome(session: AsyncSession, post: Post, now: datetime) -> None:
    """FR-19/26: tell the author about a published post; tell the author and owners about a failure."""
    author = await post_author(session, post)
    if post.status == PostStatus.PUBLISHED:
        urn = post.linkedin_post_urn
        url = f"https://www.linkedin.com/feed/update/{urn}/" if urn else None
        await notify(
            session,
            workspace_id=post.workspace_id,
            kind="post.published",
            recipients=[author],
            now=now,
            post=post,
            data={"linkedin_url": url},
        )
    elif post.status == PostStatus.FAILED:
        owners = await members_with_roles(session, post.workspace_id, [WorkspaceRole.OWNER])
        await notify(
            session,
            workspace_id=post.workspace_id,
            kind="post.failed",
            recipients=[author, *owners],
            now=now,
            post=post,
            data={"error": error_code(post.last_error)},
        )


async def _account_connector(
    session: AsyncSession, account_id: uuid.UUID, workspace_id: uuid.UUID
) -> uuid.UUID | None:
    """The member who most recently connected this account (from the audit log)."""
    return (
        await session.execute(
            select(AuditLogEntry.actor_id)
            .where(
                AuditLogEntry.action == "linkedin.connected",
                AuditLogEntry.entity_id == str(account_id),
                AuditLogEntry.workspace_id == workspace_id,
            )
            .order_by(AuditLogEntry.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def flag_reconnect_needed(
    session: AsyncSession, account: LinkedInAccount, reason: str, now: datetime
) -> None:
    """Mark the account (earliest reason wins) and notify owners + connector once per 24 h."""
    code = reason[:32]
    if account.needs_reconnect_reason is None:
        account.needs_reconnect_reason = code
        account.needs_reconnect_at = now
    recent = (
        await session.execute(
            select(Notification.id)
            .where(
                Notification.workspace_id == account.workspace_id,
                Notification.kind == "account.reconnect_needed",
                Notification.data["account_id"].astext == str(account.id),
                Notification.created_at >= now - RECONNECT_NOTIFY_WINDOW,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if recent is not None:
        return
    owners = await members_with_roles(session, account.workspace_id, [WorkspaceRole.OWNER])
    connector = await _account_connector(session, account.id, account.workspace_id)
    await notify(
        session,
        workspace_id=account.workspace_id,
        kind="account.reconnect_needed",
        recipients=[*owners, connector],
        now=now,
        data={
            "account_id": str(account.id),
            "reason": account.needs_reconnect_reason or code,
        },
    )


async def _maybe_flag_reconnect(session: AsyncSession, post: Post, reason: str, now: datetime) -> None:
    if reason not in RECONNECT_CODES:
        return
    account = await session.get(LinkedInAccount, post.account_id)
    if account is None:
        return
    await flag_reconnect_needed(session, account, reason, now)


def _audit_publish(
    session: AsyncSession,
    post: Post,
    *,
    action: str,
    now: datetime,
    changes: dict[str, object],
) -> None:
    audit.record(
        session,
        action=action,
        entity="post",
        entity_id=post.id,
        actor_id=None,
        workspace_id=post.workspace_id,
        changes=changes,
        now=now,
    )


async def claim_due_posts(session: AsyncSession, now: datetime) -> list[uuid.UUID]:
    """Moves due posts to PUBLISHING (if needed) and leases them. Commits."""
    due = or_(
        and_(Post.status == PostStatus.SCHEDULED, Post.scheduled_at <= now),
        and_(
            Post.status == PostStatus.PUBLISHING,
            or_(Post.next_attempt_at.is_(None), Post.next_attempt_at <= now),
        ),
    )
    lease_free = or_(Post.locked_until.is_(None), Post.locked_until < now)
    stmt = (
        select(Post.id)
        .where(due, lease_free)
        .order_by(Post.scheduled_at)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    ids = list((await session.execute(stmt)).scalars())
    if ids:
        # Documented exception to Post.transition_to: the row lock is the concurrency guard here.
        await session.execute(
            update(Post)
            .where(Post.id.in_(ids))
            .values(status=PostStatus.PUBLISHING, locked_until=now + LEASE, updated_at=now)
            .execution_options(synchronize_session=False)
        )
    await session.commit()
    return ids


async def _build_request(
    session: AsyncSession, post: Post, protector: TokenProtector, now: datetime
) -> PublishRequest | str:
    """The request to send, or an error code when the post cannot be published at all."""
    version = await session.get(PostVersion, post.current_version_id) if post.current_version_id else None
    if version is None or not version.body.strip():
        return "no_content"
    account = await session.get(LinkedInAccount, post.account_id)
    if (
        account is None
        or account.status != LinkedInAccountStatus.ACTIVE
        or not account.access_token_encrypted
    ):
        return "account_not_connected"
    if account.access_token_expires_at <= now:
        return "token_expired"
    try:
        token = protector.unprotect(account.access_token_encrypted, f"linkedin-account:{account.id}")
    except TokenDecryptionError:
        return "token_unreadable"
    image: ImageAttachment | None = None
    video: VideoAttachment | None = None
    if version.media_ids:
        asset = await session.get(MediaAsset, version.media_ids[0])
        if asset is None or asset.workspace_id != post.workspace_id:
            return "media_missing"
        cached = asset.linkedin_asset_urn if asset.linkedin_owner_urn == account.urn else None
        root = get_settings().media_root
        if asset.mime_type.startswith("video/"):
            try:
                path = media.path_of(root, asset.storage_key)
                size = path.stat().st_size
            except (OSError, ValueError):
                return "media_missing"
            if version.link_url:
                return "video_with_link"  # LinkedIn: a post is a video OR an article, never both
            video = VideoAttachment(media_id=asset.id, path=path, size=size, cached_urn=cached)
        else:
            try:
                data = media.load(root, asset.storage_key)
            except (OSError, ValueError):
                return "media_missing"
            image = ImageAttachment(media_id=asset.id, mime=asset.mime_type, data=data, cached_urn=cached)
    link: LinkAttachment | None = None
    if version.link_url and version.link_title:
        link = LinkAttachment(
            url=version.link_url, title=version.link_title, description=version.link_description
        )
    return PublishRequest(
        post_id=post.id,
        idempotency_key=post.idempotency_key,
        author_urn=account.urn,
        access_token=token,
        text=version.body,
        image=image,
        video=video,
        link=link,
    )


async def publish_one(
    factory: async_sessionmaker[AsyncSession],
    publisher: Publisher,
    post_id: uuid.UUID,
    clock: Clock,
    protector: TokenProtector,
) -> None:
    async with factory() as session:
        post = await session.get(Post, post_id)
        if post is None or post.status != PostStatus.PUBLISHING:
            log.warning("Post skipped: not found or not publishing", extra={"post_id": str(post_id)})
            return

        last = (
            await session.execute(
                select(PublishAttempt)
                .where(PublishAttempt.post_id == post_id)
                .order_by(PublishAttempt.attempt_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is not None and last.finished_at is None:
            now = clock()
            last.finished_at = now
            last.error_code = "unknown_outcome"
            post.mark_failed("unknown_outcome: previous attempt did not finish; check LinkedIn first", now)
            _audit_publish(
                session,
                post,
                action="post.publish_failed",
                now=now,
                changes={
                    "reason": "unknown_outcome",
                    "attempt": last.attempt_no,
                    "will_retry": False,
                    "next_attempt_at": None,
                },
            )
            await session.commit()
            await _notify_safely(session, post, now)
            log.error("Post failed: unknown outcome of previous attempt", extra={"post_id": str(post_id)})
            return

        request = await _build_request(session, post, protector, clock())
        if isinstance(request, str):
            now = clock()
            post.mark_failed(f"{request}: fix it and schedule the post again", now)
            _audit_publish(
                session,
                post,
                action="post.publish_failed",
                now=now,
                changes={
                    "reason": request,
                    "attempt": post.attempt_count,
                    "will_retry": False,
                    "next_attempt_at": None,
                },
            )
            await _maybe_flag_reconnect(session, post, request, now)
            await session.commit()
            await _notify_safely(session, post, now)
            log.error("Post cannot be published", extra={"post_id": str(post_id), "error_code": request})
            return

        # attempt_no keeps counting across manual retries (unique per post);
        # attempt_count is the retry budget of the current schedule.
        attempt_no = (last.attempt_no if last is not None else 0) + 1
        attempt = PublishAttempt(post_id=post_id, attempt_no=attempt_no, started_at=clock())
        session.add(attempt)
        post.attempt_count += 1
        # Renew per post: earlier posts in the batch may have taken a while. Videos get a long lease.
        post.locked_until = clock() + (VIDEO_LEASE if request.video is not None else LEASE)
        await session.commit()  # record the attempt BEFORE calling LinkedIn

        try:
            result = await publisher.publish(request)
        except Exception as exc:
            log.exception("Unexpected publish error", extra={"post_id": str(post_id), "attempt": attempt_no})
            # We cannot tell whether LinkedIn got the post: never retry automatically.
            result = PublishResult.permanent_error("unknown_outcome", type(exc).__name__)

        now = clock()
        attempt.finished_at = now
        attempt.http_status = result.http_status
        attempt.error_code = result.error_code
        attachment = request.image or request.video
        if (
            attachment is not None
            and attachment.cached_urn
            and not result.success
            and not result.transient
            and (result.http_status in (400, 404, 422) or result.error_code == "video_processing_failed")
        ):
            # LinkedIn rejected the post; the cached media may be the reason. Upload fresh next time.
            stale = await session.get(MediaAsset, attachment.media_id)
            if stale is not None:
                stale.linkedin_asset_urn = None
        if result.uploaded_image_urn and attachment is not None:
            # Also for a video still processing: the next attempt reuses the URN instead of uploading again.
            asset = await session.get(MediaAsset, attachment.media_id)
            if asset is not None:
                asset.linkedin_asset_urn = result.uploaded_image_urn
                asset.linkedin_owner_urn = request.author_urn

        if result.success and result.post_urn:
            post.mark_published(result.post_urn, now)
            _audit_publish(
                session,
                post,
                action="post.published",
                now=now,
                changes={"linkedin_post_urn": result.post_urn, "attempt": attempt_no},
            )
            log.info("Post published", extra={"post_id": str(post_id), "attempt": attempt_no})
        elif result.transient and (
            post.attempt_count < MAX_ATTEMPTS or result.error_code == "video_processing"
        ):
            # LinkedIn's video processing time is not our failure: it does not use up the retry budget
            # (still bounded: attempt_count keeps growing and six attempts in total end it).
            if result.error_code == "video_processing" and post.attempt_count >= MAX_ATTEMPTS + 3:
                post.mark_failed("video_processing: LinkedIn did not finish processing the video", now)
                _audit_publish(
                    session,
                    post,
                    action="post.publish_gave_up",
                    now=now,
                    changes={
                        "reason": "video_processing",
                        "attempt": attempt_no,
                        "will_retry": False,
                        "next_attempt_at": None,
                    },
                )
                log.error("Post failed: video never became available", extra={"post_id": str(post_id)})
                await session.commit()
                await _notify_safely(session, post, now)
                return
            post.next_attempt_at = now + delay_after(min(post.attempt_count, MAX_ATTEMPTS))
            post.locked_until = None
            _audit_publish(
                session,
                post,
                action="post.publish_failed",
                now=now,
                changes={
                    "reason": result.error_code,
                    "attempt": attempt_no,
                    "will_retry": True,
                    "next_attempt_at": post.next_attempt_at.isoformat(),
                },
            )
            log.warning(
                "Publish attempt failed, will retry",
                extra={"post_id": str(post_id), "attempt": attempt_no, "error_code": result.error_code},
            )
        else:
            reason = result.error_code or "unknown"
            post.mark_failed(f"{result.error_code}: {result.error_message}", now)
            gave_up = bool(result.transient)
            _audit_publish(
                session,
                post,
                action="post.publish_gave_up" if gave_up else "post.publish_failed",
                now=now,
                changes={
                    "reason": reason,
                    "attempt": attempt_no,
                    "will_retry": False,
                    "next_attempt_at": None,
                },
            )
            if result.http_status == 401 or reason in RECONNECT_CODES:
                flag_reason = reason if reason in RECONNECT_CODES else "token_invalid"
                await _maybe_flag_reconnect(session, post, flag_reason, now)
            log.error(
                "Post failed",
                extra={"post_id": str(post_id), "attempt": attempt_no, "error_code": result.error_code},
            )
        await session.commit()  # the publish outcome is saved first, whatever happens to notifications
        if post.status in (PostStatus.PUBLISHED, PostStatus.FAILED):
            await _notify_safely(session, post, now)


async def run_once(
    factory: async_sessionmaker[AsyncSession], publisher: Publisher, clock: Clock, protector: TokenProtector
) -> int:
    async with factory() as session:
        ids = await claim_due_posts(session, clock())
    for post_id in ids:
        await publish_one(factory, publisher, post_id, clock, protector)
    return len(ids)
