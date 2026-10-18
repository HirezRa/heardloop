"""Metrics entry, reports, CSV export and the weekly digest. Needs TEST_DATABASE_URL."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop import digest
from heardloop.config import get_settings
from heardloop.models import Notification, Post, Workspace
from tests.test_auth_integration import World, _query, client, login, world  # noqa: F401
from tests.test_posts_integration import _add_account, _base, _create, _h

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


async def _mark_published(post_id: str, when: datetime) -> None:
    """Shortcut past the worker: the post is live on LinkedIn since `when`."""
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(
            update(Post)
            .where(Post.id == uuid.UUID(post_id))
            .values(
                status="published",
                published_at=when,
                linkedin_post_urn=f"urn:li:share:{uuid.uuid4().int % 10**12}",
            )
        )
    await engine.dispose()


def _published(c: TestClient, world: World, acc: uuid.UUID, body: str, days_ago: float) -> str:  # noqa: F811
    post = _create(c, world, acc, body=body)
    asyncio.run(_mark_published(post["id"], datetime.now(UTC) - timedelta(days=days_ago)))
    return str(post["id"])


def test_metrics_entry_rules(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    draft = _create(client, world, acc)
    url = f"{_base(world)}/{draft['id']}/metrics"
    assert client.post(url, json={"impressions": 10}, headers=_h(client)).status_code == 409  # not published

    pid = _published(client, world, acc, "Live post", days_ago=3)
    url = f"{_base(world)}/{pid}/metrics"
    assert client.post(url, json={}, headers=_h(client)).status_code == 422  # nothing to record
    assert client.post(url, json={"impressions": -1}, headers=_h(client)).status_code == 422
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    assert (
        client.post(url, json={"impressions": 5, "captured_at": old}, headers=_h(client)).json()["detail"]
        == "captured_before_publish"
    )
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert (
        client.post(url, json={"impressions": 5, "captured_at": future}, headers=_h(client)).status_code
        == 422
    )

    first = client.post(
        url,
        json={"impressions": 1200, "reactions": 30, "comments": 4, "note": "  day  1 "},
        headers=_h(client),
    )
    assert first.status_code == 201, first.text
    assert first.json()["note"] == "day 1" and first.json()["source"] == "manual"
    assert first.json()["recorded_by_name"] == "Editor"
    second = client.post(
        url, json={"impressions": 2500, "reactions": 61, "comments": 9, "saves": 7}, headers=_h(client)
    ).json()
    listed = client.get(url).json()
    assert [m["impressions"] for m in listed] == [2500, 1200]  # newest first

    client.cookies.clear()
    login(client, "viewer@example.com")
    assert client.get(url).status_code == 200
    assert client.post(url, json={"impressions": 1}, headers=_h(client)).status_code == 403
    assert client.delete(f"{url}/{second['id']}", headers=_h(client)).status_code == 403
    client.cookies.clear()
    login(client, "stranger@example.com")
    assert client.get(url).status_code == 404


def test_report_groups_and_csv(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    camp = client.post(
        f"/api/v1/workspaces/{world.workspace_id}/campaigns", json={"name": "Launch"}, headers=_h(client)
    ).json()
    a = _published(client, world, acc, "Post A =SUM(1)", days_ago=2)
    b = _published(client, world, acc, "Post B", days_ago=9)
    _published(client, world, acc, "Post C without metrics", days_ago=1)
    client.patch(f"{_base(world)}/{a}", json={"campaign_id": camp["id"]}, headers=_h(client))
    client.post(
        f"{_base(world)}/{a}/metrics",
        json={"impressions": 1000, "reactions": 50, "comments": 10},
        headers=_h(client),
    )
    client.post(
        f"{_base(world)}/{a}/metrics",
        json={"impressions": 2000, "reactions": 80, "comments": 20},
        headers=_h(client),
    )
    client.post(f"{_base(world)}/{b}/metrics", json={"impressions": 500, "reactions": 5}, headers=_h(client))

    rep = client.get(f"/api/v1/workspaces/{world.workspace_id}/reports").json()
    assert rep["time_zone"] == "Asia/Jerusalem"
    assert rep["totals"]["posts"] == 3 and rep["totals"]["measured"] == 2
    assert (
        rep["totals"]["impressions"] == 2500 and rep["totals"]["engagement"] == 105
    )  # latest measurement only
    assert rep["top_posts"][0]["post_id"] == a and rep["top_posts"][0]["engagement_rate"] == 0.05
    assert [p["post_id"] for p in rep["unmeasured"]] == [
        str(uuid.UUID(x)) for x in [rep["unmeasured"][0]["post_id"]]
    ]
    assert rep["unmeasured"][0]["snippet"].startswith("Post C")
    campaigns = {c["key"]: c for c in rep["by_campaign"]}
    assert campaigns[camp["id"]]["label"] == "Launch" and campaigns[camp["id"]]["engagement"] == 100
    assert sum(bucket["posts"] for bucket in rep["by_weekday"]) == 3
    assert {m["key"] for m in rep["by_media"]} == {"none"}
    assert len(rep["trend"]) >= 1

    only = client.get(
        f"/api/v1/workspaces/{world.workspace_id}/reports", params={"campaign_id": camp["id"]}
    ).json()
    assert only["totals"]["posts"] == 1
    bad = client.get(
        f"/api/v1/workspaces/{world.workspace_id}/reports",
        params={"start": "2020-01-01", "end": "2026-01-01"},
    )
    assert bad.status_code == 422

    csv_res = client.get(f"/api/v1/workspaces/{world.workspace_id}/reports/export.csv")
    assert csv_res.status_code == 200 and "text/csv" in csv_res.headers["content-type"]
    assert csv_res.headers["content-disposition"].startswith('attachment; filename="heardloop-posts-')
    text = csv_res.content.decode("utf-8-sig")
    lines = text.splitlines()
    assert lines[0].startswith("published_at,campaign,media,impressions")
    assert len(lines) == 4
    assert (
        "'=SUM(1)" not in text and "Post A '=SUM" not in text
    )  # text starts with "Post", no formula at cell start
    row_a = next(line for line in lines if "Post A" in line)
    assert ",Launch,none,2000,80,20,,,," in row_a

    client.cookies.clear()
    login(client, "stranger@example.com")
    assert client.get(f"/api/v1/workspaces/{world.workspace_id}/reports").status_code == 404


def test_csv_formula_cells_are_neutralised() -> None:
    from heardloop.api.routes_metrics import _csv_safe

    assert _csv_safe("=1+1") == "'=1+1" and _csv_safe("@cmd") == "'@cmd" and _csv_safe("plain") == "plain"


def test_weekly_digest_once_per_week(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    _published(client, world, acc, "Last week's post", days_ago=4)
    monkeypatch.setenv("DATABASE_URL", URL or "")
    get_settings.cache_clear()

    async def go() -> tuple[int, int, list]:  # type: ignore[type-arg]
        engine = create_async_engine(URL or "")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        tz = ZoneInfo("Asia/Jerusalem")
        # A Monday 09:30 local, after the week with the post.
        now = datetime.now(tz)
        monday = (now + timedelta(days=(7 - now.weekday()) % 7 or 7)).replace(
            hour=9, minute=30, second=0, microsecond=0
        )
        clock = lambda: monday.astimezone(UTC)  # noqa: E731
        first = await digest.send_weekly_digests(factory, clock)
        second = await digest.send_weekly_digests(factory, clock)
        async with engine.connect() as conn:
            rows = (await conn.execute(select(Notification.kind, Notification.data))).all()
            week = (
                await conn.execute(
                    select(Workspace.last_digest_week).where(Workspace.id == world.workspace_id)
                )
            ).scalar_one()
        await engine.dispose()
        assert week and week.startswith(str(monday.isocalendar()[0]))
        return first, second, rows

    first, second, rows = asyncio.run(go())
    assert (first, second) == (1, 0)
    kinds = [r[0] for r in rows]
    assert kinds.count("digest.weekly") >= 4  # every member of the workspace
    data = next(r[1] for r in rows if r[0] == "digest.weekly")
    assert data["published_last_week"] >= 1 and data["unmeasured"] >= 1


def test_digest_not_sent_outside_monday_morning(client: TestClient, world: World) -> None:  # noqa: F811
    async def go() -> int:
        engine = create_async_engine(URL or "")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        tz = ZoneInfo("Asia/Jerusalem")
        now = datetime.now(tz)
        tuesday = (now + timedelta(days=(8 - now.weekday()) % 7 or 7)).replace(hour=10, minute=0)
        n = await digest.send_weekly_digests(factory, lambda: tuesday.astimezone(UTC))
        await engine.dispose()
        return n

    assert asyncio.run(go()) == 0
