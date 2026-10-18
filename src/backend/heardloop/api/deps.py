"""Shared FastAPI dependencies: DB session, current user, CSRF and workspace permission checks."""

import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from heardloop.auth.permissions import Permission, has_permission
from heardloop.auth.service import resolve_session
from heardloop.config import get_settings
from heardloop.models import Membership, User, UserSession, WorkspaceRole
from heardloop.timeutil import utcnow

SESSION_COOKIE = "hl_session"
SESSION_COOKIE_HOST = "__Host-hl_session"  # used when COOKIE_SECURE=true (Secure, Path=/, no Domain)
CSRF_HEADER = "X-CSRF-Token"
CLIENT_HEADER = "X-Requested-With"
CLIENT_HEADER_VALUE = "heardloop"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


Db = Annotated[AsyncSession, Depends(get_db)]


def session_token(request: Request) -> str | None:
    """The session token from either cookie name (the old name stays readable across upgrades)."""
    return request.cookies.get(SESSION_COOKIE_HOST) or request.cookies.get(SESSION_COOKIE) or None


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def require_client_header(request: Request) -> None:
    """Cross-site forms cannot set custom headers, and CORS is not enabled - blocks login CSRF."""
    if request.method in UNSAFE_METHODS and request.headers.get(CLIENT_HEADER) != CLIENT_HEADER_VALUE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing_client_header")


@dataclass(frozen=True, slots=True)
class Principal:
    user: User
    session: UserSession


async def _principal(request: Request, db: Db, *, allow_mfa_pending: bool) -> Principal:
    token = session_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    settings = get_settings()
    resolved = await resolve_session(db, token, utcnow(), idle=timedelta(hours=settings.session_idle_hours))
    if resolved is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    session, user = resolved
    if session.mfa_pending and not allow_mfa_pending:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "mfa_required")
    if allow_mfa_pending and not session.mfa_pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_not_pending")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent.encode(), session.csrf_token.encode()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_failed")
    return Principal(user=user, session=session)


async def current_principal(request: Request, db: Db) -> Principal:
    """A fully signed-in user (password + second factor when enabled)."""
    return await _principal(request, db, allow_mfa_pending=False)


async def pending_principal(request: Request, db: Db) -> Principal:
    """Password accepted, second factor still missing. Only /auth/mfa/verify uses this."""
    return await _principal(request, db, allow_mfa_pending=True)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
PendingPrincipal = Annotated[Principal, Depends(pending_principal)]

# Owner powers need a second factor (FR-01): without TOTP enabled these return 403 "mfa_required".
MFA_REQUIRED_PERMISSIONS = frozenset(
    {Permission.MEMBERS_MANAGE, Permission.SETTINGS_MANAGE, Permission.ACCOUNTS_MANAGE, Permission.AUDIT_READ}
)


@dataclass(frozen=True, slots=True)
class WorkspaceAccess:
    principal: Principal
    workspace_id: uuid.UUID
    role: WorkspaceRole


def require_permission(permission: Permission) -> Callable[..., Awaitable[WorkspaceAccess]]:
    """Checks membership of the workspace in the path. Non-members get 404 so ids do not leak."""

    async def dependency(workspace_id: uuid.UUID, principal: CurrentPrincipal, db: Db) -> WorkspaceAccess:
        membership = (
            await db.execute(
                select(Membership).where(
                    Membership.workspace_id == workspace_id, Membership.user_id == principal.user.id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace_not_found")
        if not has_permission(membership.role, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        if permission in MFA_REQUIRED_PERMISSIONS and principal.user.mfa_enabled_at is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "mfa_required")
        return WorkspaceAccess(principal=principal, workspace_id=workspace_id, role=membership.role)

    return dependency
