"""Campaigns, calendar and rescheduling. Needs TEST_DATABASE_URL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from heardloop.models import AuditLogEntry
from tests.test_auth_integration import World, _query, client, login, world  # noqa: F401
from tests.test_posts_integration import _add_account, _base, _create, _h, _set_require_approval

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _campaigns(world: World) -> str:  # noqa: F811
    return f"/api/v1/workspaces/{world.workspace_id}/campaigns"


def _calendar(world: World) -> str:  # noqa: F811
    return f"/api/v1/workspaces/{world.workspace_id}/calendar"


def _new_campaign(c: TestClient, world: World, **extra: object) -> dict:  # type: ignore[type-arg]  # noqa: F811
    res = c.post(_campaigns(world), json={"name": "Launch week", **extra}, headers=_h(c))
    assert res.status_code == 201, res.text
    return res.json()  # type: ignore[no-any-return]


def test_campaign_crud_and_summary(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    camp = _new_campaign(
        client,
        world,
        goal="Announce the release",
        start_date="2026-11-01",
        end_date="2026-11-07",
        tags=["#launch", "launch", "product"],
        color="#AA3366",
    )
    assert (
        camp["tags"] == ["launch", "product"] and camp["color"] == "#aa3366" and camp["status"] == "planned"
    )

    post = _create(client, world, acc)
    url = f"{_base(world)}/{post['id']}"
    assigned = client.patch(url, json={"campaign_id": camp["id"]}, headers=_h(client)).json()
    # Assigning a campaign is metadata only: no new version, status unchanged.
    assert assigned["campaign_id"] == camp["id"] and assigned["version_count"] == 1
    assert assigned["status"] == "draft"

    got = client.get(f"{_campaigns(world)}/{camp['id']}").json()
    assert got["post_counts"] == {"draft": 1}
    listed = client.get(_base(world), params={"campaign_id": camp["id"]}).json()
    assert [p["id"] for p in listed] == [post["id"]]

    upd = client.patch(
        f"{_campaigns(world)}/{camp['id']}", json={"status": "active", "goal": None}, headers=_h(client)
    ).json()
    assert upd["status"] == "active" and upd["goal"] is None

    # A campaign with posts cannot be deleted; after removing the post from it, it can.
    assert client.delete(f"{_campaigns(world)}/{camp['id']}", headers=_h(client)).status_code == 409
    cleared = client.patch(url, json={"campaign_id": None}, headers=_h(client)).json()
    assert cleared["campaign_id"] is None
    assert client.delete(f"{_campaigns(world)}/{camp['id']}", headers=_h(client)).status_code == 204

    actions = asyncio.run(_query(select(AuditLogEntry.action).where(AuditLogEntry.entity == "campaign")))
    assert {"campaign.created", "campaign.updated", "campaign.deleted"} <= {a for (a,) in actions}


def test_archived_hidden_by_default(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "editor@example.com")
    camp = _new_campaign(client, world, status="archived")
    assert client.get(_campaigns(world)).json() == []
    ids = [c["id"] for c in client.get(_campaigns(world), params={"include_archived": True}).json()]
    assert ids == [camp["id"]]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "   "},
        {"name": "x", "color": "red"},
        {"name": "x", "tags": ["has space"]},
        {"name": "x", "start_date": "2026-11-07", "end_date": "2026-11-01"},
        {"name": "x", "unknown": 1},
    ],
)
def test_campaign_validation(client: TestClient, world: World, payload: dict) -> None:  # type: ignore[type-arg]  # noqa: F811
    login(client, "editor@example.com")
    assert client.post(_campaigns(world), json=payload, headers=_h(client)).status_code == 422


def test_campaign_permissions_and_isolation(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "editor@example.com")
    camp = _new_campaign(client, world)
    acc = asyncio.run(_add_account(world))
    client.cookies.clear()

    login(client, "viewer@example.com")
    assert client.get(_campaigns(world)).status_code == 200
    assert client.post(_campaigns(world), json={"name": "x"}, headers=_h(client)).status_code == 403
    client.cookies.clear()

    login(client, "stranger@example.com")
    assert client.get(f"{_campaigns(world)}/{camp['id']}").status_code == 404
    other = f"/api/v1/workspaces/{world.other_workspace_id}/campaigns/{camp['id']}"
    assert client.get(other).status_code == 404
    client.cookies.clear()

    # A post cannot be put into another workspace's campaign.
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    res = client.patch(
        f"{_base(world)}/{post['id']}",
        json={"campaign_id": "00000000-0000-0000-0000-000000000001"},
        headers=_h(client),
    )
    assert res.status_code == 404 and res.json()["detail"] == "campaign_not_found"


def test_calendar_and_reschedule(client: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    camp = _new_campaign(client, world, color="#112233")
    post = client.post(
        _base(world),
        json={"account_id": str(acc), "body": "Calendar   test\npost", "campaign_id": camp["id"]},
        headers=_h(client),
    ).json()
    draft = _create(client, world, acc, body="Not scheduled")  # drafts never show in the calendar
    url = f"{_base(world)}/{post['id']}"

    tz = ZoneInfo("Asia/Jerusalem")  # the test workspace's time zone
    day = (datetime.now(tz) + timedelta(days=3)).date()
    res = client.post(f"{url}/schedule", json={"local_time": f"{day.isoformat()}T09:30"}, headers=_h(client))
    assert res.status_code == 200, res.text

    cal = client.get(_calendar(world), params={"start": day.isoformat(), "end": day.isoformat()}).json()
    assert cal["time_zone"] == "Asia/Jerusalem"
    [item] = cal["items"]
    assert item["post_id"] == post["id"] and item["local_time"] == f"{day.isoformat()}T09:30"
    assert item["snippet"] == "Calendar test post" and item["color"] == "#112233" and item["can_reschedule"]
    assert draft["id"] not in [i["post_id"] for i in cal["items"]]

    # Drag to the next day: same endpoint, status stays scheduled, audit says "rescheduled".
    nxt = day + timedelta(days=1)
    moved = client.post(
        f"{url}/schedule", json={"local_time": f"{nxt.isoformat()}T09:30"}, headers=_h(client)
    )
    assert moved.status_code == 200 and moved.json()["status"] == "scheduled"
    expected = datetime.fromisoformat(f"{nxt.isoformat()}T09:30").replace(tzinfo=tz).astimezone(UTC)
    assert datetime.fromisoformat(moved.json()["scheduled_at"].replace("Z", "+00:00")) == expected
    assert (
        client.get(_calendar(world), params={"start": day.isoformat(), "end": day.isoformat()}).json()[
            "items"
        ]
        == []
    )
    actions = asyncio.run(_query(select(AuditLogEntry.action).where(AuditLogEntry.entity == "post")))
    assert ("post.rescheduled",) in actions

    # Viewers see the calendar but cannot move posts.
    client.cookies.clear()
    login(client, "viewer@example.com")
    cal = client.get(_calendar(world), params={"start": nxt.isoformat(), "end": nxt.isoformat()}).json()
    assert cal["items"][0]["can_reschedule"] is False
    later = f"{(nxt + timedelta(days=1)).isoformat()}T09:30"
    assert client.post(f"{url}/schedule", json={"local_time": later}, headers=_h(client)).status_code == 403


@pytest.mark.parametrize(("start", "end"), [("2026-11-10", "2026-11-01"), ("2026-01-01", "2026-12-31")])
def test_calendar_range_limits(client: TestClient, world: World, start: str, end: str) -> None:  # noqa: F811
    login(client, "viewer@example.com")
    assert client.get(_calendar(world), params={"start": start, "end": end}).status_code == 422


def test_calendar_isolation(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "stranger@example.com")
    assert (
        client.get(_calendar(world), params={"start": "2026-11-01", "end": "2026-11-02"}).status_code == 404
    )


def test_reschedule_needs_approval_when_turned_on_later(client: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = _create(client, world, acc)
    url = f"{_base(world)}/{post['id']}"
    when = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert client.post(f"{url}/schedule", json={"scheduled_at": when}, headers=_h(client)).status_code == 200

    # The owner turns approval on: the never-approved scheduled post can no longer be moved or pushed out now.
    asyncio.run(_set_require_approval(world, True))
    later = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    res = client.post(f"{url}/schedule", json={"scheduled_at": later}, headers=_h(client))
    assert res.status_code == 409 and res.json()["detail"] == "approval_required"
    assert client.post(f"{url}/publish-now", headers=_h(client)).status_code == 409


def test_archived_campaign_cannot_take_posts(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    camp = _new_campaign(client, world, status="archived")
    res = client.post(
        _base(world),
        json={"account_id": str(acc), "body": "x", "campaign_id": camp["id"]},
        headers=_h(client),
    )
    assert res.status_code == 409 and res.json()["detail"] == "campaign_archived"


def test_list_oldest_first(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    first = _create(client, world, acc, body="first")
    second = _create(client, world, acc, body="second")
    ids = [p["id"] for p in client.get(_base(world), params={"order": "oldest"}).json()]
    assert ids == [first["id"], second["id"]]
