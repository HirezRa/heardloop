"""LinkedIn connect flow against a mocked LinkedIn. Needs TEST_DATABASE_URL."""

import asyncio
import os
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from heardloop.models import LinkedInAccount
from tests.test_auth_integration import HDR, World, _query, login, world  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

FAKE_TOKEN = "fake-linkedin-access-token"  # noqa: S105  # test-only


def _linkedin(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/accessToken"):
        return httpx.Response(
            200,
            json={
                "access_token": FAKE_TOKEN,
                "expires_in": 5184000,
                "scope": "openid,profile,email,w_member_social",
            },
        )
    if req.url.path.endswith("/userinfo"):
        return httpx.Response(200, json={"sub": "member123", "name": "Example Member"})
    return httpx.Response(404)


@pytest.fixture
def li(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:  # noqa: F811
    monkeypatch.setenv("LINKEDIN_CLIENT_ID", "client-id-example")
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "client-secret-example")
    monkeypatch.setenv("LINKEDIN_REDIRECT_URI", "https://testserver/api/v1/linkedin/callback")
    from heardloop.config import get_settings

    get_settings.cache_clear()
    from heardloop.api.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as c:
        asyncio.run(c.app.state.http.aclose())  # type: ignore[attr-defined]
        c.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(_linkedin))  # type: ignore[attr-defined]
        yield c


def _connect(c: TestClient, world: World) -> str:  # noqa: F811
    csrf = login(c, "owner@example.com")
    res = c.post(
        f"/api/v1/workspaces/{world.workspace_id}/linkedin/connect", headers={**HDR, "X-CSRF-Token": csrf}
    )
    assert res.status_code == 200, res.text
    return parse_qs(urlparse(res.json()["authorization_url"]).query)["state"][0]


def test_full_connect_flow(li: TestClient, world: World) -> None:  # noqa: F811
    state = _connect(li, world)
    res = li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/?section=accounts&linkedin=connected"

    accounts = li.get(f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts").json()
    assert len(accounts) == 1
    acc = accounts[0]
    assert acc["urn"] == "urn:li:person:member123" and acc["status"] == "active" and not acc["expiring_soon"]
    assert "token" not in str(acc).lower()

    stored = asyncio.run(_query(select(LinkedInAccount.access_token_encrypted)))[0][0]
    assert stored and FAKE_TOKEN not in stored  # encrypted at rest

    # Reconnecting updates the same account instead of adding a second one.
    state = _connect(li, world)
    li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    assert len(li.get(f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts").json()) == 1


def test_callback_rejects_bad_state(li: TestClient, world: World) -> None:  # noqa: F811
    _connect(li, world)
    res = li.get(
        "/api/v1/linkedin/callback", params={"code": "abc", "state": "forged.state"}, follow_redirects=False
    )
    assert res.headers["location"].endswith("reason=invalid_state")


def test_callback_without_nonce_cookie_is_rejected(li: TestClient, world: World) -> None:  # noqa: F811
    state = _connect(li, world)
    li.cookies.delete("hl_oauth", path="/api/v1/linkedin/callback")
    res = li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    assert res.headers["location"].endswith("reason=invalid_state")


def test_user_cancel(li: TestClient, world: World) -> None:  # noqa: F811
    _connect(li, world)
    res = li.get(
        "/api/v1/linkedin/callback", params={"error": "user_cancelled_authorize"}, follow_redirects=False
    )
    assert "linkedin=cancelled" in res.headers["location"]


def test_only_owner_can_connect(li: TestClient, world: World) -> None:  # noqa: F811
    csrf = login(li, "editor@example.com")
    res = li.post(
        f"/api/v1/workspaces/{world.workspace_id}/linkedin/connect", headers={**HDR, "X-CSRF-Token": csrf}
    )
    assert res.status_code == 403
    assert li.get(f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts").status_code == 200


def test_disconnect_forgets_token(li: TestClient, world: World) -> None:  # noqa: F811
    state = _connect(li, world)
    li.get("/api/v1/linkedin/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    base = f"/api/v1/workspaces/{world.workspace_id}/linkedin/accounts"
    acc_id = li.get(base).json()[0]["id"]
    csrf = li.get("/api/v1/auth/me").json()["csrf_token"]
    revoked: list[str] = []

    def _revoke_aware(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/revoke"):
            revoked.append(req.content.decode())
            return httpx.Response(200)
        return _linkedin(req)

    li.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(_revoke_aware))  # type: ignore[attr-defined]
    assert li.delete(f"{base}/{acc_id}", headers={**HDR, "X-CSRF-Token": csrf}).status_code == 204
    assert len(revoked) == 1 and FAKE_TOKEN in revoked[0]  # LinkedIn was asked to revoke the real token
    assert li.get(base).json()[0]["status"] == "revoked"
    assert asyncio.run(_query(select(LinkedInAccount.access_token_encrypted)))[0][0] == ""


def test_connect_without_configuration_is_503(client: TestClient, world: World) -> None:  # noqa: F811
    csrf = login(client, "owner@example.com")
    res = client.post(
        f"/api/v1/workspaces/{world.workspace_id}/linkedin/connect", headers={**HDR, "X-CSRF-Token": csrf}
    )
    assert res.status_code == 503


from tests.test_auth_integration import client  # noqa: E402, F401
