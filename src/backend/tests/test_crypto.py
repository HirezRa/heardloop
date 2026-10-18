import base64
import os

import pytest

from heardloop.security.crypto import TokenDecryptionError, TokenProtector


def _protector() -> TokenProtector:
    return TokenProtector(base64.b64encode(os.urandom(32)).decode())


def test_roundtrip() -> None:
    p = _protector()
    sealed = p.protect("token-value-placeholder", "account-1")
    assert "token-value-placeholder" not in sealed
    assert p.unprotect(sealed, "account-1") == "token-value-placeholder"


def test_nonce_makes_ciphertext_unique() -> None:
    p = _protector()
    assert p.protect("abc", "ctx") != p.protect("abc", "ctx")


def test_wrong_context_fails() -> None:
    p = _protector()
    with pytest.raises(TokenDecryptionError):
        p.unprotect(p.protect("abc", "account-1"), "account-2")


def test_tampered_ciphertext_fails() -> None:
    p = _protector()
    raw = bytearray(base64.b64decode(p.protect("abc", "ctx")))
    raw[-1] ^= 0xFF
    with pytest.raises(TokenDecryptionError):
        p.unprotect(base64.b64encode(bytes(raw)).decode(), "ctx")


@pytest.mark.parametrize("bad", ["not-base64!", "AAAA", ""])
def test_bad_ciphertext_fails(bad: str) -> None:
    with pytest.raises(TokenDecryptionError):
        _protector().unprotect(bad, "ctx")


@pytest.mark.parametrize("key", ["", "not-base64!", base64.b64encode(b"short").decode()])
def test_invalid_key_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        TokenProtector(key)


def test_repr_hides_key() -> None:
    assert "redacted" in repr(_protector())
