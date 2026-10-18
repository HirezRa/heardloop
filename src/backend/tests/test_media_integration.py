"""Media upload/download, posts with an image, scheduling in the workspace time zone.

Needs TEST_DATABASE_URL.
"""

import asyncio
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_integration import HDR, World, login, world  # noqa: F401
from tests.test_posts_integration import _add_account, _set_require_approval

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def mc(world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:  # noqa: F811
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    from heardloop.config import get_settings

    get_settings.cache_clear()
    from heardloop.api.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as c:
        yield c


def _h(c: TestClient, content_type: str | None = None) -> dict[str, str]:
    h = {**HDR, "X-CSRF-Token": c.get("/api/v1/auth/me").json()["csrf_token"]}
    if content_type:
        h["Content-Type"] = content_type
    return h


def test_upload_download_and_isolation(mc: TestClient, world: World) -> None:  # noqa: F811
    login(mc, "editor@example.com")
    base = f"/api/v1/workspaces/{world.workspace_id}/media"
    res = mc.post(base, content=PNG, headers=_h(mc, "image/png"))
    assert res.status_code == 201, res.text
    media = res.json()
    assert media["mime_type"] == "image/png" and media["size_bytes"] == len(PNG)

    got = mc.get(media["url"])
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]

    assert mc.post(base, content=b"<svg onload=alert(1)>", headers=_h(mc, "image/png")).status_code == 415
    big = PNG + b"\x00" * (5 * 1024 * 1024)
    assert mc.post(base, content=big, headers=_h(mc, "image/png")).status_code == 413

    mc.cookies.clear()
    login(mc, "stranger@example.com")
    assert mc.get(media["url"]).status_code == 404


def test_post_with_image_and_removal(mc: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(mc, "editor@example.com")
    media = mc.post(
        f"/api/v1/workspaces/{world.workspace_id}/media", content=PNG, headers=_h(mc, "image/png")
    ).json()
    posts = f"/api/v1/workspaces/{world.workspace_id}/posts"
    post = mc.post(
        posts, json={"account_id": str(acc), "body": "With image", "media_ids": [media["id"]]}, headers=_h(mc)
    ).json()
    assert [m["id"] for m in post["media"]] == [media["id"]]

    removed = mc.patch(f"{posts}/{post['id']}", json={"media_ids": []}, headers=_h(mc)).json()
    assert removed["media"] == [] and removed["version_count"] == 2

    two = mc.post(
        posts,
        json={"account_id": str(acc), "body": "x", "media_ids": [media["id"], media["id"]]},
        headers=_h(mc),
    )
    assert two.status_code == 422  # one image per post


def test_schedule_in_workspace_time_zone(mc: TestClient, world: World) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(mc, "editor@example.com")
    posts = f"/api/v1/workspaces/{world.workspace_id}/posts"
    post = mc.post(posts, json={"account_id": str(acc), "body": "tz"}, headers=_h(mc)).json()
    local = (datetime.now(ZoneInfo("Asia/Jerusalem")) + timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0, tzinfo=None
    )
    res = mc.post(f"{posts}/{post['id']}/schedule", json={"local_time": local.isoformat()}, headers=_h(mc))
    assert res.status_code == 200, res.text
    stored = datetime.fromisoformat(res.json()["scheduled_at"].replace("Z", "+00:00"))
    assert stored == local.replace(tzinfo=ZoneInfo("Asia/Jerusalem"))
    both = mc.post(
        f"{posts}/{post['id']}/schedule",
        json={"local_time": local.isoformat(), "scheduled_at": stored.isoformat()},
        headers=_h(mc),
    )
    assert both.status_code == 422


@pytest.mark.parametrize(
    ("local", "code"),
    [
        ("2026-10-25T01:30", "ambiguous_local_time"),  # Israel: clocks go back 02:00 -> 01:00
        ("2027-03-26T02:30", "nonexistent_local_time"),  # Israel: clocks jump 02:00 -> 03:00
    ],
)
def test_dst_edge_times_are_rejected(mc: TestClient, world: World, local: str, code: str) -> None:  # noqa: F811
    asyncio.run(_set_require_approval(world, False))
    acc = asyncio.run(_add_account(world))
    login(mc, "editor@example.com")
    posts = f"/api/v1/workspaces/{world.workspace_id}/posts"
    post = mc.post(posts, json={"account_id": str(acc), "body": "dst"}, headers=_h(mc)).json()
    res = mc.post(f"{posts}/{post['id']}/schedule", json={"local_time": local}, headers=_h(mc))
    assert res.status_code == 422 and res.json()["detail"] == code


def test_workspace_quota(mc: TestClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from heardloop.api import routes_media

    monkeypatch.setattr(routes_media, "WORKSPACE_QUOTA_BYTES", len(PNG) + 10)
    login(mc, "editor@example.com")
    base = f"/api/v1/workspaces/{world.workspace_id}/media"
    assert mc.post(base, content=PNG, headers=_h(mc, "image/png")).status_code == 201
    res = mc.post(base, content=PNG, headers=_h(mc, "image/png"))
    assert res.status_code == 413 and res.json()["detail"] == "media_quota_exceeded"


MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * (80 * 1024)


def test_video_upload_download_and_rules(mc: TestClient, world: World) -> None:  # noqa: F811
    login(mc, "editor@example.com")
    base = f"/api/v1/workspaces/{world.workspace_id}/media"
    res = mc.post(base, content=MP4, headers=_h(mc, "video/mp4"))
    assert res.status_code == 201, res.text
    video = res.json()
    assert video["mime_type"] == "video/mp4" and video["size_bytes"] == len(MP4)

    got = mc.get(video["url"])
    assert got.status_code == 200 and got.content == MP4 and got.headers["content-type"] == "video/mp4"
    part = mc.get(video["url"], headers={"Range": "bytes=0-7"})
    assert part.status_code == 206 and part.content == MP4[:8]  # seeking works

    tiny = MP4[:1024]
    assert mc.post(base, content=tiny, headers=_h(mc, "video/mp4")).json()["detail"] == "video_too_small"
    webm = b"\x1aE\xdf\xa3" + b"\x00" * 2000
    assert mc.post(base, content=webm, headers=_h(mc, "video/mp4")).status_code == 415

    # A video post cannot also carry a link card.
    acc = asyncio.run(_add_account(world))
    posts = f"/api/v1/workspaces/{world.workspace_id}/posts"
    res = mc.post(
        posts,
        json={
            "account_id": str(acc),
            "body": "Watch",
            "media_ids": [video["id"]],
            "link": {"url": "https://example.com/", "title": "x"},
        },
        headers=_h(mc),
    )
    assert res.status_code == 422 and res.json()["detail"] == "video_with_link"
    ok = mc.post(
        posts, json={"account_id": str(acc), "body": "Watch", "media_ids": [video["id"]]}, headers=_h(mc)
    )
    assert ok.status_code == 201 and ok.json()["media"][0]["id"] == video["id"]


def test_hashtag_library(mc: TestClient, world: World) -> None:  # noqa: F811
    login(mc, "editor@example.com")
    base = f"/api/v1/workspaces/{world.workspace_id}/posts/hashtags"
    assert mc.get(base).json() == {"saved": [], "recent": [], "recommended_max": 5}
    out = mc.post(base, json={"tag": "#Launch"}, headers=_h(mc)).json()
    assert out["saved"] == ["Launch"]
    assert mc.post(base, json={"tag": "launch"}, headers=_h(mc)).json()["saved"] == ["Launch"]  # same tag
    for bad in ("two words", "", "#", "a-b"):
        assert mc.post(base, json={"tag": bad}, headers=_h(mc)).status_code == 422
    assert mc.delete(f"{base}/LAUNCH", headers=_h(mc)).status_code == 204
    assert mc.get(base).json()["saved"] == []
    mc.cookies.clear()
    login(mc, "viewer@example.com")
    assert mc.post(base, json={"tag": "x"}, headers=_h(mc)).status_code == 403
