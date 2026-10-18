"""Password hashing (Argon2id) and password policy."""

import asyncio
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 128  # bounds hashing cost

_hasher = PasswordHasher()  # argon2-cffi defaults follow RFC 9106 recommendations
# Each hash uses ~64 MiB; cap parallel hashing so a login flood cannot exhaust memory.
_HASH_SLOTS = asyncio.Semaphore(4)


class PasswordPolicyError(ValueError):
    pass


def validate_password(password: str, *, email: str | None = None) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_LENGTH} characters.")
    if password.strip() != password:
        raise PasswordPolicyError("Password must not start or end with whitespace.")
    if email and password.lower() == email.lower():
        raise PasswordPolicyError("Password must not equal the e-mail address.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> tuple[bool, bool]:
    """Returns (matches, needs_rehash). Always spends roughly the same time, even without a hash."""
    target = password_hash or _dummy_hash()
    try:
        _hasher.verify(target, password[:MAX_LENGTH])
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, False
    if password_hash is None:
        return False, False
    return True, _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # Used for unknown accounts so response time does not reveal whether an e-mail exists.
    return _hasher.hash("dummy-password-for-timing-only")


async def hash_password_async(password: str) -> str:
    """Runs off the event loop, at most 4 at a time."""
    async with _HASH_SLOTS:
        return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str | None, password: str) -> tuple[bool, bool]:
    async with _HASH_SLOTS:
        return await asyncio.to_thread(verify_password, password_hash, password)
