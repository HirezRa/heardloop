"""Authenticated version and update status. Anonymous callers use /health only (no version)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from heardloop.api.deps import CurrentPrincipal, Db
from heardloop.config import get_settings
from heardloop.models import Membership, WorkspaceRole
from heardloop.timeutil import utcnow
from heardloop.updates import (
    UpdateCheckState,
    load_update_state,
    public_update_field,
    public_update_status,
    run_update_check,
)
from heardloop.version import app_version, git_sha

router = APIRouter(prefix="/api/v1", tags=["version"])


class VersionOut(BaseModel):
    version: str
    git_sha: str
    update: dict[str, Any] | None = None
    update_status: dict[str, Any] | None = None


async def _is_owner(db: Db, user_id: object) -> bool:
    row = await db.execute(select(Membership.role).where(Membership.user_id == user_id).limit(1))
    role = row.scalar_one_or_none()
    return role == WorkspaceRole.OWNER


def _owner_payload(
    state: UpdateCheckState | None, current: str
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    settings = get_settings()
    return (
        public_update_field(state, current),
        public_update_status(state, current, enabled=settings.update_check),
    )


@router.get("/version")
async def get_version(principal: CurrentPrincipal, db: Db) -> VersionOut:
    # Read identity before any later session reset/rollback in callers.
    user_id = principal.user.id
    current = app_version()
    state = await load_update_state(db)
    update = None
    update_status = None
    if await _is_owner(db, user_id):
        update, update_status = _owner_payload(state, current)
    return VersionOut(version=current, git_sha=git_sha(), update=update, update_status=update_status)


@router.post("/version/check")
async def check_version(request: Request, principal: CurrentPrincipal, db: Db) -> VersionOut:
    """Owner-only: run the GitHub check now and return the same payload as GET /version."""
    user_id = principal.user.id
    if not await _is_owner(db, user_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    factory = request.app.state.session_factory
    http = request.app.state.http
    await run_update_check(factory, http, clock=utcnow)
    # Fresh read of app_meta written in another session (no rollback — that expired principal.user).
    current = app_version()
    state = await load_update_state(db, populate_existing=True)
    update, update_status = _owner_payload(state, current)
    return VersionOut(version=current, git_sha=git_sha(), update=update, update_status=update_status)
