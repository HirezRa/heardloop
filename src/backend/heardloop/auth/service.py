"""Login, lockout and server-side sessions."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop.models import User, UserSession
from heardloop.security.passwords import hash_password_async, verify_password_async

MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)
SEEN_UPDATE_EVERY = timedelta(minutes=5)


class AuthenticationError(Exception):
    """Generic failure. Never tells the caller whether the e-mail exists or the account is locked."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    session: UserSession
    token: str  # goes into the cookie only; never logged or stored in plain text


def register_failure(user: User, now: datetime) -> None:
    """Counts a wrong password; locks the account after MAX_FAILED_LOGINS. Caller holds the row lock."""
    user.failed_login_count += 1
    if user.failed_login_count >= MAX_FAILED_LOGINS:
        user.locked_until = now + LOCKOUT
        user.failed_login_count = 0


async def authenticate(db: AsyncSession, email: str, password: str, now: datetime) -> User:
    # FOR UPDATE: parallel guesses for one account are serialized, so the lockout counter cannot be raced.
    user = (
        await db.execute(select(User).where(User.email == normalize_email(email)).with_for_update())
    ).scalar_one_or_none()
    matches, needs_rehash = await verify_password_async(user.password_hash if user else None, password)

    if user is None or not user.is_active:
        raise AuthenticationError

    if user.locked_until is not None and user.locked_until > now:
        raise AuthenticationError

    if not matches:
        register_failure(user, now)
        await db.commit()
        raise AuthenticationError

    if user.mfa_enabled_at is None:
        user.failed_login_count = 0
        user.locked_until = None
    # With TOTP the counter is cleared only after a correct second factor (routes_mfa.verify);
    # otherwise re-entering the password would reset it and allow unlimited code guesses.
    if needs_rehash:
        user.password_hash = await hash_password_async(password)
    return user


async def create_session(
    db: AsyncSession,
    user: User,
    now: datetime,
    *,
    absolute: timedelta,
    ip_address: str | None,
    user_agent: str | None,
    mfa_pending: bool = False,
) -> LoginResult:
    token = secrets.token_urlsafe(32)
    session = UserSession(
        token_hash=hash_token(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        mfa_pending=mfa_pending,
        created_at=now,
        last_seen_at=now,
        expires_at=now + absolute,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:256] or None,
    )
    db.add(session)
    await db.commit()
    return LoginResult(user=user, session=session, token=token)


async def resolve_session(
    db: AsyncSession, token: str, now: datetime, *, idle: timedelta
) -> tuple[UserSession, User] | None:
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == hash_token(token))
        )
    ).one_or_none()
    if row is None:
        return None
    session, user = row
    if session.expires_at <= now or session.last_seen_at + idle <= now or not user.is_active:
        await db.delete(session)
        await db.commit()
        return None
    if now - session.last_seen_at >= SEEN_UPDATE_EVERY:
        session.last_seen_at = now
        await db.commit()
    return session, user


async def revoke_session(db: AsyncSession, session_id: uuid.UUID, *, commit: bool = True) -> None:
    await db.execute(delete(UserSession).where(UserSession.id == session_id))
    if commit:
        await db.commit()


async def revoke_other_sessions(db: AsyncSession, user_id: uuid.UUID, keep: uuid.UUID | None) -> None:
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if keep is not None:
        stmt = stmt.where(UserSession.id != keep)
    await db.execute(stmt)


async def revoke_by_token(db: AsyncSession, token: str) -> None:
    await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))


SESSION_RETENTION = timedelta(days=1)  # expired sessions are kept this long, then swept


async def prune_expired_sessions(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """Worker, hourly: deletes sessions that expired more than a day ago (they are useless but pile up)."""
    async with factory() as db:
        result = await db.execute(
            delete(UserSession)
            .where(UserSession.expires_at < now - SESSION_RETENTION)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
