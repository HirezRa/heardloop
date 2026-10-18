"""AES-256-GCM encryption for LinkedIn tokens.

Format: base64( version:1 | nonce:12 | ciphertext+tag ). The record id is bound as associated data,
so a ciphertext copied to another row will not decrypt.
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_FORMAT_VERSION = 1
_NONCE_SIZE = 12
_TAG_SIZE = 16
_KEY_SIZE = 32


class TokenDecryptionError(Exception):
    """Raised for any malformed, tampered or wrong-context ciphertext."""


def _decode_key(key_b64: str) -> bytes:
    try:
        key = base64.b64decode(key_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("HEARDLOOP_TOKEN_KEY must be Base64 of exactly 32 bytes.") from exc
    if len(key) != _KEY_SIZE:
        raise ValueError("HEARDLOOP_TOKEN_KEY must be Base64 of exactly 32 bytes.")
    return key


def _aad(context: str) -> bytes:
    if not context:
        raise ValueError("context is required")
    return f"heardloop:v1:{context}".encode()


class TokenProtector:
    def __init__(self, key_b64: str) -> None:
        self._aead = AESGCM(_decode_key(key_b64))

    def __repr__(self) -> str:  # never expose key material
        return "TokenProtector(<redacted>)"

    def protect(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(_NONCE_SIZE)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), _aad(context))
        return base64.b64encode(bytes([_FORMAT_VERSION]) + nonce + sealed).decode("ascii")

    def unprotect(self, protected: str, context: str) -> str:
        try:
            raw = base64.b64decode(protected, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TokenDecryptionError("Protected value is not valid Base64.") from exc
        if len(raw) < 1 + _NONCE_SIZE + _TAG_SIZE or raw[0] != _FORMAT_VERSION:
            raise TokenDecryptionError("Unsupported protected value format.")
        nonce, sealed = raw[1 : 1 + _NONCE_SIZE], raw[1 + _NONCE_SIZE :]
        try:
            return self._aead.decrypt(nonce, sealed, _aad(context)).decode()
        except InvalidTag as exc:
            raise TokenDecryptionError("Decryption failed.") from exc


def derive_key(key_b64: str, purpose: str) -> bytes:
    """Separate sub-key per purpose from the master key (HMAC-SHA256), e.g. the recovery-code pepper."""
    import hashlib
    import hmac

    return hmac.new(_decode_key(key_b64), f"heardloop:{purpose}".encode(), hashlib.sha256).digest()
