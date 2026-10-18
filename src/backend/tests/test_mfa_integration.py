"""TOTP enrolment, login step 2, recovery codes and the owner rule. Needs TEST_DATABASE_URL."""

import os

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_integration import HDR, PASSWORD, World, client, login, next_totp, world  # noqa: F401

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

EMAIL = "approver@example.com"


def _enroll(client: TestClient) -> tuple[str, list[str]]:  # noqa: F811
    csrf = login(client, EMAIL)
    h = {**HDR, "X-CSRF-Token": csrf}
    assert (
        client.post("/api/v1/auth/mfa/setup", json={"password": "not the password!"}, headers=h).status_code
        == 400
    )
    setup = client.post("/api/v1/auth/mfa/setup", json={"password": PASSWORD}, headers=h)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    assert client.post("/api/v1/auth/mfa/enable", json={"code": "000000"}, headers=h).status_code in (
        400,
        200,
    )
    res = client.post("/api/v1/auth/mfa/enable", json={"code": next_totp(EMAIL, secret)}, headers=h)
    if res.status_code == 409:  # "000000" happened to be valid
        pytest.skip("random code collision")
    assert res.status_code == 200, res.text
    codes = res.json()["recovery_codes"]
    assert len(codes) == 10
    assert client.get("/api/v1/auth/me").json()["mfa_enabled"] is True
    return secret, codes


def _logout(client: TestClient) -> None:  # noqa: F811
    me = client.get("/api/v1/auth/me").json()
    client.post("/api/v1/auth/logout", headers={**HDR, "X-CSRF-Token": me["csrf_token"]})
    client.cookies.clear()


def _start_login(client: TestClient) -> dict[str, str]:  # noqa: F811
    res = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=HDR)
    assert res.status_code == 200 and res.json()["mfa_required"] is True
    return {**HDR, "X-CSRF-Token": res.json()["csrf_token"]}


def test_enrol_then_login_needs_code(client: TestClient, world: World) -> None:  # noqa: F811
    secret, _ = _enroll(client)
    _logout(client)

    h = _start_login(client)
    assert client.get("/api/v1/auth/me").status_code == 401  # pending session is not a login
    assert client.get(f"/api/v1/workspaces/{world.workspace_id}/members").status_code == 401
    assert client.post("/api/v1/auth/mfa/verify", json={"code": "123456"}, headers=h).status_code in (
        401,
        200,
    )

    code = next_totp(EMAIL, secret)
    res = client.post("/api/v1/auth/mfa/verify", json={"code": code}, headers=h)
    assert res.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200

    # The same code cannot be used twice (replay protection).
    _logout(client)
    h = _start_login(client)
    assert client.post("/api/v1/auth/mfa/verify", json={"code": code}, headers=h).status_code == 401


def test_recovery_code_works_once(client: TestClient) -> None:  # noqa: F811
    _, codes = _enroll(client)
    _logout(client)
    h = _start_login(client)
    assert (
        client.post("/api/v1/auth/mfa/verify", json={"code": codes[0].upper()}, headers=h).status_code == 200
    )
    _logout(client)
    h = _start_login(client)
    assert client.post("/api/v1/auth/mfa/verify", json={"code": codes[0]}, headers=h).status_code == 401


def test_wrong_codes_lock_the_account(client: TestClient) -> None:  # noqa: F811
    secret, _ = _enroll(client)
    _logout(client)
    h = _start_login(client)
    for _ in range(5):
        client.post("/api/v1/auth/mfa/verify", json={"code": "abcdefghij"}, headers=h)
    res = client.post("/api/v1/auth/mfa/verify", json={"code": next_totp(EMAIL, secret)}, headers=h)
    assert res.status_code == 401


def test_disable_needs_password_and_code(client: TestClient) -> None:  # noqa: F811
    secret, _ = _enroll(client)
    h = {**HDR, "X-CSRF-Token": client.get("/api/v1/auth/me").json()["csrf_token"]}
    bad = client.post(
        "/api/v1/auth/mfa/disable",
        json={"password": "not the password!", "code": "000000"},
        headers=h,
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/v1/auth/mfa/disable", json={"password": PASSWORD, "code": next_totp(EMAIL, secret)}, headers=h
    )
    assert ok.status_code == 204
    assert client.get("/api/v1/auth/me").json()["mfa_enabled"] is False


def test_owner_without_second_factor_cannot_manage(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "stranger@example.com")  # owner of the other workspace, no TOTP
    res = client.get(f"/api/v1/workspaces/{world.other_workspace_id}/audit")
    assert res.status_code == 403 and res.json()["detail"] == "mfa_required"
    assert client.get(f"/api/v1/workspaces/{world.other_workspace_id}/members").status_code == 200


def test_owner_with_second_factor_can_manage(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "owner@example.com")
    assert client.get(f"/api/v1/workspaces/{world.workspace_id}/audit").status_code == 200


def test_relogin_does_not_reset_code_guess_counter(client: TestClient) -> None:  # noqa: F811
    _enroll(client)
    _logout(client)
    locked = False
    for _ in range(4):  # 2 wrong codes per login; a reset on re-login would never lock
        res = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=HDR)
        if res.status_code == 401:
            locked = True
            break
        h = {**HDR, "X-CSRF-Token": res.json()["csrf_token"]}
        for _ in range(2):
            client.post("/api/v1/auth/mfa/verify", json={"code": "abcdefghij"}, headers=h)
        client.cookies.clear()
    assert locked


def test_regenerate_codes_needs_password_and_totp(client: TestClient) -> None:  # noqa: F811
    secret, old = _enroll(client)
    h = {**HDR, "X-CSRF-Token": client.get("/api/v1/auth/me").json()["csrf_token"]}
    url = "/api/v1/auth/mfa/recovery-codes"
    assert (
        client.post(url, json={"password": "not the password!", "code": "000000"}, headers=h).status_code
        == 400
    )
    assert client.post(url, json={"password": PASSWORD, "code": old[0]}, headers=h).status_code == 400
    res = client.post(url, json={"password": PASSWORD, "code": next_totp(EMAIL, secret)}, headers=h)
    assert res.status_code == 200 and set(res.json()["recovery_codes"]).isdisjoint(old)
