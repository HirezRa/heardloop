import base64
from datetime import UTC, datetime

import pytest

from heardloop.security import totp

# RFC 6238 appendix B, SHA-1, secret "12345678901234567890"
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    ("unix", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_vectors(unix: int, expected: str) -> None:
    assert totp.code_at(RFC_SECRET, unix // 30, digits=8) == expected


def test_verify_accepts_drift_and_blocks_replay() -> None:
    secret = totp.generate_secret()
    now = datetime(2026, 10, 14, 12, 0, 15, tzinfo=UTC)
    step = totp.time_step(now)
    assert totp.verify(secret, totp.code_at(secret, step - 1), now, last_used_step=None) == step - 1
    assert totp.verify(secret, totp.code_at(secret, step + 1), now, last_used_step=None) == step + 1
    assert totp.verify(secret, totp.code_at(secret, step + 2), now, last_used_step=None) is None
    assert totp.verify(secret, totp.code_at(secret, step), now, last_used_step=step) is None


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef"])
def test_verify_rejects_malformed(code: str) -> None:
    assert totp.verify(totp.generate_secret(), code, datetime.now(UTC), last_used_step=None) is None


def test_verify_strips_spaces_and_rtl_bidi_marks() -> None:
    """Hebrew UI (dir=rtl) browsers often insert LRM/RLM into ltr code fields."""
    secret = totp.generate_secret()
    now = datetime(2026, 10, 14, 12, 0, 15, tzinfo=UTC)
    step = totp.time_step(now)
    good = totp.code_at(secret, step)
    assert totp.verify(secret, f"\u200e{good[:3]} {good[3:]}\u200f", now, last_used_step=None) == step
    assert totp.normalize_recovery_code("\u200eab3dk-9x2mp\u200f") == "ab3dk-9x2mp"


def test_secret_and_uri() -> None:
    secret = totp.generate_secret()
    assert len(secret) == 32 and secret.isalnum()
    uri = totp.provisioning_uri(secret, "user@example.com")
    assert uri.startswith("otpauth://totp/Heardloop%3Auser%40example.com?")
    assert f"secret={secret}" in uri and "issuer=Heardloop" in uri


def test_recovery_codes() -> None:
    codes = totp.generate_recovery_codes()
    assert len(codes) == len(set(codes)) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    pepper = b"p" * 32
    assert totp.hash_recovery_code(codes[0].upper().replace("-", " "), pepper) == totp.hash_recovery_code(
        codes[0], pepper
    )
    assert totp.normalize_recovery_code("bad") == ""
