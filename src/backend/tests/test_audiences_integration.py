"""Audiences (FR-28): management, assignment to posts and campaigns, and reports that compare like with like.

Needs TEST_DATABASE_URL.
"""

import asyncio
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import create_async_engine

from heardloop.audiences import AudienceMap, weekend_for_zone
from heardloop.models import Audience, PostMetric
from tests.test_auth_integration import World, client, login, world  # noqa: F401
from tests.test_metrics_integration import URL, _published
from tests.test_posts_integration import _add_account, _base, _h

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _ws(world: World) -> str:  # noqa: F811
    return f"/api/v1/workspaces/{world.workspace_id}"


def _add(c: TestClient, world: World, **fields: object) -> dict:  # type: ignore[type-arg]  # noqa: F811
    payload = {
        "code": "IL",
        "name": "Israel",
        "time_zone": "Asia/Jerusalem",
        "weekend_days": [4, 5],
        **fields,
    }
    res = c.post(f"{_ws(world)}/audiences", json=payload, headers=_h(c))
    assert res.status_code == 201, res.text
    return res.json()  # type: ignore[no-any-return]


async def _set_published_at(post_id: str, when: datetime) -> None:
    """Moves a published post to an exact publish time (the metrics test helper uses 'now - days')."""
    from heardloop.models import Post

    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(update(Post).where(Post.id == uuid.UUID(post_id)).values(published_at=when))
    await engine.dispose()


async def _foreign_audience(workspace_id: uuid.UUID) -> uuid.UUID:
    """An audience of another workspace, inserted directly (its owner has no second factor in the seed)."""
    assert URL
    engine = create_async_engine(URL)
    audience_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            insert(Audience).values(
                id=audience_id,
                workspace_id=workspace_id,
                code="FR",
                name="France",
                time_zone="Europe/Paris",
                weekend_days=[5, 6],
                is_default=False,
                position=0,
                created_at=datetime.now(UTC),
            )
        )
    await engine.dispose()
    return audience_id


async def _set_captured_at(metric_id: str, when: datetime) -> None:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(
            update(PostMetric).where(PostMetric.id == uuid.UUID(metric_id)).values(captured_at=when)
        )
    await engine.dispose()


def test_weekend_defaults() -> None:
    assert weekend_for_zone("Asia/Jerusalem") == [4, 5]
    assert weekend_for_zone("America/New_York") == [5, 6]
    assert weekend_for_zone("UTC") == [5, 6]


def test_audience_map_resolution() -> None:
    ws_tz = ZoneInfo("Asia/Jerusalem")
    il = Audience(
        id=uuid.uuid4(), code="IL", time_zone="Asia/Jerusalem", weekend_days=[4, 5], is_default=True
    )
    us = Audience(id=uuid.uuid4(), code="US", time_zone="America/New_York", weekend_days=[5, 6])
    gone = Audience(id=uuid.uuid4(), code="X", time_zone="Not/AZone", weekend_days=[])
    amap = AudienceMap([il, us, gone], ws_tz)
    assert amap.default is il
    assert amap.resolve(us.id, il.id).tz.key == "America/New_York"  # the post's own audience wins
    assert amap.resolve(None, il.id).audience is il  # else the campaign's
    none = amap.resolve(None, None)
    assert none.audience is None and none.tz is ws_tz and none.key == "none" and none.weekend_days == []
    assert amap.resolve(uuid.uuid4(), None).audience is None  # unknown id: like none
    assert amap.resolve(gone.id, None).tz is ws_tz  # unusable zone falls back to the workspace zone


def test_audience_management_and_permissions(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "editor@example.com")
    assert client.get(f"{_ws(world)}/audiences").json() == []
    presets = client.get(f"{_ws(world)}/audiences/presets").json()
    assert {p["code"] for p in presets} >= {"IL", "US-E", "GLOBAL"}
    assert all(p["time_zone"] and len(p["weekend_days"]) == 2 for p in presets)
    forbidden = client.post(
        f"{_ws(world)}/audiences",
        json={"code": "IL", "name": "Israel", "time_zone": "Asia/Jerusalem"},
        headers=_h(client),
    )
    assert forbidden.status_code == 403  # editors read, owners manage

    client.cookies.clear()
    login(client, "owner@example.com")
    il = _add(client, world)
    assert il["is_default"] is True  # the first audience becomes the default
    assert il["weekend_days"] == [4, 5] and il["position"] == 0 and il["post_count"] == 0
    us = _add(client, world, code="us", name="US East", time_zone="America/New_York", weekend_days=[6, 5])
    assert us["code"] == "US" and us["weekend_days"] == [5, 6] and us["is_default"] is False
    assert us["position"] == 1

    dup = client.post(
        f"{_ws(world)}/audiences",
        json={"code": "il", "name": "Again", "time_zone": "UTC"},
        headers=_h(client),
    )
    assert dup.status_code == 409 and dup.json()["detail"] == "audience_code_taken"
    for bad in (
        {"code": "IL 2", "name": "x", "time_zone": "UTC"},
        {"code": "X", "name": "x", "time_zone": "Mars/Olympus"},
        {"code": "X", "name": "x", "time_zone": "UTC", "weekend_days": [7]},
        {"code": "X", "name": "x", "time_zone": "UTC", "weekend_days": [0, 1, 2, 3]},
        {"code": "X", "name": "a\x00b", "time_zone": "UTC"},
    ):
        assert client.post(f"{_ws(world)}/audiences", json=bad, headers=_h(client)).status_code == 422, bad

    moved = client.patch(f"{_ws(world)}/audiences/{us['id']}", json={"is_default": True}, headers=_h(client))
    assert moved.status_code == 200 and moved.json()["is_default"] is True
    listed = {a["code"]: a for a in client.get(f"{_ws(world)}/audiences").json()}
    assert listed["IL"]["is_default"] is False and listed["US"]["is_default"] is True
    renamed = client.patch(
        f"{_ws(world)}/audiences/{us['id']}",
        json={"name": "US East Coast", "time_zone": "America/Chicago", "weekend_days": [5, 6]},
        headers=_h(client),
    ).json()
    assert renamed["name"] == "US East Coast" and renamed["time_zone"] == "America/Chicago"
    clash = client.patch(f"{_ws(world)}/audiences/{us['id']}", json={"code": "IL"}, headers=_h(client))
    assert clash.status_code == 409

    # A used audience cannot be deleted; an unused one can.
    acc = asyncio.run(_add_account(world))
    post = client.post(
        _base(world),
        json={"account_id": str(acc), "body": "For Israel", "audience_id": il["id"]},
        headers=_h(client),
    ).json()
    assert post["audience_id"] == il["id"] and post["effective_audience_id"] == il["id"]
    in_use = client.delete(f"{_ws(world)}/audiences/{il['id']}", headers=_h(client))
    assert in_use.status_code == 409 and in_use.json()["detail"] == "audience_in_use"
    client.patch(f"{_base(world)}/{post['id']}", json={"audience_id": None}, headers=_h(client))
    assert client.delete(f"{_ws(world)}/audiences/{il['id']}", headers=_h(client)).status_code == 204
    assert client.delete(f"{_ws(world)}/audiences/{il['id']}", headers=_h(client)).status_code == 404

    client.cookies.clear()
    login(client, "stranger@example.com")
    assert client.get(f"{_ws(world)}/audiences").status_code == 404


def test_audience_on_posts_and_campaigns(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "owner@example.com")
    il = _add(client, world)
    us = _add(client, world, code="US", name="US", time_zone="America/New_York", weekend_days=[5, 6])
    acc = asyncio.run(_add_account(world))

    camp = client.post(
        f"{_ws(world)}/campaigns", json={"name": "US launch", "audience_id": us["id"]}, headers=_h(client)
    )
    assert camp.status_code == 201 and camp.json()["audience_id"] == us["id"]
    wrong = client.post(
        f"{_ws(world)}/campaigns", json={"name": "x", "audience_id": str(uuid.uuid4())}, headers=_h(client)
    )
    assert wrong.status_code == 404 and wrong.json()["detail"] == "audience_not_found"

    # A post inherits its campaign's audience until it sets its own.
    post = client.post(
        _base(world),
        json={"account_id": str(acc), "body": "Hello", "campaign_id": camp.json()["id"]},
        headers=_h(client),
    ).json()
    assert post["audience_id"] is None and post["effective_audience_id"] == us["id"]
    own = client.patch(
        f"{_base(world)}/{post['id']}", json={"audience_id": il["id"]}, headers=_h(client)
    ).json()
    assert own["audience_id"] == il["id"] and own["effective_audience_id"] == il["id"]
    assert own["status"] == "draft" and own["version_count"] == 1  # planning metadata: no new version
    missing = client.patch(
        f"{_base(world)}/{post['id']}", json={"audience_id": str(uuid.uuid4())}, headers=_h(client)
    )
    assert missing.status_code == 404

    # Changing the audience of a published post is allowed (it only changes how it is reported).
    live = _published(client, world, acc, "Live", days_ago=1)
    changed = client.patch(f"{_base(world)}/{live}", json={"audience_id": us["id"]}, headers=_h(client))
    assert changed.status_code == 200 and changed.json()["audience_id"] == us["id"]
    frozen = client.patch(f"{_base(world)}/{live}", json={"body": "edit"}, headers=_h(client))
    assert frozen.status_code == 409

    # Audiences of another workspace are invisible here.
    foreign = asyncio.run(_foreign_audience(world.other_workspace_id))
    assert (
        client.patch(
            f"{_base(world)}/{post['id']}", json={"audience_id": str(foreign)}, headers=_h(client)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{_ws(world)}/campaigns", json={"name": "y", "audience_id": str(foreign)}, headers=_h(client)
        ).status_code
        == 404
    )
    cleared = client.patch(
        f"{_ws(world)}/campaigns/{camp.json()['id']}", json={"audience_id": None}, headers=_h(client)
    )
    assert cleared.status_code == 200 and cleared.json()["audience_id"] is None


def test_reports_use_each_audience_zone(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "owner@example.com")
    il = _add(client, world)
    us = _add(client, world, code="US", name="US", time_zone="America/New_York", weekend_days=[5, 6])
    acc = asyncio.run(_add_account(world))

    # Three posts published at the same instant, 14:00 UTC on a Sunday 10 days ago: in Israel that is a
    # weekday afternoon, in New York a Sunday morning (weekend), and the workspace zone is Israel's.
    now = datetime.now(UTC)
    base = (now - timedelta(days=10)).replace(hour=14, minute=0, second=0, microsecond=0)
    sunday = base - timedelta(days=(base.weekday() - 6) % 7)
    posts = {}
    for key, audience in (("il", il), ("us", us), ("none", None)):
        pid = _published(client, world, acc, f"Post {key}", days_ago=10)
        asyncio.run(_set_published_at(pid, sunday))
        if audience is not None:
            client.patch(f"{_base(world)}/{pid}", json={"audience_id": audience["id"]}, headers=_h(client))
        client.post(
            f"{_base(world)}/{pid}/metrics", json={"impressions": 1000, "reactions": 50}, headers=_h(client)
        )
        posts[key] = pid

    rep = client.get(f"{_ws(world)}/reports").json()
    assert rep["time_zone"] == "Asia/Jerusalem" and rep["audience_id"] is None
    assert rep["time_zones"] == ["America/New_York", "Asia/Jerusalem"]
    assert rep["weekend_days"] == [4, 5]  # the default audience (IL) decides the weekend shown
    il_hour = sunday.astimezone(ZoneInfo("Asia/Jerusalem")).hour
    us_hour = sunday.astimezone(ZoneInfo("America/New_York")).hour
    hours = {b["key"]: b["posts"] for b in rep["by_hour"] if b["posts"]}
    assert hours == {str(il_hour): 2, str(us_hour): 1}  # IL post and the post without audience share a slot
    weekdays = {b["key"]: b for b in rep["by_weekday"]}
    assert weekdays["6"]["posts"] == 3 and weekdays["6"]["weekend"] is False  # Sunday is a workday in Israel
    assert weekdays["4"]["weekend"] is True and weekdays["5"]["weekend"] is True
    by_audience = {b["key"]: b for b in rep["by_audience"]}
    assert by_audience[il["id"]]["label"] == "IL" and by_audience[us["id"]]["posts"] == 1
    assert by_audience["none"]["posts"] == 1 and list(by_audience)[-1] == "none"
    by_post = {p["post_id"]: p for p in rep["top_posts"]}
    assert by_post[posts["us"]]["time_zone"] == "America/New_York" and by_post[posts["us"]]["weekend"] is True
    assert by_post[posts["il"]]["local_hour"] == il_hour and by_post[posts["il"]]["weekend"] is False
    assert (
        by_post[posts["none"]]["audience_id"] is None
        and by_post[posts["none"]]["time_zone"] == "Asia/Jerusalem"
    )

    # by_time never mixes clocks: one group per audience, each with its own zone and weekend.
    groups = {g["key"]: g for g in rep["by_time"]}
    assert rep["clock"] == "audience" and list(groups) == [il["id"], us["id"], "none"]
    assert groups[us["id"]]["time_zone"] == "America/New_York" and groups[us["id"]]["weekend_days"] == [5, 6]
    assert {b["key"] for b in groups[us["id"]]["by_hour"] if b["posts"]} == {str(us_hour)}
    assert groups[us["id"]]["by_weekday"][6]["posts"] == 1 and groups[us["id"]]["by_weekday"][6]["weekend"]
    assert groups[il["id"]]["label"] == "IL" and groups[il["id"]]["by_weekday"][6]["weekend"] is False
    assert {b["key"] for b in groups["none"]["by_hour"] if b["posts"]} == {str(il_hour)}
    # The workspace clock puts every post on one clock: "when did I press publish".
    ws_clock = client.get(f"{_ws(world)}/reports", params={"clock": "workspace"}).json()
    assert ws_clock["clock"] == "workspace" and len(ws_clock["by_time"]) == 1
    only = ws_clock["by_time"][0]
    assert only["key"] == "workspace" and only["time_zone"] == "Asia/Jerusalem" and only["posts"] == 3
    assert {b["key"]: b["posts"] for b in only["by_hour"] if b["posts"]} == {str(il_hour): 3}
    assert client.get(f"{_ws(world)}/reports", params={"clock": "mars"}).status_code == 422

    only_us = client.get(f"{_ws(world)}/reports", params={"audience_id": us["id"]}).json()
    assert only_us["totals"]["posts"] == 1 and only_us["weekend_days"] == [5, 6]
    assert only_us["time_zones"] == ["America/New_York"] and only_us["audience_id"] == us["id"]
    assert {b["key"] for b in only_us["by_hour"] if b["posts"]} == {str(us_hour)}
    assert client.get(f"{_ws(world)}/reports", params={"audience_id": str(uuid.uuid4())}).status_code == 404

    csv_text = client.get(f"{_ws(world)}/reports/export.csv").content.decode("utf-8-sig")
    lines = csv_text.splitlines()
    assert lines[0].startswith("published_at,audience,time_zone,published_local,weekend,campaign,media,")
    us_row = next(line for line in lines if "Post us" in line)
    assert ",US,America/New_York," in us_row and ",yes," in us_row
    il_row = next(line for line in lines if "Post il" in line)
    assert ",IL,Asia/Jerusalem," in il_row and ",no," in il_row
    filtered = client.get(f"{_ws(world)}/reports/export.csv", params={"audience_id": il["id"]})
    assert len(filtered.content.decode("utf-8-sig").splitlines()) == 2


def test_best_times_per_audience(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "owner@example.com")
    us = _add(client, world, code="US", name="US", time_zone="America/New_York", weekend_days=[5, 6])
    acc = asyncio.run(_add_account(world))
    now = datetime.now(UTC)
    # Five US posts at 13:00 UTC on Tuesdays (9:00 New York in summer, 8:00 in winter) and five posts
    # without an audience at the same instants: each group must be judged on its own.
    tuesday = (now - timedelta(days=14)).replace(hour=13, minute=0, second=0, microsecond=0)
    tuesday -= timedelta(days=(tuesday.weekday() - 1) % 7)
    for i in range(5):
        when = tuesday - timedelta(days=7 * i)
        for audience in (us, None):
            pid = _published(client, world, acc, "Post", days_ago=14 + 7 * i)
            asyncio.run(_set_published_at(pid, when))
            if audience is not None:
                client.patch(
                    f"{_base(world)}/{pid}", json={"audience_id": audience["id"]}, headers=_h(client)
                )
            client.post(
                f"{_base(world)}/{pid}/metrics",
                json={"impressions": 1000, "reactions": 80 if audience else 20},
                headers=_h(client),
            )
    whens = [tuesday - timedelta(days=7 * i) for i in range(5)]

    def expected(zone: str) -> set[tuple[int, int, int]]:
        # Same instants, read in the zone; a DST change inside the five weeks splits them over two hours.
        local = [w.astimezone(ZoneInfo(zone)) for w in whens]
        counts = Counter((d.weekday(), d.hour) for d in local)
        return {(weekday, hour, n) for (weekday, hour), n in counts.items() if n >= 2}

    for_us = client.get(f"{_ws(world)}/reports/best-times", params={"audience_id": us["id"]}).json()
    assert for_us["time_zone"] == "America/New_York" and for_us["audience_id"] == us["id"]
    assert for_us["measured"] == 5 and for_us["window_days"] == 7
    assert {(s["weekday"], s["hour"], s["posts"]) for s in for_us["slots"]} == expected("America/New_York")
    assert {s["avg_engagement_rate"] for s in for_us["slots"]} == {0.08}

    without = client.get(f"{_ws(world)}/reports/best-times").json()
    assert without["time_zone"] == "Asia/Jerusalem" and without["audience_id"] is None
    assert {(s["weekday"], s["hour"], s["posts"]) for s in without["slots"]} == expected("Asia/Jerusalem")
    assert {s["avg_engagement_rate"] for s in without["slots"]} == {0.02}
    assert (
        client.get(f"{_ws(world)}/reports/best-times", params={"audience_id": str(uuid.uuid4())}).status_code
        == 404
    )


def test_measurement_window(client: TestClient, world: World) -> None:  # noqa: F811
    """Posts are compared at the same age: the measurement closest to `window_days` after publishing."""
    login(client, "editor@example.com")
    acc = asyncio.run(_add_account(world))
    pid = _published(client, world, acc, "Measured twice", days_ago=20)
    published = client.get(f"{_base(world)}/{pid}").json()["published_at"]
    published_at = datetime.fromisoformat(published)
    early = client.post(
        f"{_base(world)}/{pid}/metrics", json={"impressions": 1000, "reactions": 10}, headers=_h(client)
    ).json()
    late = client.post(
        f"{_base(world)}/{pid}/metrics", json={"impressions": 3000, "reactions": 30}, headers=_h(client)
    ).json()
    asyncio.run(_set_captured_at(early["id"], published_at + timedelta(days=6)))
    asyncio.run(_set_captured_at(late["id"], published_at + timedelta(days=19)))

    def totals(**params: object) -> dict:  # type: ignore[type-arg]
        res = client.get(f"{_ws(world)}/reports", params=params)
        assert res.status_code == 200, res.text
        return res.json()  # type: ignore[no-any-return]

    default = totals()
    assert default["window_days"] == 7 and default["window_tolerance_days"] == 3
    assert default["totals"]["impressions"] == 1000  # day 6 is closest to day 7
    assert default["off_window"] == 0
    assert (
        default["top_posts"][0]["measured_age_days"] == 6.0 and default["top_posts"][0]["off_window"] is False
    )
    latest = totals(window_days=0)
    assert latest["totals"]["impressions"] == 3000 and latest["window_tolerance_days"] == 1
    assert latest["off_window"] == 0  # never flagged when the latest measurement is asked for
    month = totals(window_days=30)
    assert month["totals"]["impressions"] == 3000 and month["off_window"] == 0  # 19 is within 30 +/- 15
    two = totals(window_days=2)
    assert two["totals"]["impressions"] == 1000 and two["off_window"] == 1  # 6 is outside 2 +/- 1
    assert two["top_posts"][0]["off_window"] is True
    assert client.get(f"{_ws(world)}/reports", params={"window_days": 91}).status_code == 422

    # A post younger than the window is preliminary (its numbers still grow), not an outlier.
    young = _published(client, world, acc, "Two days old", days_ago=2)
    client.post(
        f"{_base(world)}/{young}/metrics", json={"impressions": 50, "reactions": 1}, headers=_h(client)
    )
    rep = totals()
    assert rep["preliminary"] == 1 and rep["off_window"] == 0
    row = next(p for p in rep["top_posts"] if p["post_id"] == young)
    assert row["preliminary"] is True and row["off_window"] is False
    assert totals(window_days=0)["preliminary"] == 0  # "latest" has no window to be early for

    csv_text = client.get(f"{_ws(world)}/reports/export.csv", params={"window_days": 0}).content.decode(
        "utf-8-sig"
    )
    row = csv_text.splitlines()[1]
    assert ",3000,30," in row and ",19.0," in row
