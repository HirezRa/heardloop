"""Signed OAuth `state` bound to the user, the workspace and a browser cookie nonce (anti-CSRF)."""

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

STATE_TTL = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class OAuthState:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    nonce: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def sign_state(key: bytes, state: OAuthState, now: datetime) -> str:
    payload = json.dumps(
        {
            "u": str(state.user_id),
            "w": str(state.workspace_id),
            "n": state.nonce,
            "e": int((now + STATE_TTL).timestamp()),
        },
        separators=(",", ":"),
    ).encode()
    mac = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(mac)}"


def verify_state(key: bytes, token: str, now: datetime, *, cookie_nonce: str | None) -> OAuthState | None:
    """None when the signature, expiry or browser nonce does not match."""
    try:
        payload_b64, mac_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        mac = _unb64(mac_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(mac, hmac.new(key, payload, hashlib.sha256).digest()):
        return None
    try:
        data = json.loads(payload)
        state = OAuthState(uuid.UUID(data["u"]), uuid.UUID(data["w"]), str(data["n"]))
        expires = int(data["e"])
    except (ValueError, KeyError, TypeError):
        return None
    if expires < int(now.timestamp()):
        return None
    if not cookie_nonce or not hmac.compare_digest(cookie_nonce.encode(), state.nonce.encode()):
        return None
    return state
