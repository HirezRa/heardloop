"""Post templates: reusable starting points with placeholders (FR-12)."""

import re
import uuid
from datetime import date
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from heardloop import audit
from heardloop.api.deps import Db, WorkspaceAccess, client_ip, require_client_header, require_permission
from heardloop.api.schemas import (
    LinkCard,
    SaveAsTemplateRequest,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
    TemplateUseOut,
)
from heardloop.auth.permissions import Permission
from heardloop.models import Campaign, Post, PostTemplate, PostVersion, User, Workspace
from heardloop.timeutil import utcnow

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/templates",
    tags=["templates"],
    dependencies=[Depends(require_client_header)],
)

# save-as-template lives on the posts path but shares template helpers.
posts_router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/posts",
    tags=["templates"],
    dependencies=[Depends(require_client_header)],
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
WriteAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.POSTS_WRITE))]
OwnerAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.SETTINGS_MANAGE))]

MAX_TEMPLATES = 500
# Built-ins filled on use; anything else stays as {{word}} and is listed in placeholders_left.
_PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_\u058f-\u05ff]{1,32})\}\}")
# Same hashtag rule as the post editor (routes_posts._HASHTAG).
_HASHTAG = re.compile(r"(?<![\w#])#([^\W_][\w\u0591-\u05C7\u0300-\u036F]*)", re.UNICODE)


def _link_of(template: PostTemplate) -> LinkCard | None:
    if not template.link_url or not template.link_title:
        return None
    return LinkCard(url=template.link_url, title=template.link_title, description=template.link_description)


async def _out(db: Db, template: PostTemplate) -> TemplateOut:
    campaign_name: str | None = None
    if template.campaign_id is not None:
        campaign = await db.get(Campaign, template.campaign_id)
        campaign_name = campaign.name if campaign else None
    return TemplateOut(
        id=template.id,
        name=template.name,
        body=template.body,
        link=_link_of(template),
        campaign_id=template.campaign_id,
        campaign_name=campaign_name,
        tags=list(template.tags or []),
        created_by=template.created_by,
        created_at=template.created_at,
        updated_at=template.updated_at,
        use_count=template.use_count,
        archived_at=template.archived_at,
    )


def _record(
    db: Db, access: WorkspaceAccess, request: Request, action: str, template: PostTemplate, **changes: Any
) -> None:
    # Ids and names only - never body text (audit privacy).
    audit.record(
        db,
        action=action,
        entity="template",
        entity_id=template.id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes=changes or None,
        now=utcnow(),
    )


async def _get(
    db: Db, access: WorkspaceAccess, template_id: uuid.UUID, *, lock: bool = False
) -> PostTemplate:
    stmt = select(PostTemplate).where(
        PostTemplate.id == template_id, PostTemplate.workspace_id == access.workspace_id
    )
    if lock:
        stmt = stmt.with_for_update()
    template = (await db.execute(stmt)).scalar_one_or_none()
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template_not_found")
    return template


async def _campaign(db: Db, workspace_id: uuid.UUID, campaign_id: uuid.UUID | None) -> Campaign | None:
    if campaign_id is None:
        return None
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None or campaign.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign_not_found")
    return campaign


async def _require_unique_name(
    db: Db, workspace_id: uuid.UUID, name: str, *, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(PostTemplate.id).where(
        PostTemplate.workspace_id == workspace_id,
        PostTemplate.archived_at.is_(None),
        func.lower(PostTemplate.name) == name.lower(),
    )
    if exclude_id is not None:
        stmt = stmt.where(PostTemplate.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "template_name_taken")


def _fill_placeholders(
    body: str, *, actor_name: str, today: date, campaign_name: str, link_url: str
) -> tuple[str, list[str]]:
    left: list[str] = []
    seen: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "name":
            return actor_name
        if key == "date":
            return today.isoformat()
        if key == "campaign":
            return campaign_name
        if key == "link":
            return link_url
        if key not in seen:
            seen.add(key)
            left.append(key)
        return match.group(0)

    return _PLACEHOLDER.sub(repl, body), left


def _hashtags_in(body: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _HASHTAG.finditer(body):
        key = m.group(1).casefold()
        if key not in seen:
            seen.add(key)
            out.append(m.group(1))
    return out


@router.get("")
async def list_templates(
    access: ReadAccess,
    db: Db,
    archived: Annotated[bool, Query()] = False,
) -> list[TemplateOut]:
    stmt = select(PostTemplate).where(PostTemplate.workspace_id == access.workspace_id)
    if not archived:
        stmt = stmt.where(PostTemplate.archived_at.is_(None))
    stmt = stmt.order_by(PostTemplate.use_count.desc(), PostTemplate.updated_at.desc()).limit(MAX_TEMPLATES)
    return [await _out(db, t) for t in (await db.execute(stmt)).scalars()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(body: TemplateCreate, access: WriteAccess, request: Request, db: Db) -> TemplateOut:
    await db.execute(select(Workspace.id).where(Workspace.id == access.workspace_id).with_for_update())
    existing = (
        await db.execute(
            select(func.count())
            .select_from(PostTemplate)
            .where(PostTemplate.workspace_id == access.workspace_id)
        )
    ).scalar_one()
    if existing >= MAX_TEMPLATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "too_many_templates")
    await _campaign(db, access.workspace_id, body.campaign_id)
    await _require_unique_name(db, access.workspace_id, body.name)
    now = utcnow()
    template = PostTemplate(
        workspace_id=access.workspace_id,
        name=body.name,
        body=body.body,
        link_url=body.link.url if body.link else None,
        link_title=body.link.title if body.link else None,
        link_description=body.link.description if body.link else None,
        campaign_id=body.campaign_id,
        tags=body.tags,
        created_by=access.principal.user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(template)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "template_name_taken") from None
    _record(db, access, request, "template.created", template, name=template.name)
    await db.commit()
    return await _out(db, template)


@router.get("/{template_id}")
async def get_template(template_id: uuid.UUID, access: ReadAccess, db: Db) -> TemplateOut:
    return await _out(db, await _get(db, access, template_id))


@router.patch("/{template_id}")
async def update_template(
    template_id: uuid.UUID, body: TemplateUpdate, access: WriteAccess, request: Request, db: Db
) -> TemplateOut:
    template = await _get(db, access, template_id, lock=True)
    changes: dict[str, Any] = {}
    now = utcnow()
    if "name" in body.model_fields_set and body.name is not None and body.name != template.name:
        await _require_unique_name(db, access.workspace_id, body.name, exclude_id=template.id)
        changes["name"] = {"from": template.name, "to": body.name}
        template.name = body.name
    if "body" in body.model_fields_set and body.body is not None and body.body != template.body:
        changes["body"] = {"changed": True}
        template.body = body.body
    if "link" in body.model_fields_set:
        new_link = body.link
        old_link = _link_of(template)
        if new_link != old_link:
            changes["link"] = {"changed": True}
            template.link_url = new_link.url if new_link else None
            template.link_title = new_link.title if new_link else None
            template.link_description = new_link.description if new_link else None
    if "campaign_id" in body.model_fields_set and body.campaign_id != template.campaign_id:
        await _campaign(db, access.workspace_id, body.campaign_id)
        changes["campaign_id"] = {"changed": True}
        template.campaign_id = body.campaign_id
    if "tags" in body.model_fields_set and body.tags is not None and body.tags != list(template.tags or []):
        changes["tags"] = {"changed": True}
        template.tags = body.tags
    if changes:
        template.updated_at = now
        _record(db, access, request, "template.updated", template, name=template.name, **changes)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "template_name_taken") from None
    return await _out(db, template)


@router.post("/{template_id}/archive")
async def archive_template(
    template_id: uuid.UUID, access: WriteAccess, request: Request, db: Db
) -> TemplateOut:
    template = await _get(db, access, template_id, lock=True)
    if template.archived_at is None:
        template.archived_at = utcnow()
        template.updated_at = template.archived_at
        _record(db, access, request, "template.archived", template, name=template.name)
        await db.commit()
    return await _out(db, template)


@router.post("/{template_id}/unarchive")
async def unarchive_template(
    template_id: uuid.UUID, access: WriteAccess, request: Request, db: Db
) -> TemplateOut:
    template = await _get(db, access, template_id, lock=True)
    if template.archived_at is not None:
        await _require_unique_name(db, access.workspace_id, template.name, exclude_id=template.id)
        template.archived_at = None
        template.updated_at = utcnow()
        _record(db, access, request, "template.unarchived", template, name=template.name)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "template_name_taken") from None
    return await _out(db, template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: uuid.UUID, access: OwnerAccess, request: Request, db: Db) -> None:
    """Owners only. Refused when the template has been used - archive instead."""
    template = await _get(db, access, template_id, lock=True)
    if template.use_count > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "template_in_use")
    _record(db, access, request, "template.deleted", template, name=template.name)
    await db.delete(template)
    await db.commit()


@router.post("/{template_id}/use")
async def use_template(
    template_id: uuid.UUID, access: WriteAccess, request: Request, db: Db
) -> TemplateUseOut:
    """Fills built-in placeholders and increments use_count. Does not create a post."""
    template = await _get(db, access, template_id, lock=True)
    if template.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "template_archived")
    ws = await db.get(Workspace, access.workspace_id)
    tz = ZoneInfo(ws.time_zone if ws else "UTC")
    today = utcnow().astimezone(tz).date()
    campaign = await db.get(Campaign, template.campaign_id) if template.campaign_id else None
    campaign_name = campaign.name if campaign else ""
    link_url = template.link_url or ""
    actor = access.principal.user
    # Prefer a fresh display name from the users table (session principal is enough, but be explicit).
    user = await db.get(User, actor.id)
    actor_name = user.display_name if user else actor.display_name
    filled, left = _fill_placeholders(
        template.body,
        actor_name=actor_name,
        today=today,
        campaign_name=campaign_name,
        link_url=link_url,
    )
    template.use_count += 1
    template.updated_at = utcnow()
    _record(db, access, request, "template.used", template, name=template.name)
    await db.commit()
    return TemplateUseOut(
        body=filled,
        link=_link_of(template),
        campaign_id=template.campaign_id,
        hashtags_in_body=_hashtags_in(filled),
        placeholders_left=left,
    )


@posts_router.post("/{post_id}/save-as-template", status_code=status.HTTP_201_CREATED)
async def save_as_template(
    post_id: uuid.UUID, body: SaveAsTemplateRequest, access: WriteAccess, request: Request, db: Db
) -> TemplateOut:
    """Copies the current version's body and link into a new template. Media is not copied."""
    post = await db.get(Post, post_id)
    if post is None or post.workspace_id != access.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post_not_found")
    current = await db.get(PostVersion, post.current_version_id) if post.current_version_id else None
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post_not_found")
    await db.execute(select(Workspace.id).where(Workspace.id == access.workspace_id).with_for_update())
    existing = (
        await db.execute(
            select(func.count())
            .select_from(PostTemplate)
            .where(PostTemplate.workspace_id == access.workspace_id)
        )
    ).scalar_one()
    if existing >= MAX_TEMPLATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "too_many_templates")
    await _require_unique_name(db, access.workspace_id, body.name)
    now = utcnow()
    template = PostTemplate(
        workspace_id=access.workspace_id,
        name=body.name,
        body=current.body,
        link_url=current.link_url,
        link_title=current.link_title,
        link_description=current.link_description,
        campaign_id=post.campaign_id,
        tags=body.tags,
        created_by=access.principal.user.id,
        created_at=now,
        updated_at=now,
    )
    db.add(template)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "template_name_taken") from None
    _record(db, access, request, "template.created", template, name=template.name)
    await db.commit()
    return await _out(db, template)
