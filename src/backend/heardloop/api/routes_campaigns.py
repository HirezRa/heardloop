"""Campaigns: group posts under a goal and a date range (FR-06), with a per-status post summary (FR-07)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

from heardloop import audit
from heardloop.api.deps import Db, WorkspaceAccess, client_ip, require_client_header, require_permission
from heardloop.api.schemas import CampaignCreate, CampaignOut, CampaignUpdate
from heardloop.auth.permissions import Permission
from heardloop.models import Campaign, CampaignStatus, Post, Workspace
from heardloop.timeutil import utcnow

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/campaigns",
    tags=["campaigns"],
    dependencies=[Depends(require_client_header)],
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
WriteAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.POSTS_WRITE))]

MAX_CAMPAIGNS = 500  # per workspace; keeps the list endpoint cheap


async def _counts(db: Db, campaign_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    if not campaign_ids:
        return {}
    rows = await db.execute(
        select(Post.campaign_id, Post.status, func.count())
        .where(Post.campaign_id.in_(campaign_ids))
        .group_by(Post.campaign_id, Post.status)
    )
    out: dict[uuid.UUID, dict[str, int]] = {cid: {} for cid in campaign_ids}
    for cid, st, n in rows.all():
        out[cid][st.value] = n
    return out


def _out(c: Campaign, counts: dict[str, int]) -> CampaignOut:
    return CampaignOut(
        id=c.id,
        name=c.name,
        goal=c.goal,
        audience=c.audience,
        start_date=c.start_date,
        end_date=c.end_date,
        tags=list(c.tags or []),
        color=c.color,
        status=c.status.value,
        post_counts=counts,
        created_at=c.created_at,
    )


async def _get(db: Db, access: WorkspaceAccess, campaign_id: uuid.UUID, *, lock: bool = False) -> Campaign:
    stmt = select(Campaign).where(Campaign.id == campaign_id, Campaign.workspace_id == access.workspace_id)
    if lock:
        stmt = stmt.with_for_update()
    campaign = (await db.execute(stmt)).scalar_one_or_none()
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign_not_found")
    return campaign


def _check_dates(c: Campaign) -> None:
    if c.start_date and c.end_date and c.end_date < c.start_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "end_before_start")


def _record(
    db: Db, access: WorkspaceAccess, request: Request, action: str, c: Campaign, **changes: Any
) -> None:
    audit.record(
        db,
        action=action,
        entity="campaign",
        entity_id=c.id,
        actor_id=access.principal.user.id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes=changes or None,
        now=utcnow(),
    )


@router.get("")
async def list_campaigns(
    access: ReadAccess,
    db: Db,
    include_archived: Annotated[bool, Query()] = False,
) -> list[CampaignOut]:
    stmt = select(Campaign).where(Campaign.workspace_id == access.workspace_id)
    if not include_archived:
        stmt = stmt.where(Campaign.status != CampaignStatus.ARCHIVED)
    stmt = stmt.order_by(Campaign.start_date.desc().nulls_last(), Campaign.created_at.desc()).limit(
        MAX_CAMPAIGNS
    )
    campaigns = list((await db.execute(stmt)).scalars())
    counts = await _counts(db, [c.id for c in campaigns])
    return [_out(c, counts[c.id]) for c in campaigns]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_campaign(body: CampaignCreate, access: WriteAccess, request: Request, db: Db) -> CampaignOut:
    # Lock the workspace row so two concurrent creates cannot both pass the limit check.
    await db.execute(select(Workspace.id).where(Workspace.id == access.workspace_id).with_for_update())
    existing = (
        await db.execute(
            select(func.count()).select_from(Campaign).where(Campaign.workspace_id == access.workspace_id)
        )
    ).scalar_one()
    if existing >= MAX_CAMPAIGNS:
        raise HTTPException(status.HTTP_409_CONFLICT, "too_many_campaigns")
    campaign = Campaign(
        workspace_id=access.workspace_id,
        name=body.name,
        goal=body.goal,
        audience=body.audience,
        start_date=body.start_date,
        end_date=body.end_date,
        tags=body.tags or [],
        color=body.color or "#2563eb",
        status=body.status,
        created_at=utcnow(),
    )
    _check_dates(campaign)
    db.add(campaign)
    await db.flush()
    _record(db, access, request, "campaign.created", campaign, name=campaign.name)
    await db.commit()
    return _out(campaign, {})


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: uuid.UUID, access: ReadAccess, db: Db) -> CampaignOut:
    campaign = await _get(db, access, campaign_id)
    return _out(campaign, (await _counts(db, [campaign.id]))[campaign.id])


@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: uuid.UUID, body: CampaignUpdate, access: WriteAccess, request: Request, db: Db
) -> CampaignOut:
    campaign = await _get(db, access, campaign_id, lock=True)
    changes: dict[str, Any] = {}
    for field in body.model_fields_set:
        new = getattr(body, field)
        if new is None and field in ("name", "status", "tags", "color"):
            continue  # required columns: null means "no change"
        old = getattr(campaign, field)
        if new != old:
            # Free text stays out of the activity log; only that it changed.
            changes[field] = (
                {"changed": True}
                if field in ("goal", "audience")
                else {"from": _plain(old), "to": _plain(new)}
            )
            setattr(campaign, field, new)
    _check_dates(campaign)
    if changes:
        _record(db, access, request, "campaign.updated", campaign, **changes)
        await db.commit()
    return _out(campaign, (await _counts(db, [campaign.id]))[campaign.id])


def _plain(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return list(value) if isinstance(value, list) else value


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(campaign_id: uuid.UUID, access: WriteAccess, request: Request, db: Db) -> None:
    """Only empty campaigns can be deleted; campaigns with posts are archived instead (keeps history)."""
    campaign = await _get(db, access, campaign_id, lock=True)
    used = (
        await db.execute(select(func.count()).select_from(Post).where(Post.campaign_id == campaign.id))
    ).scalar_one()
    if used:
        raise HTTPException(status.HTTP_409_CONFLICT, "campaign_has_posts")
    _record(db, access, request, "campaign.deleted", campaign, name=campaign.name)
    await db.delete(campaign)
    await db.commit()
