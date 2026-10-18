"""Security hardening round 1 against a real PostgreSQL. Needs TEST_DATABASE_URL (throwaway database only)."""

# ruff: noqa: F811  # pytest fixtures are imported and then used as test arguments

import asyncio
import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop import media
from heardloop.auth.service import prune_expired_sessions
from heardloop.media_gc import sweep_media
from heardloop.models import (
    AuditLogEntry,
    MediaAsset,
    Membership,
    Notification,
    RecoveryCode,
    User,
    UserSession,
)
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
from tests.test_media_integration import PNG, mc  # noqa: F401
from tests.test_mfa_integration import EMAIL, _enroll, _logout, _start_login
from tests.test_posts_integration import _add_account, _h

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

WRONG = "not the password 123"  # test-only value


def _purge_url(world: World) -> str:
    return f"/api/v1/workspaces/{world.workspace_id}/audit/purge"


def _factory():  # type: ignore[no-untyped-def]
    assert URL
    engine = create_async_engine(URL)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


# ---- B1: purge lockout ----


def test_purge_wrong_passwords_lock_the_account_like_login(client: TestClient, world: World) -> None:
    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    for _ in range(5):
        res = client.post(_purge_url(world), json={"password": WRONG}, headers=h)
        assert res.status_code == 403 and res.json()["detail"] == "invalid_password"
    sixth = client.post(_purge_url(world), json={"password": WRONG}, headers=h)
    assert sixth.status_code == 423 and sixth.json()["detail"] == "account_locked"
    # The right password does not help while locked.
    ok = client.post(_purge_url(world), json={"password": PASSWORD}, headers=h)
    assert ok.status_code == 423

    denied = asyncio.run(
        _query(select(AuditLogEntry.diff_json).where(AuditLogEntry.action == "audit.purge_denied"))
    )
    assert len(denied) == 5 and all(row[0] is None for row in denied)  # no password material in the details
    # Locked at sign-in too (same counter).
    client.cookies.clear()
    res = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}, headers=HDR
    )
    assert res.status_code == 401


def test_purge_success_resets_the_failure_counter(client: TestClient, world: World) -> None:
    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    for _ in range(4):
        assert client.post(_purge_url(world), json={"password": WRONG}, headers=h).status_code == 403
    assert client.post(_purge_url(world), json={"password": PASSWORD}, headers=h).status_code == 200
    for _ in range(4):  # a fresh budget: four more wrong tries still do not lock
        assert client.post(_purge_url(world), json={"password": WRONG}, headers=h).status_code == 403
    assert client.post(_purge_url(world), json={"password": PASSWORD}, headers=h).status_code == 200


# ---- B3: validation errors do not echo input ----


def test_validation_error_does_not_echo_the_password(client: TestClient) -> None:
    secret = "p" * 300
    res = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": secret}, headers=HDR
    )
    assert res.status_code == 422
    assert secret not in res.text
    detail = res.json()["detail"]
    assert detail and all(set(item) <= {"loc", "msg", "type"} for item in detail)
    bad_mail = "definitely-not-an-address-xyz"
    res = client.post("/api/v1/auth/login", json={"email": bad_mail, "password": PASSWORD}, headers=HDR)
    assert res.status_code == 422 and bad_mail not in res.text and PASSWORD not in res.text


# ---- B4: headers and cookie ----


def test_api_responses_carry_the_security_headers(client: TestClient) -> None:
    res = client.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert res.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["x-content-type-options"] == "nosniff"


def test_secure_cookie_uses_the_host_prefix_and_old_name_still_works(client: TestClient) -> None:
    res = client.post(
        "/api/v1/auth/login", json={"email": "viewer@example.com", "password": PASSWORD}, headers=HDR
    )
    assert res.status_code == 200
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("__Host-hl_session=") and cookie.lower().count("hl_session=") == 1
    lowered = cookie.lower()
    assert "secure" in lowered and "path=/" in lowered and "domain" not in lowered and "httponly" in lowered

    token = client.cookies.get("__Host-hl_session")
    assert token
    client.cookies.clear()
    client.cookies.set("hl_session", token)  # a session that was created before the upgrade
    assert client.get("/api/v1/auth/me").status_code == 200


def test_plain_cookie_name_when_not_secure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heardloop.config import get_settings

    monkeypatch.setenv("COOKIE_SECURE", "false")
    get_settings.cache_clear()
    try:
        res = client.post(
            "/api/v1/auth/login", json={"email": "viewer@example.com", "password": PASSWORD}, headers=HDR
        )
        assert res.status_code == 200
        assert res.headers["set-cookie"].startswith("hl_session=")
        assert "__Host-" not in res.headers["set-cookie"]
    finally:
        monkeypatch.delenv("COOKIE_SECURE")
        get_settings.cache_clear()


def test_logout_clears_both_cookie_names(client: TestClient) -> None:
    csrf = login(client, "viewer@example.com")
    res = client.post("/api/v1/auth/logout", headers={**HDR, "X-CSRF-Token": csrf})
    assert res.status_code == 204
    cleared = res.headers.get_list("set-cookie")
    assert any(c.startswith("hl_session=") for c in cleared)
    assert any(c.startswith("__Host-hl_session=") for c in cleared)


# ---- B5: recovery code consumed only on success ----


def test_recovery_code_is_not_burned_by_the_wrong_endpoint(client: TestClient, world: World) -> None:
    _, codes = _enroll(client)
    h = {**HDR, "X-CSRF-Token": client.get("/api/v1/auth/me").json()["csrf_token"]}
    # /recovery-codes accepts TOTP only: a recovery code fails there ...
    res = client.post(
        "/api/v1/auth/mfa/recovery-codes", json={"password": PASSWORD, "code": codes[0]}, headers=h
    )
    assert res.status_code == 400 and res.json()["detail"] == "invalid_code"
    unused = asyncio.run(_query(select(RecoveryCode.used_at).where(RecoveryCode.used_at.is_not(None))))
    assert unused == []  # ... and is still unused
    # ... so it works where it is accepted.
    _logout(client)
    h = _start_login(client)
    assert client.post("/api/v1/auth/mfa/verify", json={"code": codes[0]}, headers=h).status_code == 200
    assert (
        len(asyncio.run(_query(select(RecoveryCode.used_at).where(RecoveryCode.used_at.is_not(None))))) == 1
    )


def test_recovery_code_survives_a_failed_disable_and_then_works(client: TestClient, world: World) -> None:
    _, codes = _enroll(client)
    h = {**HDR, "X-CSRF-Token": client.get("/api/v1/auth/me").json()["csrf_token"]}
    url = "/api/v1/auth/mfa/disable"
    assert client.post(url, json={"password": WRONG, "code": codes[0]}, headers=h).status_code == 400
    assert client.post(url, json={"password": PASSWORD, "code": codes[0]}, headers=h).status_code == 204
    assert client.get("/api/v1/auth/me").json()["mfa_enabled"] is False


# ---- B7: control characters over HTTP ----


def test_control_characters_in_names_are_rejected(client: TestClient, world: World) -> None:
    login(client, "editor@example.com")
    campaigns = f"/api/v1/workspaces/{world.workspace_id}/campaigns"
    assert client.post(campaigns, json={"name": "Launch\r\nBcc: x"}, headers=_h(client)).status_code == 422
    assert (
        client.post(campaigns, json={"name": "Launch", "goal": "a\r\nb"}, headers=_h(client)).status_code
        == 422
    )
    assert (
        client.post(campaigns, json={"name": "Launch", "goal": "a\nb"}, headers=_h(client)).status_code == 201
    )
    client.cookies.clear()
    login(client, "owner@example.com")
    settings = f"/api/v1/workspaces/{world.workspace_id}/settings"
    assert client.patch(settings, json={"name": "Main\x00"}, headers=_h(client)).status_code == 422


# ---- B9: media ----


def test_quota_is_rechecked_after_the_stream_and_the_file_is_removed(
    mc: TestClient,
    world: World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from heardloop.api import routes_media

    original = media.StreamWriter.finish

    def finish_then_fill_quota(self: media.StreamWriter, kind: media.ImageType) -> tuple[str, str]:
        result = original(self, kind)
        # Another upload of the workspace finished meanwhile: the quota is now used up.
        monkeypatch.setattr(routes_media, "WORKSPACE_QUOTA_BYTES", 10)
        return result

    monkeypatch.setattr(media.StreamWriter, "finish", finish_then_fill_quota)
    login(mc, "editor@example.com")
    csrf = mc.get("/api/v1/auth/me").json()["csrf_token"]
    res = mc.post(
        f"/api/v1/workspaces/{world.workspace_id}/media",
        content=PNG,
        headers={**HDR, "X-CSRF-Token": csrf, "Content-Type": "image/png"},
    )
    assert res.status_code == 413 and res.json()["detail"] == "media_quota_exceeded"
    assert list(tmp_path.rglob("*.png")) == []  # file removed
    assert asyncio.run(_query(select(MediaAsset.id))) == []  # no row


def test_media_sweeper_removes_unreferenced_assets_and_orphan_files(
    mc: TestClient,
    world: World,
    tmp_path: Path,
) -> None:
    acc = asyncio.run(_add_account(world))
    login(mc, "editor@example.com")

    def upload() -> dict:  # type: ignore[type-arg]
        res = mc.post(
            f"/api/v1/workspaces/{world.workspace_id}/media",
            content=PNG,
            headers={
                **HDR,
                "X-CSRF-Token": mc.get("/api/v1/auth/me").json()["csrf_token"],
                "Content-Type": "image/png",
            },
        )
        assert res.status_code == 201, res.text
        return res.json()  # type: ignore[no-any-return]

    used, unused = upload(), upload()
    posts = f"/api/v1/workspaces/{world.workspace_id}/posts"
    assert (
        mc.post(
            posts, json={"account_id": str(acc), "body": "x", "media_ids": [used["id"]]}, headers=_h(mc)
        ).status_code
        == 201
    )

    old = utcnow() - timedelta(days=2)

    async def backdate() -> None:
        engine, factory = _factory()
        async with factory() as db:
            await db.execute(update(MediaAsset).values(created_at=old))
            await db.commit()
        await engine.dispose()

    asyncio.run(backdate())
    fresh = upload()  # uploaded just now, nothing refers to it yet

    ws_dir = tmp_path / str(world.workspace_id)
    stale = ws_dir / f"{uuid.uuid4().hex}.png"  # file without a row, 2 days old
    stale_tmp = ws_dir / f"{uuid.uuid4().hex}.tmp"  # leftover of an interrupted upload
    young = ws_dir / f"{uuid.uuid4().hex}.png"  # file without a row, but just written
    for path in (stale, stale_tmp, young):
        path.write_bytes(b"x")
    for path in (stale, stale_tmp):
        os.utime(path, (old.timestamp(), old.timestamp()))

    async def sweep_twice() -> tuple[tuple[int, int], tuple[int, int], list[uuid.UUID]]:
        engine, factory = _factory()
        first = await sweep_media(factory, tmp_path, utcnow)
        async with factory() as db:
            ids = list((await db.execute(select(MediaAsset.id))).scalars())
        again = await sweep_media(factory, tmp_path, utcnow)  # nothing left to do
        await engine.dispose()
        return first, again, ids

    first, again, ids = asyncio.run(sweep_twice())
    assert first == (1, 2) and again == (0, 0)  # one unreferenced asset; stale file + stale tmp
    assert set(ids) == {uuid.UUID(used["id"]), uuid.UUID(fresh["id"])}  # the unused old one is gone
    assert not stale.exists() and not stale_tmp.exists() and young.exists()
    assert len(list(ws_dir.iterdir())) == 3  # referenced + fresh files, and the young stray file
    assert unused["id"] not in {str(i) for i in ids}


# ---- B10: anonymise, session retention ----


def _anonymise_url(world: World, user_id: object) -> str:
    return f"/api/v1/workspaces/{world.workspace_id}/members/{user_id}/anonymise"


def _user_id(email: str) -> uuid.UUID:
    return asyncio.run(_query(select(User.id).where(User.email == email)))[0][0]  # type: ignore[no-any-return]


def test_anonymise_member(client: TestClient, world: World) -> None:
    viewer_id = _user_id("editor@example.com")  # an editor: leaves a session and a logged action behind
    login(client, "editor@example.com")
    campaigns = f"/api/v1/workspaces/{world.workspace_id}/campaigns"
    assert client.post(campaigns, json={"name": "Launch"}, headers=_h(client)).status_code == 201
    client.cookies.clear()
    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    url = _anonymise_url(world, viewer_id)

    assert client.post(url, json={"code": "000000"}, headers=h).status_code in (400, 423)
    owner_id = _user_id("owner@example.com")
    assert (
        client.post(_anonymise_url(world, owner_id), json={"code": "000000"}, headers=h).json()["detail"]
        == "cannot_anonymise_self"
    )
    assert (
        client.post(_anonymise_url(world, uuid.uuid4()), json={"code": "000000"}, headers=h).status_code
        == 404
    )

    res = client.post(url, json={"code": next_totp("owner@example.com")}, headers=h)
    assert res.status_code == 204, res.text

    members = client.get(f"/api/v1/workspaces/{world.workspace_id}/members").json()
    assert viewer_id.hex not in str(members).replace("-", "") and len(members) == 3
    row = asyncio.run(
        _query(
            select(
                User.email,
                User.display_name,
                User.is_active,
                User.deactivated_at,
                User.password_hash,
                User.email_notifications,
                User.ui_prefs,
                User.mfa_enabled_at,
            ).where(User.id == viewer_id)
        )
    )[0]
    assert row.email == f"deleted+{viewer_id}@invalid" and row.display_name == "Former member"
    assert row.is_active is False and row.deactivated_at is not None and row.password_hash is None
    assert row.email_notifications is False and row.ui_prefs == {} and row.mfa_enabled_at is None
    for model, col in (
        (Membership, Membership.user_id),
        (UserSession, UserSession.user_id),
        (Notification, Notification.user_id),
    ):
        assert asyncio.run(_query(select(model).where(col == viewer_id))) == []

    # History still joins: the person's sign-in event shows the neutral name, the anonymisation is logged.
    audit = client.get(f"/api/v1/workspaces/{world.workspace_id}/audit", params={"limit": 200}).json()[
        "items"
    ]
    assert any(e["action"] == "member.anonymised" for e in audit)
    created = [e for e in audit if e["action"] == "campaign.created"]
    assert created and all(e["actor_name"] == "Former member" for e in created)
    assert all(e["actor_name"] != "Editor" for e in audit)

    # Sign-in is impossible, with the old address or the new one.
    client.cookies.clear()
    for email, refused in (("editor@example.com", 401), (f"deleted+{viewer_id}@invalid", 422)):
        # The placeholder address is not even a valid login address, so it never reaches a lookup.
        res = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}, headers=HDR)
        assert res.status_code == refused


def test_anonymise_is_owner_only_scoped_to_the_workspace_and_refuses_shared_accounts(
    client: TestClient,
    world: World,
) -> None:
    viewer_id = _user_id("viewer@example.com")
    login(client, "viewer@example.com")
    assert (
        client.post(_anonymise_url(world, viewer_id), json={"code": "123456"}, headers=_h(client)).status_code
        == 403
    )
    client.cookies.clear()
    login(client, "stranger@example.com")  # owner of another workspace: not a member here
    assert (
        client.post(_anonymise_url(world, viewer_id), json={"code": "123456"}, headers=_h(client)).status_code
        == 404
    )
    assert len(client.get(f"/api/v1/workspaces/{world.other_workspace_id}/members").json()) == 1
    client.cookies.clear()

    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    base = f"/api/v1/workspaces/{world.workspace_id}/members"
    stranger_id = _user_id("stranger@example.com")
    added = client.post(
        base, json={"email": "stranger@example.com", "display_name": "Stranger", "role": "viewer"}, headers=h
    )
    assert added.status_code == 201
    res = client.post(
        _anonymise_url(world, stranger_id), json={"code": next_totp("owner@example.com")}, headers=h
    )
    assert res.status_code == 409 and res.json()["detail"] == "member_in_other_workspaces"
    still = asyncio.run(_query(select(User.is_active, User.display_name).where(User.id == stranger_id)))
    assert still == [(True, "Stranger")]


def test_expired_sessions_are_swept_after_a_day(client: TestClient, world: World) -> None:
    login(client, "viewer@example.com")
    uid = _user_id("viewer@example.com")

    async def go() -> int:
        engine, factory = _factory()
        now = utcnow()
        async with factory() as db:
            for hours in (-48, -12, 24):  # expired 2 days ago, expired 12 h ago, still valid
                db.add(
                    UserSession(
                        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                        user_id=uid,
                        csrf_token="x",  # noqa: S106  # test-only value
                        created_at=now - timedelta(days=3),
                        last_seen_at=now - timedelta(days=3),
                        expires_at=now + timedelta(hours=hours),
                    )
                )
            await db.commit()
        removed = await prune_expired_sessions(factory, now)
        await engine.dispose()
        return removed

    assert asyncio.run(go()) == 1
    assert (
        len(asyncio.run(_query(select(UserSession.id).where(UserSession.user_id == uid)))) == 3
    )  # login + 2


# ---- B11 ----


def test_audit_search_treats_wildcards_literally(client: TestClient, world: World) -> None:
    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    settings = f"/api/v1/workspaces/{world.workspace_id}/settings"
    for name in ("100% done", "Zeta", "1000 done"):
        assert client.patch(settings, json={"name": name}, headers=h).status_code == 200
    audit = f"/api/v1/workspaces/{world.workspace_id}/audit"

    hits = client.get(audit, params={"q": "100%"}).json()["items"]
    assert {e["changes"]["name"]["to"] for e in hits} == {"100% done", "Zeta"}  # "1000 done" is not a match
    assert client.get(audit, params={"q": "Ze_a"}).json()["items"] == []  # "_" is not "any character"
    assert client.get(audit, params={"q": "\\"}).status_code == 200  # a lone backslash is fine
    assert client.get(audit, params={"q": "zeta"}).json()["items"]  # still case-insensitive


def test_password_change_to_the_same_password_is_refused(client: TestClient) -> None:
    h = {**HDR, "X-CSRF-Token": login(client, "approver@example.com")}
    res = client.post(
        "/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD}, headers=h
    )
    assert res.status_code == 400 and res.json()["detail"] == "password_unchanged"
    client.cookies.clear()
    login(client, "approver@example.com")  # unchanged: the old password still works


def test_owner_created_members_start_without_email_notifications(client: TestClient, world: World) -> None:
    h = {**HDR, "X-CSRF-Token": login(client, "owner@example.com")}
    res = client.post(
        f"/api/v1/workspaces/{world.workspace_id}/members",
        json={
            "email": "fresh@example.com",
            "display_name": "Fresh",
            "role": "editor",
            "initial_password": PASSWORD,
        },
        headers=h,
    )
    assert res.status_code == 201
    flag = asyncio.run(_query(select(User.email_notifications).where(User.email == "fresh@example.com")))
    assert flag == [(False,)]
    seeded = asyncio.run(_query(select(User.email_notifications).where(User.email == EMAIL)))
    assert seeded == [(True,)]  # seeded users keep the default
