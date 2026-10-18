"""Auth, sessions, CSRF and workspace permissions against a real PostgreSQL.

Skipped unless TEST_DATABASE_URL is set. Drops and recreates all tables - throwaway databases only.
"""

import asyncio
import base64
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.models import AuditLogEntry, Base, Membership, User, Workspace, WorkspaceRole
from heardloop.security import totp
from heardloop.security.crypto import TokenProtector
from heardloop.security.passwords import hash_password
from heardloop.timeutil import utcnow

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

PASSWORD = "a long enough passphrase"  # noqa: S105  # test-only value
HDR = {"X-Requested-With": "heardloop"}


OWNER_TOTP_SECRET = totp.generate_secret()  # random per test run, never committed
_last_step: dict[str, int] = {}


@dataclass
class World:
    workspace_id: uuid.UUID
    other_workspace_id: uuid.UUID
    token_key: str


def next_totp(email: str, secret: str = OWNER_TOTP_SECRET) -> str:
    """A valid code that was not used before in this test (replay protection rejects reuse)."""
    step = max(totp.time_step(utcnow()), _last_step.get(email, -1) + 1)
    _last_step[email] = step
    return totp.code_at(secret, step)


async def _reset_and_seed(token_key: str) -> World:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    now = utcnow()
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        ws = Workspace(name="Main", created_at=now)
        other = Workspace(name="Other", created_at=now)
        users = {
            role: User(
                email=f"{role.value}@example.com",
                display_name=role.value.title(),
                password_hash=hash_password(PASSWORD),
                created_at=now,
            )
            for role in WorkspaceRole
        }
        stranger = User(
            email="stranger@example.com",
            display_name="Stranger",
            password_hash=hash_password(PASSWORD),
            created_at=now,
        )
        db.add_all([ws, other, stranger, *users.values()])
        await db.flush()
        # Owners need a second factor for management actions: the seeded owner has TOTP enabled.
        owner = users[WorkspaceRole.OWNER]
        owner.mfa_secret_encrypted = TokenProtector(token_key).protect(
            OWNER_TOTP_SECRET, f"user-mfa:{owner.id}"
        )
        owner.mfa_enabled_at = now
        db.add_all([Membership(user_id=u.id, workspace_id=ws.id, role=r) for r, u in users.items()])
        db.add(Membership(user_id=stranger.id, workspace_id=other.id, role=WorkspaceRole.OWNER))
        await db.commit()
        world = World(workspace_id=ws.id, other_workspace_id=other.id, token_key=token_key)
    await engine.dispose()
    return world


async def _drop() -> None:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _query(stmt):  # type: ignore[no-untyped-def]
    assert URL
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    await engine.dispose()
    return rows


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    assert URL
    monkeypatch.setenv("DATABASE_URL", URL)
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", key)
    monkeypatch.setenv("APP_ENV", "production")
    from heardloop.config import get_settings

    get_settings.cache_clear()
    _last_step.clear()
    yield asyncio.run(_reset_and_seed(key))
    asyncio.run(_drop())


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    from heardloop.api.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as c:
        yield c


def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    """Signs in; completes the TOTP step for the seeded owner."""
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=HDR)
    assert res.status_code == 200, res.text
    body = res.json()
    if body.get("mfa_required"):
        h = {**HDR, "X-CSRF-Token": body["csrf_token"]}
        res = client.post("/api/v1/auth/mfa/verify", json={"code": next_totp(email)}, headers=h)
        assert res.status_code == 200, res.text
        body = res.json()
    return str(body["csrf_token"])


def test_login_sets_secure_httponly_cookie(client: TestClient) -> None:
    res = client.post(
        "/api/v1/auth/login", json={"email": "Viewer@Example.com ", "password": PASSWORD}, headers=HDR
    )
    assert res.status_code == 200
    cookie = res.headers["set-cookie"].lower()
    assert (
        "hl_session=" in cookie and "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    )
    body = res.json()
    assert body["user"]["email"] == "viewer@example.com"
    assert body["memberships"][0]["role"] == "viewer"
    assert body["mfa_enabled"] is False


def test_wrong_password_and_unknown_user_look_the_same(client: TestClient) -> None:
    a = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": "x" * 12}, headers=HDR
    )
    b = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x" * 12}, headers=HDR
    )
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "invalid_credentials"}


def test_login_requires_client_header(client: TestClient) -> None:
    res = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert res.status_code == 403


def test_lockout_after_five_failures(client: TestClient) -> None:
    for _ in range(5):
        client.post(
            "/api/v1/auth/login", json={"email": "editor@example.com", "password": "x" * 12}, headers=HDR
        )
    res = client.post(
        "/api/v1/auth/login", json={"email": "editor@example.com", "password": PASSWORD}, headers=HDR
    )
    assert res.status_code == 401
    locked = asyncio.run(_query(select(User.locked_until).where(User.email == "editor@example.com")))
    assert locked[0][0] is not None


def test_me_and_logout_with_csrf(client: TestClient) -> None:
    csrf = login(client, "viewer@example.com")
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout", headers=HDR).status_code == 403  # no CSRF token
    assert client.post("/api/v1/auth/logout", headers={**HDR, "X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_change_password(client: TestClient) -> None:
    csrf = login(client, "approver@example.com")
    h = {**HDR, "X-CSRF-Token": csrf}
    url = "/api/v1/auth/password"
    assert (
        client.post(
            url, json={"current_password": "not-the-current-password", "new_password": "x" * 20}, headers=h
        ).status_code
        == 400
    )
    assert (
        client.post(url, json={"current_password": PASSWORD, "new_password": "short"}, headers=h).status_code
        == 422
    )
    new = "another long passphrase"
    assert (
        client.post(url, json={"current_password": PASSWORD, "new_password": new}, headers=h).status_code
        == 204
    )
    client.cookies.clear()
    bad = client.post(
        "/api/v1/auth/login", json={"email": "approver@example.com", "password": PASSWORD}, headers=HDR
    )
    assert bad.status_code == 401
    login(client, "approver@example.com", new)


def test_non_member_gets_404(client: TestClient, world: World) -> None:
    login(client, "stranger@example.com")
    assert client.get(f"/api/v1/workspaces/{world.workspace_id}/members").status_code == 404
    assert client.get(f"/api/v1/workspaces/{uuid.uuid4()}/members").status_code == 404


def test_viewer_can_read_but_not_manage(client: TestClient, world: World) -> None:
    csrf = login(client, "viewer@example.com")
    members = client.get(f"/api/v1/workspaces/{world.workspace_id}/members")
    assert members.status_code == 200 and len(members.json()) == 4
    res = client.post(
        f"/api/v1/workspaces/{world.workspace_id}/members",
        json={
            "email": "new@example.com",
            "display_name": "New",
            "role": "viewer",
            "initial_password": PASSWORD,
        },
        headers={**HDR, "X-CSRF-Token": csrf},
    )
    assert res.status_code == 403


def test_owner_manages_members_and_last_owner_is_protected(client: TestClient, world: World) -> None:
    csrf = login(client, "owner@example.com")
    h = {**HDR, "X-CSRF-Token": csrf}
    base = f"/api/v1/workspaces/{world.workspace_id}/members"

    added = client.post(
        base,
        json={
            "email": "New@Example.com",
            "display_name": "New",
            "role": "editor",
            "initial_password": PASSWORD,
        },
        headers=h,
    )
    assert added.status_code == 201
    new_id = added.json()["user_id"]
    dup = client.post(
        base, json={"email": "new@example.com", "display_name": "New", "role": "editor"}, headers=h
    )
    assert dup.status_code == 409

    owner_id = next(m["user_id"] for m in client.get(base).json() if m["role"] == "owner")
    assert client.patch(f"{base}/{owner_id}", json={"role": "viewer"}, headers=h).status_code == 409
    assert client.delete(f"{base}/{owner_id}", headers=h).status_code == 409

    assert client.patch(f"{base}/{new_id}", json={"role": "approver"}, headers=h).json()["role"] == "approver"
    assert client.delete(f"{base}/{new_id}", headers=h).status_code == 204

    actions = {row[0] for row in asyncio.run(_query(select(AuditLogEntry.action)))}
    assert {"auth.login", "member.added", "member.role_changed", "member.removed"} <= actions


def test_unknown_fields_are_rejected(client: TestClient) -> None:
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": PASSWORD, "admin": True},
        headers=HDR,
    )
    assert res.status_code == 422


def test_non_ascii_csrf_header_is_rejected_not_500(client: TestClient) -> None:
    login(client, "viewer@example.com")
    bad_token = "\u00e9\u00e9".encode("latin-1")
    res = client.post("/api/v1/auth/logout", headers={**HDR, "X-CSRF-Token": bad_token})
    assert res.status_code == 403


def test_login_again_replaces_the_old_session(client: TestClient) -> None:
    from heardloop.models import UserSession

    login(client, "viewer@example.com")
    login(client, "viewer@example.com")
    assert len(asyncio.run(_query(select(UserSession.id)))) == 1


def test_password_change_guesses_count_towards_lockout(client: TestClient) -> None:
    csrf = login(client, "editor@example.com")
    h = {**HDR, "X-CSRF-Token": csrf}
    for _ in range(5):
        res = client.post(
            "/api/v1/auth/password", json={"current_password": "x" * 12, "new_password": "y" * 20}, headers=h
        )
        if res.status_code != 400:
            break
    # Locked: the session is revoked and the correct password no longer logs in for now.
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()
    res = client.post(
        "/api/v1/auth/login", json={"email": "editor@example.com", "password": PASSWORD}, headers=HDR
    )
    assert res.status_code == 401


def test_ui_preferences_roundtrip(client: TestClient, world: World) -> None:
    csrf = login(client, "viewer@example.com")
    h = {**HDR, "X-CSRF-Token": csrf}
    me = client.get("/api/v1/auth/me").json()
    assert me["ui_prefs"]["theme"] == "system" and me["ui_prefs"]["locale"] is None  # server default language
    prefs = {
        "locale": "he",
        "date_format": "ymd",
        "time_format": "12h",
        "font_size": "l",
        "theme": "dark",
        "accent": "#AA3366",
    }
    saved = client.put("/api/v1/auth/me/preferences", json=prefs, headers=h).json()
    assert saved["accent"] == "#aa3366" and saved["font_size"] == "l"
    assert client.get("/api/v1/auth/me").json()["ui_prefs"]["theme"] == "dark"
    for bad in ({"theme": "neon"}, {"accent": "red"}, {"font_size": "xxl"}, {"locale": "fr"}, {"unknown": 1}):
        assert client.put("/api/v1/auth/me/preferences", json={**prefs, **bad}, headers=h).status_code == 422
