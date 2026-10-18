"""Refuse to run with a different HEARDLOOP_TOKEN_KEY than the one the data was encrypted with.

Everything encrypted (LinkedIn tokens, TOTP secrets, recovery-code hashes) depends on this key.
If it changes - for example because a shell variable overrides .env when `docker compose up` runs -
the app must stop with a clear error instead of silently failing to decrypt.
The fingerprint is an HMAC of a fixed label, so it does not reveal the key.
"""

import asyncio
import hmac
import logging

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop.models import AppMeta
from heardloop.security.crypto import derive_key
from heardloop.timeutil import utcnow

META_KEY = "token_key_fingerprint"
KEYCHECK_ATTEMPTS = 5
KEYCHECK_RETRY_SECONDS = 2.0
log = logging.getLogger("heardloop.keycheck")


class TokenKeyMismatchError(RuntimeError):
    pass


class TokenKeyUnverifiedError(RuntimeError):
    """Raised when the database stays unreachable after all key-check retries."""


def fingerprint(key_b64: str) -> str:
    return derive_key(key_b64, "key-fingerprint").hex()[:32]


async def ensure_token_key(factory: async_sessionmaker[AsyncSession], key_b64: str) -> None:
    """Stores the fingerprint on first start; afterwards refuses to run with a different key.

    If the database is not reachable, retries a few times and then fails startup - never continues
    with an unverified key.
    """
    current = fingerprint(key_b64)
    last_exc: BaseException | None = None
    for attempt in range(1, KEYCHECK_ATTEMPTS + 1):
        try:
            await _check(factory, current)
            return
        except TokenKeyMismatchError:
            raise
        except (DBAPIError, OSError) as exc:
            last_exc = exc
            log.warning(
                "Could not verify HEARDLOOP_TOKEN_KEY fingerprint (database not ready)",
                extra={"attempt": attempt, "of": KEYCHECK_ATTEMPTS},
                exc_info=True,
            )
            if attempt < KEYCHECK_ATTEMPTS:
                await asyncio.sleep(KEYCHECK_RETRY_SECONDS)
    raise TokenKeyUnverifiedError(
        f"Could not verify HEARDLOOP_TOKEN_KEY: database unreachable after {KEYCHECK_ATTEMPTS} attempts"
    ) from last_exc


async def _check(factory: async_sessionmaker[AsyncSession], current: str) -> None:
    async with factory() as db:
        row = await db.get(AppMeta, META_KEY)
        if row is None:
            db.add(AppMeta(key=META_KEY, value=current, updated_at=utcnow()))
            try:
                await db.commit()
                return
            except IntegrityError:  # api and worker started at the same moment
                await db.rollback()
                row = (await db.execute(select(AppMeta).where(AppMeta.key == META_KEY))).scalar_one()
        if not hmac.compare_digest(row.value, current):
            raise TokenKeyMismatchError(
                "HEARDLOOP_TOKEN_KEY does not match the key this database was set up with. "
                "Check .env and that no shell variable named HEARDLOOP_TOKEN_KEY overrides it "
                "(PowerShell: Remove-Item Env:HEARDLOOP_TOKEN_KEY), then restart. "
                "Fingerprints: expected " + row.value[:8] + "..., got " + current[:8] + "..."
            )
