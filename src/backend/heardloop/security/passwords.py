"""Password hashing (Argon2id) and password policy."""

import asyncio
import re
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 128  # bounds hashing cost

_hasher = PasswordHasher()  # argon2-cffi defaults follow RFC 9106 recommendations
# Each hash uses ~64 MiB; cap parallel hashing so a login flood cannot exhaust memory.
_HASH_SLOTS = asyncio.Semaphore(4)


class PasswordPolicyError(ValueError):
    """`str()` is a sentence for the command line; `code`, when set, is what the API returns."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


TOO_COMMON = "Password is too common or easy to guess. Use a longer phrase of unrelated words."

_COMMON_FILE = Path(__file__).with_name("common_passwords.txt")
_EDGE = "0123456789!@#$%^&*()_+-=.,;:?~ "  # what people add around a word to pass a length rule
_LEET = str.maketrans(
    {"@": "a", "4": "a", "3": "e", "1": "i", "!": "i", "0": "o", "$": "s", "5": "s", "7": "t"}
)
_SEQUENCES = (
    "01234567890123456789",
    "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
    "qwertyuiopasdfghjklzxcvbnmqwertyuiopasdfghjklzxcvbnm",
    "1qaz2wsx3edc4rfv5tgb6yhn7ujm8ik9ol0p1qaz2wsx3edc4rfv",
)


@lru_cache(maxsize=1)
def _common() -> frozenset[str]:
    """Bundled list (lower-case letters/digits); offline, loaded once."""
    words = set()
    for line in _COMMON_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.add(line)
    return frozenset(words)


def is_too_common(password: str, *, email: str | None = None) -> bool:
    """True for listed passwords and for a listed word (or the e-mail name) padded with digits/symbols.

    Examples refused: "password12345", "Summer2026!!!", "P@ssw0rd2026!", "123456789012", "qwertyuiop12".
    Unrelated words ("correct horse battery") are fine.
    """
    lower = password.lower()
    compact = re.sub(r"[^a-z0-9]", "", lower)
    common = _common()
    if compact in common:
        return True
    cores = {compact.strip("0123456789"), lower.strip(_EDGE).translate(_LEET)}
    cores = {re.sub(r"[^a-z0-9]", "", c) for c in cores}
    local = (email or "").lower().split("@", 1)[0]
    local = re.sub(r"[^a-z0-9]", "", local)
    for core in cores:
        if len(core) >= 4 and core in common:
            return True
        if len(local) >= 3 and core == local:
            return True
    if len(set(lower)) < 5:  # "aaaaaaaaaaaa", "abababababab"
        return True
    return any(compact in seq or compact in seq[::-1] for seq in _SEQUENCES)


def validate_password(password: str, *, email: str | None = None) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_LENGTH} characters.")
    if password.strip() != password:
        raise PasswordPolicyError("Password must not start or end with whitespace.")
    if email and password.lower() == email.lower():
        raise PasswordPolicyError("Password must not equal the e-mail address.")
    if is_too_common(password, email=email):
        raise PasswordPolicyError(TOO_COMMON, code="password_too_common")


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
