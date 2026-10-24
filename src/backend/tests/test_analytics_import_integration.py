"""Import of LinkedIn analytics exports through the API. Needs TEST_DATABASE_URL."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from heardloop.domain.workflow import PostStatus
from heardloop.models import AuditLogEntry, Post, PostVersion, User
from tests.test_auth_integration import World, client, login, world  # noqa: F401
from tests.test_posts_integration import _add_account, _base, _create, _h
from tests.xlsx_builder import layout_a, layout_b, make_xlsx

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

SHARE_A = "7114500000000000001"
SHARE_B = "7114500000000000002"
NEW_SHARE = "7114500000000000099"


def _day(d: datetime) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def _a(share: str, *, published: datetime, exported: datetime) -> bytes:
    return layout_a(share, date=_day(published), time="5:30 AM", exported=_day(exported))


async def _publish(post_id: str, urn: str, when: datetime) -> None:
    assert URL
    engine = create_async_engine(URL)
    async with engine.begin() as conn:
        await conn.execute(
            update(Post)
            .where(Post.id == uuid.UUID(post_id))
            .values(status="published", published_at=when, linkedin_post_urn=urn)
        )
    await engine.dispose()


def _published(c: TestClient, world: World, acc: uuid.UUID, urn: str, days_ago: float = 12) -> str:  # noqa: F811
    post = _create(c, world, acc, body="An earlier post")
    asyncio.run(_publish(post["id"], urn, datetime.now(UTC) - timedelta(days=days_ago)))
    return str(post["id"])


def _import(c: TestClient, world: World, data: bytes, **form: str) -> httpx.Response:  # noqa: F811
    response: httpx.Response = c.post(
        f"/api/v1/workspaces/{world.workspace_id}/metrics/import",
        files={"file": ("export.xlsx", data, "application/octet-stream")},
        data=form,
        headers=_h(c),
    )
    return response


def _audit_actions() -> list[tuple[str, dict | None]]:  # type: ignore[type-arg]
    async def go() -> list[tuple[str, dict | None]]:  # type: ignore[type-arg]
        assert URL
        engine = create_async_engine(URL)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(AuditLogEntry.action, AuditLogEntry.diff_json).order_by(AuditLogEntry.id)
                )
            ).all()
        await engine.dispose()
        return [(a, d) for a, d in rows]

    return asyncio.run(go())


def test_import_into_existing_post_is_idempotent(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    pid = _published(client, world, acc, f"urn:li:share:{SHARE_A}")
    now = datetime.now(UTC)
    data = _a(SHARE_A, published=now - timedelta(days=12), exported=now - timedelta(days=1))

    res = _import(client, world, data)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["post_id"] == pid and body["created"] is False
    assert body["source"] == "linkedin_export" and body["note"] == "Imported from LinkedIn export"
    assert body["impressions"] == 1200 and body["members_reached"] == 800
    assert body["profile_viewers"] == 12 and body["followers_gained"] == 3 and body["sends"] == 4
    assert body["demographics"][0] == {"category": "Seniority", "value": "Senior", "share": 0.365}
    assert body["top"]["location"] == "Tel Aviv" and body["recorded_by_name"] == "Editor"

    listed = client.get(f"{_base(world)}/{pid}/metrics").json()
    assert len(listed) == 1 and listed[0]["top"]["industry"] == "Software Development"

    again = _import(client, world, data)
    assert again.status_code == 409 and again.json()["detail"] == "already_imported"
    assert len(client.get(f"{_base(world)}/{pid}/metrics").json()) == 1
    actions = [a for a, _ in _audit_actions()]
    assert "metrics.imported" in actions and "post.imported" not in actions
    changes = next(d for a, d in _audit_actions() if a == "metrics.imported")
    assert changes and changes["impressions"] == 1200 and "demographics" not in changes

    # An export captured on another day is a new measurement, not a duplicate.
    explicit = _import(client, world, data, captured_at=(now - timedelta(days=3)).isoformat())
    assert explicit.status_code == 201, explicit.text


def test_import_matches_ugc_post_urn_layout_b(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    pid = _published(client, world, acc, f"urn:li:ugcPost:{SHARE_B}")
    res = _import(client, world, layout_b(SHARE_B))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["post_id"] == pid and body["clicks"] == 21 and body["impressions"] == 2500
    assert body["demographics"][0]["share"] == 0.36 and body["top"] is None
    captured = datetime.fromisoformat(body["captured_at"])
    assert abs((datetime.now(UTC) - captured).total_seconds()) < 120  # layout B: now


def test_post_not_found_then_create_missing(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    now = datetime.now(UTC)
    published = now - timedelta(days=6)
    data = _a(NEW_SHARE, published=published, exported=now - timedelta(days=1))

    missing = _import(client, world, data)
    assert missing.status_code == 404
    detail = missing.json()["detail"]
    assert detail["code"] == "post_not_found" and detail["share_id"] == NEW_SHARE
    assert detail["post_url"].endswith(f"urn:li:share:{NEW_SHARE}")
    assert datetime.fromisoformat(detail["published_at_hint"]) == published.replace(
        hour=5, minute=30, second=0, microsecond=0
    )
    assert client.get(_base(world)).json() == []  # nothing was created by the failed attempt

    no_account = _import(client, world, data, create_missing="true")
    assert no_account.status_code == 422 and no_account.json()["detail"] == "account_required"
    foreign = _import(client, world, data, create_missing="true", account_id=str(uuid.uuid4()))
    assert foreign.status_code == 404 and foreign.json()["detail"] == "account_not_found"
    long_text = _import(client, world, data, create_missing="true", account_id=str(acc), text="x" * 3001)
    assert long_text.status_code == 422 and long_text.json()["detail"] == "text_too_long"

    created = _import(
        client, world, data, create_missing="true", account_id=str(acc), text="  Pasted post text  "
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["created"] is True and body["impressions"] == 1200
    post = client.get(f"{_base(world)}/{body['post_id']}").json()
    assert post["status"] == "published" and post["external_source"] == "linkedin_export"
    assert post["body"] == "Pasted post text" and post["linkedin_post_urn"] == f"urn:li:share:{NEW_SHARE}"
    assert datetime.fromisoformat(post["published_at"]) == published.replace(
        hour=5, minute=30, second=0, microsecond=0
    )
    listed = client.get(_base(world)).json()
    assert [p["external_source"] for p in listed] == ["linkedin_export"]

    # The same export again finds the post that was just created and is skipped.
    repeat = _import(client, world, data, create_missing="true", account_id=str(acc))
    assert repeat.status_code == 409 and repeat.json()["detail"] == "already_imported"
    actions = [a for a, _ in _audit_actions()]
    assert actions.count("post.imported") == 1 and actions.count("metrics.imported") == 1
    created_audit = next(d for a, d in _audit_actions() if a == "post.imported")
    assert created_audit and created_audit["share_id"] == NEW_SHARE and "Pasted" not in str(created_audit)


def test_create_missing_layout_b_localises_publish_time_and_leaves_text_empty(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
) -> None:
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    when = datetime.now(UTC) - timedelta(days=8)
    data = layout_b(NEW_SHARE, date=f"{when.month}/{when.day}/{when.year}", time="8:30 AM")
    res = _import(client, world, data, create_missing="true", account_id=str(acc))
    assert res.status_code == 201, res.text
    post = client.get(f"{_base(world)}/{res.json()['post_id']}").json()
    assert post["body"] == ""  # the UI shows its own placeholder; the owner can paste the text later
    rep = client.get(f"/api/v1/workspaces/{world.workspace_id}/reports").json()
    assert [p["snippet"] for p in rep["top_posts"]] == [""]
    local = datetime.fromisoformat(post["published_at"]).astimezone(ZoneInfo("Asia/Jerusalem"))
    assert (local.hour, local.minute) == (8, 30)  # the file's wall-clock time is the workspace's local time


def test_import_validation_and_permissions(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    now = datetime.now(UTC)
    _published(client, world, acc, f"urn:li:share:{SHARE_A}")
    good = _a(SHARE_A, published=now - timedelta(days=12), exported=now - timedelta(days=1))

    assert _import(client, world, b"definitely not a workbook").status_code == 415
    assert _import(client, world, make_xlsx({"Other": [["a"]]})).status_code == 422
    url = f"/api/v1/workspaces/{world.workspace_id}/metrics/import"
    assert client.post(url, json={}, headers=_h(client)).status_code == 422  # not multipart
    huge = client.post(
        url,
        files={"file": ("big.xlsx", b"\x00" * (2 * 1024 * 1024 + 100_000), "application/octet-stream")},
        headers=_h(client),
    )
    assert huge.status_code == 413 and huge.json()["detail"] == "file_too_large"
    assert (
        client.post(url, data={"create_missing": "true"}, headers=_h(client)).status_code == 422
    )  # no file at all
    odd = client.post(
        url,
        files={"file": ("a.xlsx", good, "application/octet-stream")},
        data={"surprise": "1"},
        headers=_h(client),
    )
    assert odd.status_code == 422 and odd.json()["detail"] == "invalid_form"
    bad_time = _import(client, world, good, captured_at="2026-10-01T10:00:00")  # no offset
    assert bad_time.status_code == 422
    future = _import(client, world, good, captured_at=(now + timedelta(days=2)).isoformat())
    assert future.json()["detail"] == "captured_in_future"
    early = _import(client, world, good, captured_at=(now - timedelta(days=30)).isoformat())
    assert early.json()["detail"] == "captured_before_publish"

    client.cookies.clear()
    login(client, "viewer@example.com")
    assert _import(client, world, good).status_code == 403
    client.cookies.clear()
    login(client, "stranger@example.com")
    assert _import(client, world, good).status_code == 404


def test_imported_numbers_feed_reports_and_csv(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    now = datetime.now(UTC)
    _published(client, world, acc, f"urn:li:share:{SHARE_A}", days_ago=12)
    manual = _published(client, world, acc, "urn:li:share:7114500000000000050", days_ago=3)
    client.post(
        f"{_base(world)}/{manual}/metrics", json={"impressions": 100, "reactions": 5}, headers=_h(client)
    )
    assert (
        _import(
            client, world, _a(SHARE_A, published=now - timedelta(days=12), exported=now - timedelta(days=1))
        ).status_code
        == 201
    )
    created = _import(
        client,
        world,
        _a(NEW_SHARE, published=now - timedelta(days=2), exported=now - timedelta(days=1)),
        create_missing="true",
        account_id=str(acc),
    )
    assert created.status_code == 201

    rep = client.get(f"/api/v1/workspaces/{world.workspace_id}/reports").json()
    totals = rep["totals"]
    assert totals["posts"] == 3 and totals["measured"] == 3  # imported posts count as published
    assert totals["impressions"] == 100 + 1200 + 1200
    assert totals["members_reached"] == 1600 and totals["followers_gained"] == 6
    top = next(p for p in rep["top_posts"] if p["post_id"] == created.json()["post_id"])
    assert top["members_reached"] == 800 and top["followers_gained"] == 3

    csv_res = client.get(f"/api/v1/workspaces/{world.workspace_id}/reports/export.csv")
    lines = csv_res.content.decode("utf-8-sig").splitlines()
    header = lines[0].split(",")
    assert header[header.index("saves") + 1 : header.index("saves") + 3] == [
        "members_reached",
        "followers_gained",
    ]
    row = next(line for line in lines if created.json()["post_id"] in line)
    cells = row.split(",")
    assert cells[header.index("members_reached")] == "800" and cells[header.index("followers_gained")] == "3"


def _imported(c: TestClient, world: World, acc: uuid.UUID, **form: str) -> str:  # noqa: F811
    now = datetime.now(UTC)
    res = _import(
        c,
        world,
        _a(NEW_SHARE, published=now - timedelta(days=3), exported=now - timedelta(days=1)),
        create_missing="true",
        account_id=str(acc),
        **form,
    )
    assert res.status_code == 201, res.text
    return str(res.json()["post_id"])


def test_imported_post_text_can_be_set_while_published(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    pid = _imported(client, world, acc)
    url = f"{_base(world)}/{pid}"
    before = client.get(url).json()
    assert before["body"] == "" and before["version_count"] == 1

    pasted = client.patch(url, json={"body": "The original text #launch"}, headers=_h(client))
    assert pasted.status_code == 200, pasted.text
    after = pasted.json()
    assert after["status"] == "published" and after["body"] == "The original text #launch"
    assert after["version_count"] == 2 and after["media"] == [] and after["link"] is None
    assert after["linkedin_post_urn"] == before["linkedin_post_urn"]

    actions = [a for a, _ in _audit_actions()]
    assert "post.imported_text_set" in actions and "post.edited" not in actions
    changes = next(d for a, d in _audit_actions() if a == "post.imported_text_set")
    assert "original" not in str(changes)

    # Same text again is not a new version; clearing the text is allowed on an imported post.
    same = client.patch(url, json={"body": "The original text #launch"}, headers=_h(client)).json()
    assert same["version_count"] == 2
    cleared = client.patch(url, json={"body": ""}, headers=_h(client))
    assert cleared.status_code == 200 and cleared.json()["body"] == ""
    assert cleared.json()["status"] == "published" and cleared.json()["version_count"] == 3
    assert client.patch(url, json={"body": "x", "campaign_id": None}, headers=_h(client)).status_code == 200


def test_imported_post_other_fields_stay_frozen(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    pid = _imported(client, world, acc, text="Pasted at import")
    url = f"{_base(world)}/{pid}"
    payloads: list[dict[str, object]] = [
        {"media_ids": []},
        {"link": None},
        {"account_id": str(acc)},
        {"body": "ok", "media_ids": []},
    ]
    for payload in payloads:
        res = client.patch(url, json=payload, headers=_h(client))
        assert res.status_code == 409 and res.json()["detail"] == "post_not_editable", payload
    after = client.get(url).json()
    assert after["body"] == "Pasted at import" and after["version_count"] == 1

    # A published post written in Heardloop keeps its text frozen, as before.
    normal = _published(client, world, acc, "urn:li:share:7114500000000000077")
    res = client.patch(f"{_base(world)}/{normal}", json={"body": "Changed"}, headers=_h(client))
    assert res.status_code == 409 and res.json()["detail"] == "post_not_editable"
    empty = client.patch(f"{_base(world)}/{normal}", json={"body": ""}, headers=_h(client))
    assert empty.status_code == 409

    # A draft still needs text.
    draft = _create(client, world, acc, body="A draft")
    res = client.patch(f"{_base(world)}/{draft['id']}", json={"body": "  "}, headers=_h(client))
    assert res.status_code == 422 and res.json()["detail"] == "body_required"

    client.cookies.clear()
    login(client, "viewer@example.com")
    assert client.patch(url, json={"body": "Nope"}, headers=_h(client)).status_code == 403


def test_concurrent_import_of_a_new_post_ends_with_one_post(
    client: TestClient,  # noqa: F811
    world: World,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    urn = f"urn:li:share:{NEW_SHARE}"
    rival: dict[str, uuid.UUID] = {}

    async def insert_rival() -> None:
        assert URL
        engine = create_async_engine(URL)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            now = datetime.now(UTC)
            post = Post(
                workspace_id=world.workspace_id,
                account_id=acc,
                status=PostStatus.PUBLISHED,
                published_at=now - timedelta(days=3),
                linkedin_post_urn=urn,
                external_source="linkedin_export",
                created_at=now,
                updated_at=now,
            )
            db.add(post)
            await db.flush()
            author = (await db.execute(select(User.id).limit(1))).scalar_one()
            version = PostVersion(post_id=post.id, body="", media_ids=[], author_id=author, created_at=now)
            db.add(version)
            await db.flush()
            post.current_version_id = version.id
            await db.commit()
            rival["id"] = post.id
        await engine.dispose()

    real_flush = AsyncSession.flush
    fired = {"done": False}

    async def racing_flush(self: AsyncSession, objects: object = None) -> None:
        if not fired["done"] and any(isinstance(o, Post) and o.linkedin_post_urn == urn for o in self.new):
            fired["done"] = True
            await insert_rival()  # another upload commits the same URN just before our flush
        await real_flush(self, objects)  # type: ignore[arg-type]

    monkeypatch.setattr(AsyncSession, "flush", racing_flush)
    now = datetime.now(UTC)
    res = _import(
        client,
        world,
        _a(NEW_SHARE, published=now - timedelta(days=3), exported=now - timedelta(days=1)),
        create_missing="true",
        account_id=str(acc),
        text="Loser's text",
    )
    assert fired["done"] and res.status_code == 201, res.text
    body = res.json()
    assert body["created"] is False and body["post_id"] == str(rival["id"])
    listed = client.get(_base(world)).json()
    assert [p["id"] for p in listed] == [str(rival["id"])]
    assert len(client.get(f"{_base(world)}/{rival['id']}/metrics").json()) == 1
    actions = [a for a, _ in _audit_actions()]
    assert "post.imported" not in actions and actions.count("metrics.imported") == 1


# ---- import from the post's LinkedIn link (FR-29) ----

LINK_ACTIVITY = "7519065369198620675"
LINK_UGC = "7518638656480395265"


async def _post_ids(urn_like: str) -> list[tuple[str | None, str | None, str | None]]:
    assert URL
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(Post.linkedin_post_urn, Post.linkedin_activity_urn, Post.external_source).where(
                    Post.linkedin_post_urn.like(f"%{urn_like}")
                )
            )
        ).all()
    await engine.dispose()
    return [tuple(r) for r in rows]


def test_import_from_post_link(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    link = f"https://www.linkedin.com/feed/update/urn:li:activity:{LINK_ACTIVITY}/"
    data = layout_b(LINK_UGC, vanity="ugcPost")
    create = {"create_missing": "true", "account_id": str(acc), "text": "My own post"}

    short = _import(client, world, data, post_link="https://lnkd.in/p/d9-544yc", **create)
    assert short.status_code == 422 and short.json()["detail"] == "short_link"
    other_site = _import(client, world, data, post_link="https://example.com/x", **create)
    assert other_site.json()["detail"] == "not_linkedin"
    renamed = _import(client, world, data, post_link=link, **create)  # no id from the file name
    assert renamed.status_code == 422 and renamed.json()["detail"] == "export_not_for_link"
    wrong = _import(client, world, data, post_link=link, file_activity_id="7519065369198620999", **create)
    assert wrong.json()["detail"] == "export_not_for_link"
    bad_id = _import(client, world, data, post_link=link, file_activity_id="abc", **create)
    assert bad_id.json()["detail"] == "invalid_file_activity_id"
    other_share = _import(
        client,
        world,
        data,
        post_link=f"https://www.linkedin.com/feed/update/urn:li:share:{LINK_ACTIVITY}",
        **create,
    )
    assert other_share.json()["detail"] == "export_not_for_link"
    assert asyncio.run(_post_ids(LINK_UGC)) == []  # nothing was created by the refused attempts

    res = _import(client, world, data, post_link=link, file_activity_id=LINK_ACTIVITY, **create)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["created"] is True and body["impressions"] == 2500
    post = client.get(f"{_base(world)}/{body['post_id']}").json()
    assert post["status"] == "published" and post["body"] == "My own post"
    assert post["linkedin_url"] == f"https://www.linkedin.com/feed/update/urn:li:activity:{LINK_ACTIVITY}/"
    assert asyncio.run(_post_ids(LINK_UGC)) == [
        (f"urn:li:ugcPost:{LINK_UGC}", f"urn:li:activity:{LINK_ACTIVITY}", "linkedin_export")
    ]

    # The same link again matches that post instead of creating a second one.
    again = _import(client, world, data, post_link=link, file_activity_id=LINK_ACTIVITY, **create)
    assert again.status_code == 409 and again.json()["detail"] == "already_imported"
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    later = _import(
        client,
        world,
        data,
        post_link=f"https://www.linkedin.com/posts/someone_topic-ugcPost-{LINK_UGC}-ls-K",
        captured_at=yesterday,
        **create,
    )
    assert later.status_code == 201 and later.json()["created"] is False
    assert later.json()["post_id"] == body["post_id"]
    assert len(asyncio.run(_post_ids(LINK_UGC))) == 1

    audit = [d for a, d in _audit_actions() if a == "metrics.imported"]
    assert audit[0] is not None and audit[0]["via_link"] == "activity"
    assert "My own post" not in str(_audit_actions())  # post text never reaches the activity log


def test_older_imports_get_the_right_id_kind(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    old_id = "7518638656480395266"
    pid = _published(client, world, acc, f"urn:li:share:{old_id}")

    async def mark_imported() -> None:
        assert URL
        engine = create_async_engine(URL)
        async with engine.begin() as conn:
            await conn.execute(
                update(Post).where(Post.id == uuid.UUID(pid)).values(external_source="linkedin_export")
            )
        await engine.dispose()

    asyncio.run(mark_imported())
    res = _import(client, world, layout_b(old_id, vanity="ugcPost"))
    assert res.status_code == 201 and res.json()["post_id"] == pid
    assert asyncio.run(_post_ids(old_id)) == [(f"urn:li:ugcPost:{old_id}", None, "linkedin_export")]

    # A post published through Heardloop keeps the id LinkedIn's API returned.
    own = _published(client, world, acc, "urn:li:share:7518638656480395267")
    res = _import(client, world, layout_b("7518638656480395267", vanity="ugcPost"))
    assert res.status_code == 201 and res.json()["post_id"] == own
    assert asyncio.run(_post_ids("7518638656480395267"))[0][0] == "urn:li:share:7518638656480395267"
