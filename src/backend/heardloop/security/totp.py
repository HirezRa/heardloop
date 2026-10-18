"""TOTP (RFC 6238, HMAC-SHA1, 6 digits, 30 s) - the format every authenticator app supports."""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
SECRET_BYTES = 20  # 160 bits, as recommended by RFC 4226
WINDOW = 1  # accept the previous/next 30 s step for clock drift


def generate_secret() -> str:
    """Base32 without padding - what authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _key(secret_b32: str) -> bytes:
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    return base64.b32decode(padded, casefold=True)


def time_step(now: datetime) -> int:
    return int(now.timestamp()) // STEP_SECONDS


def code_at(secret_b32: str, step: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret_b32), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def _ascii_digits(code: str) -> str:
    """Keep ASCII digits only — drops spaces and RTL bidi marks browsers insert in dir=rtl UIs."""
    return "".join(ch for ch in code if ch.isascii() and ch.isdigit())


def verify(secret_b32: str, code: str, now: datetime, *, last_used_step: int | None) -> int | None:
    """Returns the matched time step, or None.

    A step at or before `last_used_step` is rejected, so a code cannot be replayed.
    """
    code = _ascii_digits(code)
    if len(code) != DIGITS:
        return None
    current = time_step(now)
    matched: int | None = None
    for step in range(current - WINDOW, current + WINDOW + 1):
        if hmac.compare_digest(code_at(secret_b32, step), code) and matched is None:
            matched = step
    if matched is None or (last_used_step is not None and matched <= last_used_step):
        return None
    return matched


def provisioning_uri(secret_b32: str, account: str, issuer: str = "Heardloop") -> str:
    label = quote(f"{issuer}:{account}")
    query = urlencode({"secret": secret_b32, "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{query}"


def generate_recovery_codes(count: int = 10) -> list[str]:
    """Format xxxxx-xxxxx (base32, ~50 bits each). Shown once; only hashes are stored."""
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"  # no 0/o/1/l confusion
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def normalize_recovery_code(code: str) -> str:
    raw = "".join(ch for ch in code.lower() if ch.isascii() and ch.isalnum())
    return f"{raw[:5]}-{raw[5:]}" if len(raw) == 10 else ""


def hash_recovery_code(code: str, pepper: bytes) -> str:
    """HMAC with a server-side key: a leaked DB alone is not enough to brute-force the codes."""
    return hmac.new(pepper, normalize_recovery_code(code).encode(), hashlib.sha256).hexdigest()
