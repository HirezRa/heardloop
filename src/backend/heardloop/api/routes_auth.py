"""Authentication endpoints (FR-01)."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select

from heardloop import audit
from heardloop.api.deps import (
    SESSION_COOKIE,
    SESSION_COOKIE_HOST,
    CurrentPrincipal,
    Db,
    client_ip,
    require_client_header,
    session_token,
)
from heardloop.api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MembershipOut,
    MeOut,
    MfaChallenge,
    UiPrefs,
    UserOut,
)
from heardloop.auth.service import (
    AuthenticationError,
    authenticate,
    create_session,
    register_failure,
    revoke_by_token,
    revoke_other_sessions,
    revoke_session,
)
from heardloop.config import get_settings
from heardloop.models import Membership, User, Workspace
from heardloop.security.passwords import (
    PasswordPolicyError,
    hash_password_async,
    validate_password,
    verify_password_async,
)
from heardloop.timeutil import utcnow

log = logging.getLogger("heardloop.auth")
router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(require_client_header)])


async def _me(db: Db, user: User, csrf_token: str) -> MeOut:
    rows = (
        await db.execute(
            select(Membership, Workspace)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(Membership.user_id == user.id)
            .order_by(Workspace.name)
        )
    ).all()
    return MeOut(
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name, locale=user.locale),
        memberships=[MembershipOut(workspace_id=w.id, workspace_name=w.name, role=m.role) for m, w in rows],
        csrf_token=csrf_token,
        mfa_enabled=user.mfa_enabled_at is not None,
        ui_prefs=_prefs_of(user),
        ai_assist=get_settings().ai_configured,
    )


def _prefs_of(user: User) -> UiPrefs:
    """Stored JSON -> validated prefs; unreadable values fall back to defaults instead of breaking sign-in."""
    try:
        return UiPrefs.model_validate(user.ui_prefs or {})
    except ValueError:
        return UiPrefs()


@router.get("/me/preferences")
async def get_preferences(principal: CurrentPrincipal) -> UiPrefs:
    return _prefs_of(principal.user)


@router.put("/me/preferences")
async def set_preferences(body: UiPrefs, principal: CurrentPrincipal, db: Db) -> UiPrefs:
    principal.user.ui_prefs = body.model_dump()
    await db.commit()
    return body


def set_session_cookie(response: Response, token: str, max_age: timedelta) -> None:
    """With COOKIE_SECURE only the `__Host-` name is written (Secure, Path=/, no Domain)."""
    secure = get_settings().cookie_secure
    response.set_cookie(
        SESSION_COOKIE_HOST if secure else SESSION_COOKIE,
        token,
        max_age=int(max_age.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(SESSION_COOKIE_HOST, path="/", secure=True, httponly=True, samesite="lax")


MFA_PENDING_TTL = timedelta(minutes=5)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, db: Db) -> MeOut | MfaChallenge:
    settings = get_settings()
    now = utcnow()
    ip = client_ip(request)
    try:
        user = await authenticate(db, body.email, body.password, now)
    except AuthenticationError:
        audit.record(db, action="auth.login_failed", entity="user", now=now, ip_address=ip)
        await db.commit()
        log.info("Login failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials") from None

    old_token = session_token(request)
    if old_token:
        await revoke_by_token(db, old_token)  # one session per browser; no stale rows

    if user.mfa_enabled_at is not None:
        # Step 1 of 2: a short-lived session that can only call /auth/mfa/verify.
        pending = await create_session(
            db,
            user,
            now,
            absolute=MFA_PENDING_TTL,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
            mfa_pending=True,
        )
        audit.record(
            db,
            action="auth.password_ok_mfa_pending",
            entity="user",
            entity_id=user.id,
            actor_id=user.id,
            now=now,
            ip_address=ip,
        )
        await db.commit()
        set_session_cookie(response, pending.token, MFA_PENDING_TTL)
        return MfaChallenge(csrf_token=pending.session.csrf_token)

    absolute = timedelta(days=settings.session_absolute_days)
    result = await create_session(
        db, user, now, absolute=absolute, ip_address=ip, user_agent=request.headers.get("user-agent")
    )
    audit.record(
        db, action="auth.login", entity="user", entity_id=user.id, actor_id=user.id, now=now, ip_address=ip
    )
    await db.commit()
    set_session_cookie(response, result.token, absolute)
    log.info("Login succeeded", extra={"user_id": str(user.id)})
    return await _me(db, user, result.session.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: CurrentPrincipal, request: Request, response: Response, db: Db) -> None:
    audit.record(
        db,
        action="auth.logout",
        entity="user",
        entity_id=principal.user.id,
        actor_id=principal.user.id,
        now=utcnow(),
        ip_address=client_ip(request),
    )
    await revoke_session(db, principal.session.id)
    clear_session_cookies(response)


@router.get("/me")
async def me(principal: CurrentPrincipal, db: Db) -> MeOut:
    return await _me(db, principal.user, principal.session.csrf_token)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest, principal: CurrentPrincipal, request: Request, db: Db
) -> None:
    user = (await db.execute(select(User).where(User.id == principal.user.id).with_for_update())).scalar_one()
    now = utcnow()
    matches, _ = await verify_password_async(user.password_hash, body.current_password)
    if not matches:
        # Same counter as login: a stolen session cannot brute-force the current password.
        register_failure(user, now)
        if user.locked_until is not None:
            await revoke_other_sessions(db, user.id, keep=None)
        await db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current_password_incorrect")
    if body.new_password == body.current_password:  # the current password was just verified against the hash
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "password_unchanged")
    try:
        validate_password(body.new_password, email=user.email)
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    user.password_hash = await hash_password_async(body.new_password)
    user.failed_login_count = 0
    user.password_changed_at = now
    await revoke_other_sessions(db, user.id, keep=principal.session.id)
    audit.record(
        db,
        action="auth.password_changed",
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        now=now,
        ip_address=client_ip(request),
    )
    await db.commit()
