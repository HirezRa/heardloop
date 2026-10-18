"""Workspace settings and audit log endpoints (FR-15, FR-25, FR-27). Needs TEST_DATABASE_URL."""

import os

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_integration import HDR, PASSWORD, World, client, login, world  # noqa: F401  # fixtures

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")


def test_members_can_read_settings_but_only_owner_edits(client: TestClient, world: World) -> None:  # noqa: F811
    url = f"/api/v1/workspaces/{world.workspace_id}/settings"
    csrf = login(client, "editor@example.com")
    got = client.get(url).json()
    assert got["time_zone"] == "Asia/Jerusalem" and got["can_edit"] is False
    assert client.patch(url, json={"name": "X"}, headers={**HDR, "X-CSRF-Token": csrf}).status_code == 403

    client.cookies.clear()
    csrf = login(client, "owner@example.com")
    h = {**HDR, "X-CSRF-Token": csrf}
    res = client.patch(
        url, json={"name": " Renamed ", "time_zone": "Europe/London", "require_approval": False}, headers=h
    )
    assert res.status_code == 200
    body = res.json()
    assert (body["name"], body["time_zone"], body["require_approval"], body["can_edit"]) == (
        "Renamed",
        "Europe/London",
        False,
        True,
    )


@pytest.mark.parametrize("payload", [{"time_zone": "Mars/Base"}, {"name": "   "}, {"unknown": 1}])
def test_invalid_settings_rejected(client: TestClient, world: World, payload: dict[str, object]) -> None:  # noqa: F811
    csrf = login(client, "owner@example.com")
    res = client.patch(
        f"/api/v1/workspaces/{world.workspace_id}/settings",
        json=payload,
        headers={**HDR, "X-CSRF-Token": csrf},
    )
    assert res.status_code == 422


def test_audit_is_owner_only(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "approver@example.com")
    assert client.get(f"/api/v1/workspaces/{world.workspace_id}/audit").status_code == 403
    client.cookies.clear()
    login(client, "stranger@example.com")
    assert client.get(f"/api/v1/workspaces/{world.workspace_id}/audit").status_code == 404


def test_audit_lists_workspace_events_newest_first_with_paging(client: TestClient, world: World) -> None:  # noqa: F811
    csrf = login(client, "owner@example.com")
    h = {**HDR, "X-CSRF-Token": csrf}
    url = f"/api/v1/workspaces/{world.workspace_id}/settings"
    for i in range(3):
        client.patch(url, json={"name": f"Name {i}"}, headers=h)

    audit_url = f"/api/v1/workspaces/{world.workspace_id}/audit"
    first = client.get(audit_url, params={"limit": 2}).json()
    assert [e["action"] for e in first["items"]] == ["workspace.settings_changed"] * 2
    assert first["items"][0]["changes"]["name"]["to"] == "Name 2"
    assert first["items"][0]["actor_name"] == "Owner"
    assert first["next_before_id"] is not None

    rest = client.get(audit_url, params={"limit": 50, "before_id": first["next_before_id"]}).json()
    actions = [e["action"] for e in rest["items"]]
    assert actions[0] == "workspace.settings_changed" and "auth.login" in actions
    assert rest["next_before_id"] is None


def test_audit_does_not_leak_other_workspaces(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "stranger@example.com")  # owner of the other workspace; logs auth.login
    client.cookies.clear()
    login(client, "owner@example.com")
    items = client.get(f"/api/v1/workspaces/{world.workspace_id}/audit").json()["items"]
    assert all(e["actor_name"] != "Stranger" for e in items)


def test_audit_filters(client: TestClient, world: World) -> None:  # noqa: F811
    csrf = login(client, "owner@example.com")
    h = {**HDR, "X-CSRF-Token": csrf}
    client.patch(f"/api/v1/workspaces/{world.workspace_id}/settings", json={"name": "Filtered"}, headers=h)
    url = f"/api/v1/workspaces/{world.workspace_id}/audit"

    by_cat = client.get(url, params={"category": "workspace"}).json()["items"]
    assert by_cat and all(e["action"].startswith("workspace.") for e in by_cat)
    assert all(
        e["action"].startswith("auth.") for e in client.get(url, params={"category": "auth"}).json()["items"]
    )

    by_text = client.get(url, params={"q": "filtered"}).json()[
        "items"
    ]  # case-insensitive, searches the details
    assert len(by_text) == 1 and by_text[0]["changes"]["name"]["to"] == "Filtered"

    owner_id = client.get("/api/v1/auth/me").json()["user"]["id"]
    assert client.get(url, params={"actor_id": owner_id}).json()["items"]
    assert client.get(url, params={"actor_id": "00000000-0000-0000-0000-000000000001"}).json()["items"] == []

    assert client.get(url, params={"since": "2099-01-01T00:00:00+00:00"}).json()["items"] == []
    assert client.get(url, params={"until": "2000-01-01T00:00:00+00:00"}).json()["items"] == []
    assert client.get(url, params={"category": "DROP TABLE"}).status_code == 422
    assert client.delete(url, headers=h).status_code == 405  # no blind delete; purge is a guarded action


def test_audit_purge_is_guarded_and_recorded(client: TestClient, world: World) -> None:  # noqa: F811
    csrf = login(client, "owner@example.com")
    h = {**HDR, "X-CSRF-Token": csrf}
    settings_url = f"/api/v1/workspaces/{world.workspace_id}/settings"
    for i in range(2):
        client.patch(settings_url, json={"name": f"N{i}"}, headers=h)
    url = f"/api/v1/workspaces/{world.workspace_id}/audit"
    before = len(client.get(url, params={"category": "workspace"}).json()["items"])
    assert before >= 2

    wrong = client.post(f"{url}/purge", json={"password": "not the password 123"}, headers=h)
    assert wrong.status_code == 403 and wrong.json()["detail"] == "invalid_password"
    assert (
        client.post(
            f"{url}/purge", json={"password": PASSWORD, "before": "2030-01-01T00:00:00"}, headers=h
        ).status_code
        == 422
    )

    res = client.post(f"{url}/purge", json={"password": PASSWORD}, headers=h)
    assert res.status_code == 200 and res.json()["deleted"] >= before
    assert client.get(url, params={"category": "workspace"}).json()["items"] == []
    left = client.get(url, params={"category": "audit"}).json()["items"]
    assert [e["action"] for e in left] == ["audit.purged"]  # the purge itself stays
    assert left[0]["changes"]["deleted"] >= before and left[0]["changes"]["before"] is None
    # Members' sign-in events go too: "everything" means everything the log shows.
    assert client.get(url, params={"category": "auth"}).json()["items"] == []

    client.cookies.clear()
    login(client, "approver@example.com")
    approver_h = {**HDR, "X-CSRF-Token": client.get("/api/v1/auth/me").json()["csrf_token"]}
    assert client.post(f"{url}/purge", json={"password": PASSWORD}, headers=approver_h).status_code == 403
