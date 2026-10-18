"""Authenticated version and update status. Anonymous callers use /health only (no version)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from heardloop.api.deps import CurrentPrincipal, Db
from heardloop.models import Membership, WorkspaceRole
from heardloop.timeutil import utcnow
from heardloop.updates import load_update_state, public_update_field, run_update_check
from heardloop.version import app_version, git_sha

router = APIRouter(prefix="/api/v1", tags=["version"])


class VersionOut(BaseModel):
    version: str
    git_sha: str
    update: dict[str, Any] | None = None


async def _is_owner(db: Db, user_id: object) -> bool:
    row = await db.execute(select(Membership.role).where(Membership.user_id == user_id).limit(1))
    role = row.scalar_one_or_none()
    return role == WorkspaceRole.OWNER


@router.get("/version")
async def get_version(principal: CurrentPrincipal, db: Db) -> VersionOut:
    current = app_version()
    state = await load_update_state(db)
    update = None
    if await _is_owner(db, principal.user.id):
        update = public_update_field(state, current)
    return VersionOut(version=current, git_sha=git_sha(), update=update)


@router.post("/version/check")
async def check_version(request: Request, principal: CurrentPrincipal, db: Db) -> VersionOut:
    """Owner-only: run the GitHub check now and return the same payload as GET /version."""
    if not await _is_owner(db, principal.user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    factory = request.app.state.session_factory
    http = request.app.state.http
    await run_update_check(factory, http, clock=utcnow)
    # Re-read after the worker-style commit in run_update_check.
    await db.rollback()
    return await get_version(principal, db)
