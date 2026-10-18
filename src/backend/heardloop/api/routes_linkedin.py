"""Connect LinkedIn accounts (FR-03) and track token expiry (FR-05)."""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from heardloop import audit
from heardloop.api.deps import (
    MFA_REQUIRED_PERMISSIONS,
    CurrentPrincipal,
    Db,
    WorkspaceAccess,
    client_ip,
    require_client_header,
    require_permission,
)
from heardloop.api.post_approval import version_approved
from heardloop.auth.permissions import Permission, has_permission
from heardloop.config import get_settings
from heardloop.domain.workflow import PostStatus
from heardloop.linkedin.client import LinkedInClient, LinkedInError
from heardloop.linkedin.state import OAuthState, new_nonce, sign_state, verify_state
from heardloop.models import (
    LinkedInAccount,
    LinkedInAccountStatus,
    LinkedInAccountType,
    Membership,
    Post,
    PublishAttempt,
    Workspace,
)
from heardloop.notify import RECONNECT_CODES, error_code
from heardloop.security.crypto import TokenDecryptionError, TokenProtector, derive_key
from heardloop.timeutil import utcnow

log = logging.getLogger("heardloop.linkedin")

router = APIRouter(tags=["linkedin"], dependencies=[Depends(require_client_header)])

NONCE_COOKIE = "hl_oauth"
CALLBACK_PATH = "/api/v1/linkedin/callback"
EXPIRY_WARNING = timedelta(days=7)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
ManageAccounts = Annotated[WorkspaceAccess, Depends(require_permission(Permission.ACCOUNTS_MANAGE))]
ApproveAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.POSTS_APPROVE))]


class AccountOut(BaseModel):
    id: uuid.UUID
    display_name: str
    urn: str
    type: LinkedInAccountType
    status: LinkedInAccountStatus
    expires_at: datetime
    expiring_soon: bool
    needs_reconnect_reason: str | None = None
    needs_reconnect_at: datetime | None = None


class ConnectOut(BaseModel):
    authorization_url: str


class RetryFailedOut(BaseModel):
    count: int
    skipped_unapproved: int = 0


def _state_key() -> bytes:
    return derive_key(get_settings().token_key.get_secret_value(), "linkedin-oauth-state")


def _client(request: Request) -> LinkedInClient:
    return LinkedInClient(get_settings(), request.app.state.http)


def _effective_status(acc: LinkedInAccount, now: datetime) -> LinkedInAccountStatus:
    if acc.status == LinkedInAccountStatus.ACTIVE and acc.access_token_expires_at <= now:
        return LinkedInAccountStatus.EXPIRED
    return acc.status


def _out(acc: LinkedInAccount, now: datetime) -> AccountOut:
    st = _effective_status(acc, now)
    return AccountOut(
        id=acc.id,
        display_name=acc.display_name,
        urn=acc.urn,
        type=acc.type,
        status=st,
        expires_at=acc.access_token_expires_at,
        expiring_soon=st == LinkedInAccountStatus.ACTIVE
        and acc.access_token_expires_at - now <= EXPIRY_WARNING,
        needs_reconnect_reason=acc.needs_reconnect_reason,
        needs_reconnect_at=acc.needs_reconnect_at,
    )


def _clear_reconnect(acc: LinkedInAccount) -> None:
    acc.needs_reconnect_reason = None
    acc.needs_reconnect_at = None


@router.get("/api/v1/workspaces/{workspace_id}/linkedin/accounts")
async def list_accounts(access: ReadAccess, db: Db) -> list[AccountOut]:
    now = utcnow()
    rows = (
        await db.execute(
            select(LinkedInAccount)
            .where(LinkedInAccount.workspace_id == access.workspace_id)
            .order_by(LinkedInAccount.display_name)
        )
    ).scalars()
    return [_out(a, now) for a in rows]


@router.post("/api/v1/workspaces/{workspace_id}/linkedin/connect")
async def start_connect(access: ManageAccounts, request: Request, response: Response) -> ConnectOut:
    settings = get_settings()
    if not settings.linkedin_configured or not settings.linkedin_redirect_uri:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "linkedin_not_configured")
    nonce = new_nonce()
    state = sign_state(
        _state_key(), OAuthState(access.principal.user.id, access.workspace_id, nonce), utcnow()
    )
    response.set_cookie(
        NONCE_COOKIE,
        nonce,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",  # sent on LinkedIn's top-level redirect back to us
        path=CALLBACK_PATH,
    )
    return ConnectOut(authorization_url=_client(request).authorization_url(state))


def _back(result: str, reason: str | None = None) -> RedirectResponse:
    url = f"/?section=accounts&linkedin={result}"
    if reason:
        url += f"&reason={reason}"
    resp = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(NONCE_COOKIE, path=CALLBACK_PATH)
    resp.headers["Referrer-Policy"] = "no-referrer"  # the callback URL carried the OAuth code
    return resp


@router.get(CALLBACK_PATH)
async def callback(
    request: Request,
    principal: CurrentPrincipal,
    db: Db,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    now = utcnow()
    if error:
        # e.g. user_cancelled_authorize - nothing to clean up.
        return _back("cancelled" if "cancel" in error else "error", "denied")
    if not code or not state:
        return _back("error", "missing_code")
    verified = verify_state(_state_key(), state, now, cookie_nonce=request.cookies.get(NONCE_COOKIE))
    if verified is None or verified.user_id != principal.user.id:
        log.warning("LinkedIn callback with invalid state", extra={"user_id": str(principal.user.id)})
        return _back("error", "invalid_state")

    # Re-check permission now: the role or 2FA could have changed during the redirect.
    membership = await db.get(Membership, (principal.user.id, verified.workspace_id))
    needs_mfa = (
        Permission.ACCOUNTS_MANAGE in MFA_REQUIRED_PERMISSIONS and principal.user.mfa_enabled_at is None
    )
    if membership is None or not has_permission(membership.role, Permission.ACCOUNTS_MANAGE) or needs_mfa:
        return _back("error", "forbidden")

    client = _client(request)
    try:
        token = await client.exchange_code(code)
        member = await client.userinfo(token.access_token)
    except LinkedInError as exc:
        log.warning("LinkedIn connect failed", extra={"code": exc.code})
        return _back("error", "linkedin")
    if "w_member_social" not in token.scopes:
        return _back("error", "missing_scope")

    protector: TokenProtector = request.app.state.token_protector
    account = (
        await db.execute(
            select(LinkedInAccount)
            .where(LinkedInAccount.workspace_id == verified.workspace_id, LinkedInAccount.urn == member.urn)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if account is None:
        account = LinkedInAccount(
            id=uuid.uuid4(),
            workspace_id=verified.workspace_id,
            type=LinkedInAccountType.PERSON,
            urn=member.urn,
            display_name=member.name,
            access_token_encrypted="",
            access_token_expires_at=now,
            connected_at=now,
        )
        db.add(account)
    was_reconnect = (
        account.needs_reconnect_reason is not None or account.status != LinkedInAccountStatus.ACTIVE
    )
    account.display_name = member.name
    account.access_token_encrypted = protector.protect(token.access_token, f"linkedin-account:{account.id}")
    account.access_token_expires_at = now + timedelta(seconds=max(token.expires_in, 0))
    account.scopes = list(token.scopes)
    account.status = LinkedInAccountStatus.ACTIVE
    _clear_reconnect(account)
    audit.record(
        db,
        action="linkedin.connected",
        entity="linkedin_account",
        entity_id=account.id,
        actor_id=principal.user.id,
        workspace_id=verified.workspace_id,
        ip_address=client_ip(request),
        changes={"expires_at": account.access_token_expires_at.isoformat()},
        now=now,
    )
    if was_reconnect:
        audit.record(
            db,
            action="linkedin.account_reconnected",
            entity="linkedin_account",
            entity_id=account.id,
            actor_id=principal.user.id,
            workspace_id=verified.workspace_id,
            ip_address=client_ip(request),
            now=now,
        )
    try:
        await db.commit()
    except IntegrityError:
        # A parallel callback for the same member won the insert; that account is connected.
        await db.rollback()
    return _back("connected")


@router.delete(
    "/api/v1/workspaces/{workspace_id}/linkedin/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def disconnect(account_id: uuid.UUID, access: ManageAccounts, request: Request, db: Db) -> None:
    """Forgets the token and asks LinkedIn to revoke it (best-effort). The row stays: posts use it."""
    acc = (
        await db.execute(
            select(LinkedInAccount)
            .where(LinkedInAccount.id == account_id, LinkedInAccount.workspace_id == access.workspace_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account_not_found")
    token: str | None = None
    if acc.access_token_encrypted and acc.status == LinkedInAccountStatus.ACTIVE:
        try:
            token = request.app.state.token_protector.unprotect(
                acc.access_token_encrypted, f"linkedin-account:{acc.id}"
            )
        except TokenDecryptionError:
            token = None  # unreadable anyway; nothing to revoke
    acc.access_token_encrypted = ""
    acc.refresh_token_encrypted = None
    acc.status = LinkedInAccountStatus.REVOKED
    _clear_reconnect(acc)
    audit.record(
        db,
        action="linkedin.disconnected",
        entity="linkedin_account",
        entity_id=acc.id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        now=utcnow(),
    )
    await db.commit()
    if token and get_settings().linkedin_configured:
        revoked = await _client(request).revoke_token(
            token
        )  # after the commit: no row lock during network IO
        log.info("LinkedIn token revoke on disconnect", extra={"revoked": revoked})


@router.post("/api/v1/workspaces/{workspace_id}/linkedin/accounts/{account_id}/retry-failed")
async def retry_failed(
    account_id: uuid.UUID, access: ApproveAccess, request: Request, db: Db
) -> RetryFailedOut:
    """Reschedule failed posts that failed because this account needed reconnecting.

    Refuses while the account still needs reconnecting or is not active. When the workspace
    requires approval, posts whose current version is not approved are skipped (same rule as
    the manual schedule path) and counted in `skipped_unapproved`.
    """
    acc = (
        await db.execute(
            select(LinkedInAccount)
            .where(LinkedInAccount.id == account_id, LinkedInAccount.workspace_id == access.workspace_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account_not_found")
    if acc.needs_reconnect_reason is not None or acc.status != LinkedInAccountStatus.ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "account_needs_reconnect")
    now = utcnow()
    when = now + timedelta(minutes=1)
    ws = await db.get(Workspace, access.workspace_id)
    need_approval = bool(ws and ws.require_approval)
    posts = list(
        (
            await db.execute(
                select(Post)
                .where(
                    Post.workspace_id == access.workspace_id,
                    Post.account_id == account_id,
                    Post.status == PostStatus.FAILED,
                )
                .with_for_update()
            )
        ).scalars()
    )
    count = 0
    skipped_unapproved = 0
    for post in posts:
        if error_code(post.last_error) not in RECONNECT_CODES:
            continue
        if post.scheduled_at is not None and post.scheduled_at > now:
            continue
        if need_approval and not await version_approved(db, post):
            skipped_unapproved += 1
            continue
        await db.execute(
            update(PublishAttempt)
            .where(PublishAttempt.post_id == post.id, PublishAttempt.finished_at.is_(None))
            .values(finished_at=now, error_code="abandoned_by_user")
        )
        post.transition_to(PostStatus.SCHEDULED, require_approval=need_approval, now=now)
        post.scheduled_at = when
        post.attempt_count = 0
        post.next_attempt_at = None
        post.locked_until = None
        post.last_error = None
        audit.record(
            db,
            action="post.rescheduled",
            entity="post",
            entity_id=post.id,
            actor_id=access.principal.user.id,
            workspace_id=access.workspace_id,
            ip_address=client_ip(request),
            changes={"by": "retry_failed", "scheduled_at": when.isoformat()},
            now=now,
        )
        count += 1
    await db.commit()
    return RetryFailedOut(count=count, skipped_unapproved=skipped_unapproved)
