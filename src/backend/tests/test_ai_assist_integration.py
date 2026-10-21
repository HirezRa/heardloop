"""AI assistant endpoint and best-time suggestions against a real PostgreSQL. Needs TEST_DATABASE_URL."""

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine

from heardloop.ai_assist import AssistError, AssistResult
from heardloop.api import routes_posts
from heardloop.config import get_settings
from heardloop.models import Workspace
from tests.test_auth_integration import World, login, world  # noqa: F401
from tests.test_metrics_integration import _mark_published
from tests.test_posts_integration import _add_account, _base, _create, _h

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _app_client(monkeypatch: pytest.MonkeyPatch, **env: str) -> Iterator[TestClient]:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    from heardloop.api.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as c:
        yield c
    get_settings.cache_clear()


@pytest.fixture
def ai_on(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:  # noqa: F811
    routes_posts._assist_calls.clear()
    yield from _app_client(monkeypatch, AI_BASE_URL="http://ai.example.test:11434/v1", AI_MODEL="llama3.1:8b")


@pytest.fixture
def ai_off(world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:  # noqa: F811
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    yield from _app_client(monkeypatch)


def _assist_url(world: World) -> str:  # noqa: F811
    return f"{_base(world)}/assist"


def test_assist_is_off_without_configuration(ai_off: TestClient, world: World) -> None:  # noqa: F811
    login(ai_off, "editor@example.com")
    assert ai_off.get("/api/v1/auth/me").json()["ai_assist"] is False
    res = ai_off.post(_assist_url(world), json={"action": "improve", "text": "Hi"}, headers=_h(ai_off))
    assert res.status_code == 503 and res.json()["detail"] == "ai_not_configured"


def test_assist_endpoint_roles_validation_errors_and_rate_limit(
    ai_on: TestClient,
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    async def fake(http, settings, action, text, tone):  # type: ignore[no-untyped-def]
        calls.append((action.value, text, tone.value if tone else None))
        if text == "fail":
            raise AssistError("timeout")
        if action.value == "hashtags":
            return AssistResult(text=None, hashtags=["Hiring", "Remote"])
        return AssistResult(text=f"better: {text}", hashtags=[])

    monkeypatch.setattr(routes_posts, "assist", fake)
    url = _assist_url(world)

    login(ai_on, "viewer@example.com")
    assert ai_on.get("/api/v1/auth/me").json()["ai_assist"] is True
    assert ai_on.post(url, json={"action": "improve", "text": "Hi"}, headers=_h(ai_on)).status_code == 403

    ai_on.cookies.clear()
    login(ai_on, "editor@example.com")
    ok = ai_on.post(url, json={"action": "improve", "text": "Hi", "tone": "friendly"}, headers=_h(ai_on))
    assert ok.status_code == 200 and ok.json() == {"text": "better: Hi", "hashtags": []}
    tags = ai_on.post(url, json={"action": "hashtags", "text": "Hi"}, headers=_h(ai_on)).json()
    assert tags == {"text": None, "hashtags": ["Hiring", "Remote"]}
    assert calls[0] == ("improve", "Hi", "friendly")

    for bad in (
        {"action": "summon", "text": "Hi"},
        {"action": "improve", "text": ""},
        {"action": "improve", "text": "x" * 3001},
        {"action": "improve", "text": "Hi", "model": "other"},  # no extra fields
    ):
        assert ai_on.post(url, json=bad, headers=_h(ai_on)).status_code == 422

    failed = ai_on.post(url, json={"action": "improve", "text": "fail"}, headers=_h(ai_on))
    assert failed.status_code == 502 and failed.json()["detail"] == "ai_failed:timeout"

    used = len(calls)  # each accepted request counted, invalid bodies never reach the limiter
    for _ in range(routes_posts._ASSIST_PER_WINDOW - used):
        assert ai_on.post(url, json={"action": "fix", "text": "Hi"}, headers=_h(ai_on)).status_code == 200
    limited = ai_on.post(url, json={"action": "fix", "text": "Hi"}, headers=_h(ai_on))
    assert limited.status_code == 429 and limited.json()["detail"] == "too_many_requests"


# ---- best times ----


async def _utc_workspace(world: World) -> None:  # noqa: F811
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(
            update(Workspace).where(Workspace.id == world.workspace_id).values(time_zone="UTC")
        )
    await engine.dispose()


def _slot_time(weekday: int, hour: int, weeks_ago: int) -> datetime:
    now = datetime.now(UTC)
    day = now - timedelta(days=(now.weekday() - weekday) % 7 + 7 * weeks_ago)
    return day.replace(hour=hour, minute=5, second=0, microsecond=0)


def test_best_times_need_enough_data_and_rank_slots(ai_off: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_utc_workspace(world))
    acc = asyncio.run(_add_account(world))
    login(ai_off, "editor@example.com")
    url = f"/api/v1/workspaces/{world.workspace_id}/reports/best-times"

    def publish(weekday: int, hour: int, weeks_ago: int, impressions: int, reactions: int) -> None:
        post = _create(ai_off, world, acc, body=f"Post {uuid.uuid4().hex[:6]}")
        asyncio.run(_mark_published(post["id"], _slot_time(weekday, hour, weeks_ago)))
        res = ai_off.post(
            f"{_base(world)}/{post['id']}/metrics",
            json={"impressions": impressions, "reactions": reactions},
            headers=_h(ai_off),
        )
        assert res.status_code == 201, res.text

    publish(1, 9, 1, 100, 10)  # Tuesday 09:00, 10 %
    publish(1, 9, 2, 100, 12)  # Tuesday 09:00, 12 %
    publish(3, 17, 1, 200, 10)  # Thursday 17:00, 5 %
    empty = ai_off.get(url).json()
    assert empty["measured"] == 3 and empty["slots"] == []  # below the minimum in total

    publish(3, 17, 3, 200, 10)  # Thursday 17:00, 5 %
    publish(0, 8, 1, 10, 9)  # Monday 08:00, 90 % but a single post: not a pattern yet
    body = ai_off.get(url).json()
    assert body["time_zone"] == "UTC" and body["measured"] == 5 and body["min_posts"] == 2
    assert [(s["weekday"], s["hour"], s["posts"]) for s in body["slots"]] == [(1, 9, 2), (3, 17, 2)]
    assert body["slots"][0]["avg_engagement_rate"] == pytest.approx(0.11)

    assert ai_off.get(url, params={"days": 10}).status_code == 422  # below the 30-day minimum
    ai_off.cookies.clear()
    login(ai_off, "stranger@example.com")
    assert ai_off.get(url).status_code == 404  # not a member
