"""Own sessions, security notices, lockout consistency (#55). Needs TEST_DATABASE_URL (throwaway DB)."""

# ruff: noqa: F811  # pytest fixtures are imported and then used as test arguments

import asyncio
import os
import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine

from heardloop.models import Notification, User, WebhookDelivery
from heardloop.timeutil import utcnow
from tests.test_auth_integration import (  # noqa: F401
    HDR,
    PASSWORD,
    World,
    _query,
    client,
    login,
    next_totp,
    world,
)

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


class _SharedTransport(httpx.BaseTransport):
    """Uses the running app's transport without closing it."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._inner.handle_request(request)

    def close(self) -> None:
        return None


def _device(client: TestClient) -> httpx.Client:
    """Another browser: own cookie jar, same event loop as the running app."""
    return httpx.Client(
        transport=_SharedTransport(client._transport),
        base_url=client.base_url,
        follow_redirects=True,
        trust_env=False,
        headers={"user-agent": "testclient"},
    )


def _h(csrf: str) -> dict[str, str]:
    return {**HDR, "X-CSRF-Token": csrf}


def _user_id(email: str) -> uuid.UUID:
    return asyncio.run(_query(select(User.id).where(User.email == email)))[0][0]  # type: ignore[no-any-return]


def _kinds(user_id: uuid.UUID) -> list[str]:
    return [
        k for (k,) in asyncio.run(_query(select(Notification.kind).where(Notification.user_id == user_id)))
    ]


async def _lock(email: str) -> None:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(
            update(User).where(User.email == email).values(locked_until=utcnow() + timedelta(minutes=10))
        )
    await engine.dispose()


def test_list_and_revoke_own_sessions(client: TestClient, world: World) -> None:
    phone = _device(client)
    login(phone, "editor@example.com")
    csrf = login(client, "editor@example.com")  # this browser

    sessions = client.get("/api/v1/auth/sessions").json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    current = next(s for s in sessions if s["current"])
    assert other["user_agent"] and "ip_address" in other

    assert client.delete(f"/api/v1/auth/sessions/{current['id']}", headers=_h(csrf)).status_code == 409
    assert client.delete(f"/api/v1/auth/sessions/{uuid.uuid4()}", headers=_h(csrf)).status_code == 404
    assert client.delete(f"/api/v1/auth/sessions/{other['id']}", headers=_h(csrf)).status_code == 204
    assert [s["id"] for s in client.get("/api/v1/auth/sessions").json()] == [current["id"]]
    assert "security.sessions_revoked" in _kinds(_user_id("editor@example.com"))

    assert phone.get("/api/v1/auth/me").status_code == 401  # the revoked device is signed out
    assert client.get("/api/v1/auth/me").status_code == 200


def test_cannot_revoke_someone_elses_session(client: TestClient, world: World) -> None:
    viewer = _device(client)
    login(viewer, "viewer@example.com")
    viewer_session = viewer.get("/api/v1/auth/sessions").json()[0]["id"]
    csrf = login(client, "editor@example.com")
    assert client.delete(f"/api/v1/auth/sessions/{viewer_session}", headers=_h(csrf)).status_code == 404
    assert viewer.get("/api/v1/auth/me").status_code == 200  # still signed in


def test_sign_out_other_devices(client: TestClient, world: World) -> None:
    others = [_device(client), _device(client)]
    for device in others:
        login(device, "approver@example.com")
    csrf = login(client, "approver@example.com")
    res = client.post("/api/v1/auth/sessions/revoke-others", headers=_h(csrf))
    assert res.status_code == 200 and res.json() == {"revoked": 2}
    assert len(client.get("/api/v1/auth/sessions").json()) == 1
    assert all(d.get("/api/v1/auth/me").status_code == 401 for d in others)
    again = client.post("/api/v1/auth/sessions/revoke-others", headers=_h(csrf)).json()
    assert again == {"revoked": 0}


def test_password_change_and_mfa_changes_notify_only_the_person(client: TestClient, world: World) -> None:
    csrf = login(client, "editor@example.com")
    editor_id = _user_id("editor@example.com")
    res = client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "another long passphrase"},
        headers=_h(csrf),
    )
    assert res.status_code == 204
    assert _kinds(editor_id) == ["security.password_changed"]
    items = client.get("/api/v1/notifications").json()["items"]
    assert [n["kind"] for n in items] == ["security.password_changed"]
    # Personal: no copy for owners and nothing for the workspace webhook.
    assert _kinds(_user_id("owner@example.com")) == []
    assert asyncio.run(_query(select(WebhookDelivery.id))) == []


def test_locked_account_gets_423_on_password_and_mfa_endpoints(client: TestClient, world: World) -> None:
    csrf = login(client, "editor@example.com")
    asyncio.run(_lock("editor@example.com"))
    h = _h(csrf)
    calls = (
        ("/api/v1/auth/password", {"current_password": PASSWORD, "new_password": "another long passphrase"}),
        ("/api/v1/auth/mfa/setup", {"password": PASSWORD}),
    )
    for url, body in calls:
        res = client.post(url, json=body, headers=h)
        assert res.status_code == 423 and res.json()["detail"] == "account_locked", url


def test_owner_with_mfa_gets_423_on_disable_and_recovery_codes(client: TestClient, world: World) -> None:
    csrf = login(client, "owner@example.com")
    asyncio.run(_lock("owner@example.com"))
    h = _h(csrf)
    for url in ("/api/v1/auth/mfa/disable", "/api/v1/auth/mfa/recovery-codes"):
        res = client.post(url, json={"password": PASSWORD, "code": next_totp("owner@example.com")}, headers=h)
        assert res.status_code == 423, url


def test_new_recovery_codes_are_80_bit_and_work(client: TestClient, world: World) -> None:
    csrf = login(client, "owner@example.com")
    res = client.post(
        "/api/v1/auth/mfa/recovery-codes",
        json={"password": PASSWORD, "code": next_totp("owner@example.com")},
        headers=_h(csrf),
    )
    assert res.status_code == 200, res.text
    codes = res.json()["recovery_codes"]
    assert len(codes) == 10 and all(len(c) == 19 for c in codes)
    assert "security.recovery_codes_regenerated" in _kinds(_user_id("owner@example.com"))

    client.cookies.clear()
    first = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}, headers=HDR
    ).json()
    verified = client.post(
        "/api/v1/auth/mfa/verify", json={"code": codes[0].upper()}, headers=_h(first["csrf_token"])
    )
    assert verified.status_code == 200, verified.text
