"""Second factor: TOTP enrolment, login step 2, recovery codes (issue #6)."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select

from heardloop import audit
from heardloop.api.deps import (
    CurrentPrincipal,
    Db,
    PendingPrincipal,
    client_ip,
    require_client_header,
)
from heardloop.api.routes_auth import _me, set_session_cookie
from heardloop.api.schemas import (
    MeOut,
    MfaCodeRequest,
    MfaDisableRequest,
    MfaPasswordRequest,
    MfaSetupOut,
    RecoveryCodesOut,
)
from heardloop.auth.service import create_session, register_failure, revoke_other_sessions, revoke_session
from heardloop.config import get_settings
from heardloop.models import RecoveryCode, User
from heardloop.security import totp
from heardloop.security.crypto import TokenDecryptionError, TokenProtector, derive_key
from heardloop.security.passwords import verify_password_async
from heardloop.timeutil import utcnow

log = logging.getLogger("heardloop.auth")
router = APIRouter(prefix="/api/v1/auth/mfa", tags=["mfa"], dependencies=[Depends(require_client_header)])


def _protector(request: Request) -> TokenProtector:
    protector: TokenProtector = request.app.state.token_protector
    return protector


def _context(user: User) -> str:
    return f"user-mfa:{user.id}"


def _pepper() -> bytes:
    return derive_key(get_settings().token_key.get_secret_value(), "recovery-codes")


def _mfa_secret(request: Request, user: User) -> str | None:
    """Decrypt the TOTP secret, or None if the ciphertext no longer matches TOKEN_KEY."""
    if not user.mfa_secret_encrypted:
        return None
    try:
        return _protector(request).unprotect(user.mfa_secret_encrypted, _context(user))
    except TokenDecryptionError:
        log.error("MFA secret decryption failed (TOKEN_KEY mismatch?)", extra={"user_id": str(user.id)})
        return None


async def _locked_user(db: Db, user_id: object) -> User:
    return (await db.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one()


@dataclass(frozen=True, slots=True)
class SecondFactor:
    method: str  # "totp" | "recovery"
    recovery: RecoveryCode | None = None  # the matching unused row; NOT yet consumed


def consume_recovery(factor: SecondFactor, now: datetime) -> None:
    """Burns a recovery code. Call it only on the success path, in the transaction that commits."""
    if factor.recovery is not None:
        factor.recovery.used_at = now


async def _check_second_factor(db: Db, request: Request, user: User, code: str) -> SecondFactor | None:
    """Finds out which factor `code` is. Caller holds the user row lock and commits.

    A recovery code is only *found* here, never marked used: a request that fails for another reason
    (wrong endpoint, wrong password) must not burn it. A TOTP step is recorded on `user` as before.
    """
    now = utcnow()
    secret = _mfa_secret(request, user)
    if secret is not None:
        step = totp.verify(secret, code, now, last_used_step=user.mfa_last_used_step)
        if step is not None:
            user.mfa_last_used_step = step
            return SecondFactor("totp")
    normalized = totp.normalize_recovery_code(code)
    if normalized:
        row = (
            await db.execute(
                select(RecoveryCode).where(
                    RecoveryCode.user_id == user.id,
                    RecoveryCode.code_hash == totp.hash_recovery_code(normalized, _pepper()),
                    RecoveryCode.used_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return SecondFactor("recovery", row)
    return None


async def confirm_second_factor(db: Db, request: Request, user_id: object, code: str) -> User:
    """Step-up check for sensitive owner actions (no password): a TOTP or recovery code of the caller.

    Locks the caller's row; wrong codes count toward the sign-in lockout and a locked account gets 423.
    A recovery code is consumed here, so call this only once the request is otherwise allowed - the code
    is burned only if the transaction is committed, and the caller commits on success.
    """
    user = await _locked_user(db, user_id)
    now = utcnow()
    if user.locked_until is not None and user.locked_until > now:
        raise HTTPException(status.HTTP_423_LOCKED, "account_locked")
    factor = await _check_second_factor(db, request, user, code)
    if factor is None:
        raise await _fail(db, user)
    consume_recovery(factor, now)
    return user


async def _fail(db: Db, user: User, detail: str = "invalid_code") -> HTTPException:
    """Counts a wrong password/code on a sensitive MFA action; locks and signs out after 5."""
    register_failure(user, utcnow())
    if user.locked_until is not None:
        await revoke_other_sessions(db, user.id, keep=None)
    await db.commit()
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail)


async def _check_password(db: Db, user: User, password: str) -> None:
    ok, _ = await verify_password_async(user.password_hash, password)
    if not ok:
        raise await _fail(db, user, "invalid_password")


@router.post("/verify")
async def verify(
    body: MfaCodeRequest, principal: PendingPrincipal, request: Request, response: Response, db: Db
) -> MeOut:
    """Login step 2. On success the pending session is replaced by a new full session (new token)."""
    now = utcnow()
    ip = client_ip(request)
    user = await _locked_user(db, principal.user.id)
    if user.locked_until is not None and user.locked_until > now:
        await revoke_session(db, principal.session.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")

    factor = await _check_second_factor(db, request, user, body.code)
    if factor is None:
        register_failure(user, now)
        audit.record(
            db,
            action="auth.mfa_failed",
            entity="user",
            entity_id=user.id,
            actor_id=user.id,
            now=now,
            ip_address=ip,
        )
        if user.locked_until is not None:
            await revoke_other_sessions(db, user.id, keep=None)
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_code")

    consume_recovery(factor, now)  # same commit as the new session
    user.failed_login_count = 0
    await revoke_session(db, principal.session.id, commit=False)  # one commit below: no half-done login
    absolute = timedelta(days=get_settings().session_absolute_days)
    result = await create_session(
        db, user, now, absolute=absolute, ip_address=ip, user_agent=request.headers.get("user-agent")
    )
    audit.record(
        db,
        action="auth.login",
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        now=now,
        ip_address=ip,
        changes={"second_factor": factor.method},
    )
    await db.commit()
    set_session_cookie(response, result.token, absolute)
    return await _me(db, user, result.session.csrf_token)


@router.post("/setup")
async def setup(
    body: MfaPasswordRequest, principal: CurrentPrincipal, request: Request, db: Db
) -> MfaSetupOut:
    """Starts enrolment: a new secret is stored (encrypted) but not active until /enable succeeds.

    Needs the password, so a stolen session alone cannot bind the attacker's authenticator.
    """
    user = await _locked_user(db, principal.user.id)
    if user.mfa_enabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_already_enabled")
    await _check_password(db, user, body.password)
    secret = totp.generate_secret()
    user.mfa_secret_encrypted = _protector(request).protect(secret, _context(user))
    user.mfa_last_used_step = None
    await db.commit()
    return MfaSetupOut(secret=secret, otpauth_uri=totp.provisioning_uri(secret, user.email))


async def _new_recovery_codes(db: Db, user: User) -> list[str]:
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = totp.generate_recovery_codes()
    pepper = _pepper()
    db.add_all([RecoveryCode(user_id=user.id, code_hash=totp.hash_recovery_code(c, pepper)) for c in codes])
    return codes


@router.post("/enable")
async def enable(
    body: MfaCodeRequest, principal: CurrentPrincipal, request: Request, db: Db
) -> RecoveryCodesOut:
    user = await _locked_user(db, principal.user.id)
    if user.mfa_enabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_already_enabled")
    if not user.mfa_secret_encrypted:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_setup_required")
    now = utcnow()
    secret = _mfa_secret(request, user)
    if secret is None:
        user.mfa_secret_encrypted = None
        user.mfa_last_used_step = None
        await db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_setup_required")
    step = totp.verify(secret, body.code, now, last_used_step=None)
    if step is None:
        raise await _fail(db, user)
    user.mfa_enabled_at = now
    user.mfa_last_used_step = step
    codes = await _new_recovery_codes(db, user)
    await revoke_other_sessions(db, user.id, keep=principal.session.id)
    audit.record(
        db,
        action="auth.mfa_enabled",
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        now=now,
        ip_address=client_ip(request),
    )
    await db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/recovery-codes")
async def regenerate_recovery_codes(
    body: MfaDisableRequest, principal: CurrentPrincipal, request: Request, db: Db
) -> RecoveryCodesOut:
    """Needs password + a TOTP code (not a recovery code); failures count toward the lockout."""
    user = await _locked_user(db, principal.user.id)
    if user.mfa_enabled_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_not_enabled")
    await _check_password(db, user, body.password)
    factor = await _check_second_factor(db, request, user, body.code)
    if factor is None or factor.method != "totp":
        raise await _fail(db, user)  # a recovery code posted here stays unused
    codes = await _new_recovery_codes(db, user)
    audit.record(
        db,
        action="auth.recovery_codes_regenerated",
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        now=utcnow(),
        ip_address=client_ip(request),
    )
    await db.commit()
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable(body: MfaDisableRequest, principal: CurrentPrincipal, request: Request, db: Db) -> None:
    user = await _locked_user(db, principal.user.id)
    if user.mfa_enabled_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "mfa_not_enabled")
    now = utcnow()
    await _check_password(db, user, body.password)
    factor = await _check_second_factor(db, request, user, body.code)
    if factor is None:
        raise await _fail(db, user)
    consume_recovery(factor, now)
    user.mfa_enabled_at = None
    user.mfa_secret_encrypted = None
    user.mfa_last_used_step = None
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await revoke_other_sessions(db, user.id, keep=principal.session.id)
    audit.record(
        db,
        action="auth.mfa_disabled",
        entity="user",
        entity_id=user.id,
        actor_id=user.id,
        now=now,
        ip_address=client_ip(request),
    )
    await db.commit()
