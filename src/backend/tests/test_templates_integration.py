"""Post templates CRUD, placeholders and save-as-template (FR-12). Needs TEST_DATABASE_URL."""

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from heardloop.models import AuditLogEntry, Campaign, CampaignStatus
from tests.test_auth_integration import World, client, login, world  # noqa: F401
from tests.test_posts_integration import _add_account, _base, _h

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def _tpl(world: World) -> str:  # noqa: F811
    return f"/api/v1/workspaces/{world.workspace_id}/templates"


def test_template_crud_permissions_and_unique_name(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "viewer@example.com")
    assert client.get(_tpl(world), headers=_h(client)).status_code == 200
    assert (
        client.post(_tpl(world), json={"name": "Hello", "body": "Hi"}, headers=_h(client)).status_code == 403
    )

    client.cookies.clear()
    login(client, "stranger@example.com")
    assert client.get(_tpl(world), headers=_h(client)).status_code == 404

    client.cookies.clear()
    login(client, "editor@example.com")
    created = client.post(
        _tpl(world),
        json={"name": "Launch", "body": "Hello {{name}} on {{date}} about {{topic}}", "tags": ["promo"]},
        headers=_h(client),
    )
    assert created.status_code == 201, created.text
    tid = created.json()["id"]
    assert created.json()["use_count"] == 0

    dup = client.post(_tpl(world), json={"name": "launch", "body": "x"}, headers=_h(client))
    assert dup.status_code == 409 and dup.json()["detail"] == "template_name_taken"

    patched = client.patch(
        f"{_tpl(world)}/{tid}", json={"body": "Updated {{name}}"}, headers=_h(client)
    ).json()
    assert patched["body"] == "Updated {{name}}"

    archived = client.post(f"{_tpl(world)}/{tid}/archive", headers=_h(client)).json()
    assert archived["archived_at"] is not None
    assert all(t["id"] != tid for t in client.get(_tpl(world), headers=_h(client)).json())
    assert any(t["id"] == tid for t in client.get(f"{_tpl(world)}?archived=true", headers=_h(client)).json())

    # Same name allowed while the first is archived.
    again = client.post(_tpl(world), json={"name": "Launch", "body": "new"}, headers=_h(client))
    assert again.status_code == 201

    # Unarchive conflicts with the active twin.
    conflict = client.post(f"{_tpl(world)}/{tid}/unarchive", headers=_h(client))
    assert conflict.status_code == 409 and conflict.json()["detail"] == "template_name_taken"


def test_delete_refused_when_used(client: TestClient, world: World) -> None:  # noqa: F811
    login(client, "editor@example.com")
    tid = client.post(
        _tpl(world), json={"name": "Used once", "body": "Hi {{name}}"}, headers=_h(client)
    ).json()["id"]
    used = client.post(f"{_tpl(world)}/{tid}/use", headers=_h(client)).json()
    assert "{{name}}" not in used["body"]
    assert used["placeholders_left"] == []

    client.cookies.clear()
    login(client, "owner@example.com")
    res = client.delete(f"{_tpl(world)}/{tid}", headers=_h(client))
    assert res.status_code == 409 and res.json()["detail"] == "template_in_use"

    fresh = client.post(_tpl(world), json={"name": "Disposable", "body": ""}, headers=_h(client))
    assert fresh.status_code == 201, fresh.text
    assert client.delete(f"{_tpl(world)}/{fresh.json()['id']}", headers=_h(client)).status_code == 204


def test_use_fills_placeholders_and_reports_leftovers(client: TestClient, world: World) -> None:  # noqa: F811
    camp_id = asyncio.run(_add_campaign(world, "Spring"))
    login(client, "editor@example.com")
    tid = client.post(
        _tpl(world),
        json={
            "name": "Placeholders",
            "body": "Hi {{name}} on {{date}} for {{campaign}} at {{link}} #tag {{topic}} {{custom_topic}}",
            "campaign_id": str(camp_id),
            "link": {"url": "https://example.com/a", "title": "A", "description": None},
        },
        headers=_h(client),
    ).json()["id"]
    used = client.post(f"{_tpl(world)}/{tid}/use", headers=_h(client)).json()
    assert "Editor" in used["body"]  # display_name of editor@example.com
    assert "Spring" in used["body"]
    assert "https://example.com/a" in used["body"]
    assert "{{date}}" not in used["body"]
    assert set(used["placeholders_left"]) == {"topic", "custom_topic"}
    assert "tag" in used["hashtags_in_body"]
    assert used["campaign_id"] == str(camp_id)
    assert used["link"]["url"] == "https://example.com/a"
    assert client.get(f"{_tpl(world)}/{tid}", headers=_h(client)).json()["use_count"] == 1

    actions = asyncio.run(_audit_names(world.workspace_id, "template.used"))
    assert actions and "body" not in str(actions[0]).lower()


def test_save_as_template_copies_body_and_link(client: TestClient, world: World) -> None:  # noqa: F811
    acc = asyncio.run(_add_account(world))
    login(client, "editor@example.com")
    post = client.post(
        _base(world),
        json={
            "account_id": str(acc),
            "body": "Post body for template",
            "link": {"url": "https://example.com/p", "title": "P", "description": "d"},
        },
        headers=_h(client),
    ).json()
    saved = client.post(
        f"{_base(world)}/{post['id']}/save-as-template",
        json={"name": "From post", "tags": ["from-post"]},
        headers=_h(client),
    )
    assert saved.status_code == 201, saved.text
    data = saved.json()
    assert data["body"] == "Post body for template"
    assert data["link"]["url"] == "https://example.com/p"
    assert data["tags"] == ["from-post"]


async def _add_campaign(world: World, name: str) -> uuid.UUID:  # noqa: F811
    assert URL
    engine = create_async_engine(URL)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        c = Campaign(
            workspace_id=world.workspace_id,
            name=name,
            status=CampaignStatus.ACTIVE,
            tags=[],
            created_at=datetime.now(UTC),
        )
        db.add(c)
        await db.commit()
        cid = c.id
    await engine.dispose()
    return cid


async def _audit_names(workspace_id: uuid.UUID, action: str) -> list[dict]:  # type: ignore[type-arg]
    assert URL
    engine = create_async_engine(URL)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        rows = list(
            (
                await db.execute(
                    select(AuditLogEntry.diff_json).where(
                        AuditLogEntry.workspace_id == workspace_id, AuditLogEntry.action == action
                    )
                )
            ).scalars()
        )
    await engine.dispose()
    return [dict(r) if r else {} for r in rows]
