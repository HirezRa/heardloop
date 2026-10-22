"""Audiences (FR-28): the markets a workspace writes for, each with its own time zone and weekend.

Members read them (the editor and the reports need the list); owners manage them. Deleting an audience
that posts or campaigns still use is refused: that would silently move those posts back to the
workspace time zone in every report.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from heardloop import audit
from heardloop.api.deps import Db, WorkspaceAccess, client_ip, require_client_header, require_permission
from heardloop.api.schemas import AudienceCreate, AudienceOut, AudiencePreset, AudienceUpdate
from heardloop.audiences import PRESETS
from heardloop.auth.permissions import Permission
from heardloop.models import Audience, Campaign, Post, Workspace
from heardloop.timeutil import utcnow

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/audiences",
    tags=["audiences"],
    dependencies=[Depends(require_client_header)],
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
ManageAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.SETTINGS_MANAGE))]

MAX_AUDIENCES = 20  # per workspace; more than that is no longer a handful of markets


async def _post_counts(db: Db, audience_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not audience_ids:
        return {}
    rows = await db.execute(
        select(Post.audience_id, func.count())
        .where(Post.audience_id.in_(audience_ids))
        .group_by(Post.audience_id)
    )
    counts = {aid: 0 for aid in audience_ids}
    for aid, n in rows.all():
        counts[aid] = n
    return counts


def _out(a: Audience, post_count: int) -> AudienceOut:
    return AudienceOut(
        id=a.id,
        code=a.code,
        name=a.name,
        time_zone=a.time_zone,
        weekend_days=list(a.weekend_days or []),
        is_default=a.is_default,
        position=a.position,
        post_count=post_count,
        created_at=a.created_at,
    )


async def _get(db: Db, access: WorkspaceAccess, audience_id: uuid.UUID, *, lock: bool = False) -> Audience:
    stmt = select(Audience).where(Audience.id == audience_id, Audience.workspace_id == access.workspace_id)
    if lock:
        stmt = stmt.with_for_update()
    audience = (await db.execute(stmt)).scalar_one_or_none()
    if audience is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audience_not_found")
    return audience


def _record(
    db: Db, access: WorkspaceAccess, request: Request, action: str, a: Audience, **changes: Any
) -> None:
    audit.record(
        db,
        action=action,
        entity="audience",
        entity_id=a.id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes=changes or None,
        now=utcnow(),
    )


async def _clear_default(db: Db, workspace_id: uuid.UUID, keep: uuid.UUID | None) -> None:
    """At most one default per workspace (enforced by a partial unique index too)."""
    rows = await db.execute(
        select(Audience)
        .where(Audience.workspace_id == workspace_id, Audience.is_default.is_(True))
        .with_for_update()
    )
    for other in rows.scalars():
        if other.id != keep:
            other.is_default = False
    await db.flush()


@router.get("")
async def list_audiences(access: ReadAccess, db: Db) -> list[AudienceOut]:
    rows = await db.execute(
        select(Audience)
        .where(Audience.workspace_id == access.workspace_id)
        .order_by(Audience.position, Audience.created_at)
    )
    audiences = list(rows.scalars())
    counts = await _post_counts(db, [a.id for a in audiences])
    return [_out(a, counts.get(a.id, 0)) for a in audiences]


@router.get("/presets")
async def list_presets(access: ReadAccess) -> list[AudiencePreset]:
    """Starting points for common markets; the owner can change any field after adding one."""
    return [
        AudiencePreset(code=p.code, name=p.name, time_zone=p.time_zone, weekend_days=list(p.weekend_days))
        for p in PRESETS
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_audience(
    body: AudienceCreate, access: ManageAccess, request: Request, db: Db
) -> AudienceOut:
    # Lock the workspace row so two concurrent creates cannot both pass the limit check.
    await db.execute(select(Workspace.id).where(Workspace.id == access.workspace_id).with_for_update())
    existing = (
        await db.execute(
            select(func.count()).select_from(Audience).where(Audience.workspace_id == access.workspace_id)
        )
    ).scalar_one()
    if existing >= MAX_AUDIENCES:
        raise HTTPException(status.HTTP_409_CONFLICT, "too_many_audiences")
    max_position = (
        await db.execute(
            select(func.coalesce(func.max(Audience.position), -1)).where(
                Audience.workspace_id == access.workspace_id
            )
        )
    ).scalar_one()
    if body.is_default:
        await _clear_default(db, access.workspace_id, keep=None)
    audience = Audience(
        workspace_id=access.workspace_id,
        code=body.code,
        name=body.name,
        time_zone=body.time_zone,
        weekend_days=list(body.weekend_days),
        is_default=body.is_default or existing == 0,  # the first audience becomes the default
        position=max_position + 1,
        created_at=utcnow(),
    )
    db.add(audience)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "audience_code_taken") from None
    _record(
        db,
        access,
        request,
        "audience.created",
        audience,
        code=audience.code,
        time_zone=audience.time_zone,
        weekend_days=list(audience.weekend_days),
        is_default=audience.is_default,
    )
    await db.commit()
    return _out(audience, 0)


@router.patch("/{audience_id}")
async def update_audience(
    audience_id: uuid.UUID, body: AudienceUpdate, access: ManageAccess, request: Request, db: Db
) -> AudienceOut:
    audience = await _get(db, access, audience_id, lock=True)
    changes: dict[str, Any] = {}
    for field in body.model_fields_set:
        new = getattr(body, field)
        if new is None:
            continue  # every column is required: null means "no change"
        old = getattr(audience, field)
        if field == "is_default":
            if new and not old:
                await _clear_default(db, access.workspace_id, keep=audience.id)
            elif not new and old:
                # Switching the default off leaves the workspace without one, which is allowed.
                pass
        if field == "weekend_days":
            new = list(new)
            old = list(old or [])
        if new != old:
            changes[field] = {"from": old, "to": new}
            setattr(audience, field, new)
    if changes:
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "audience_code_taken") from None
        _record(db, access, request, "audience.updated", audience, **changes)
        await db.commit()
    counts = await _post_counts(db, [audience.id])
    return _out(audience, counts.get(audience.id, 0))


@router.delete("/{audience_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audience(audience_id: uuid.UUID, access: ManageAccess, request: Request, db: Db) -> None:
    """Refused while posts or campaigns still use the audience (else they would fall back to the
    workspace zone)."""
    audience = await _get(db, access, audience_id, lock=True)
    used_by_posts = (
        await db.execute(select(func.count()).select_from(Post).where(Post.audience_id == audience.id))
    ).scalar_one()
    used_by_campaigns = (
        await db.execute(
            select(func.count()).select_from(Campaign).where(Campaign.audience_id == audience.id)
        )
    ).scalar_one()
    if used_by_posts or used_by_campaigns:
        raise HTTPException(status.HTTP_409_CONFLICT, "audience_in_use")
    _record(db, access, request, "audience.deleted", audience, code=audience.code)
    await db.delete(audience)
    await db.commit()
