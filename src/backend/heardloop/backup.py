"""Backup status (FR-11): reads the manifest the backup service writes, and tells owners when it is stale.

The backup service (`src/backup`) writes `latest.json` into a directory that the API and worker mount
read-only. Only the fields of `BackupManifest` are ever read, the file is size-capped, and the file name or
path of any backup never leaves this module: the API and notifications carry status, time, sizes and a short
error text only.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop.config import get_settings
from heardloop.models import Workspace, WorkspaceRole
from heardloop.notify import members_with_roles, notify

log = logging.getLogger("heardloop.backup")

Clock = Callable[[], datetime]

MAX_MANIFEST_BYTES = 64 * 1024
STALE_AFTER = timedelta(hours=36)
ALERT_INTERVAL = timedelta(hours=24)
MAX_ERROR_LENGTH = 200

_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


class BackupFile(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(max_length=200)
    size: int = Field(alias="bytes", ge=0)
    sha256: str = Field(max_length=128)


class BackupManifest(BaseModel):
    """`latest.json`. Unknown fields are ignored so a newer backup service never breaks an older app."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    set_name: str = Field(alias="set", max_length=64)
    started_at: datetime | None = None
    finished_at: datetime
    status: Literal["ok", "failed"]
    error: str | None = None
    files: list[BackupFile] = Field(default_factory=list, max_length=20)
    db_bytes: int = Field(default=0, ge=0)
    media_bytes: int = Field(default=0, ge=0)
    encrypted: bool = False
    pg_dump_version: str | None = Field(default=None, max_length=32)

    @field_validator("started_at", "finished_at")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)

    @field_validator("error")
    @classmethod
    def _short_error(cls, v: str | None) -> str | None:
        if v is None:
            return None
        text = _CONTROLS.sub(" ", v).strip()[:MAX_ERROR_LENGTH]
        return text or None


ManifestState = Literal["disabled", "missing", "unreadable", "ok"]


@dataclass(frozen=True)
class ManifestRead:
    state: ManifestState
    manifest: BackupManifest | None = None


def read_manifest(path: str) -> ManifestRead:
    """Never raises. `disabled` = no path configured; `missing` = no file; `unreadable` = too big, not JSON,
    not matching the schema, or not readable by this process."""
    if not path:
        return ManifestRead("disabled")
    file = Path(path)
    try:
        if not file.exists():
            return ManifestRead("missing")
        if not file.is_file() or file.stat().st_size > MAX_MANIFEST_BYTES:
            return ManifestRead("unreadable")
        with file.open("rb") as fh:
            raw = fh.read(MAX_MANIFEST_BYTES + 1)
    except OSError:
        return ManifestRead("unreadable")
    if len(raw) > MAX_MANIFEST_BYTES:
        return ManifestRead("unreadable")
    try:
        return ManifestRead("ok", BackupManifest.model_validate_json(raw))
    except (ValidationError, ValueError):
        return ManifestRead("unreadable")


Health = Literal["ok", "stale", "failed", "missing", "unreadable", "disabled"]


def age_hours(manifest: BackupManifest, now: datetime) -> float:
    return max(0.0, round((now - manifest.finished_at).total_seconds() / 3600, 1))


def health(read: ManifestRead, now: datetime) -> Health:
    if read.state != "ok" or read.manifest is None:
        return read.state
    if read.manifest.status == "failed":
        return "failed"
    return "stale" if now - read.manifest.finished_at >= STALE_AFTER else "ok"


def alert_for(read: ManifestRead, now: datetime, started_at: datetime) -> tuple[str, dict[str, Any]] | None:
    """The notification (kind, data) owners should get now, or None when all is well.

    A missing manifest only counts after the worker has been up for 36 hours: the first backup runs
    at night, so a fresh install is not a problem yet. Data holds status fields only.
    """
    state = health(read, now)
    manifest = read.manifest
    if state == "failed" and manifest is not None:
        return "backup.failed", {
            "reason": "failed",
            "finished_at": manifest.finished_at.isoformat(),
            "error": manifest.error,
        }
    if state == "unreadable":
        return "backup.failed", {"reason": "unreadable", "finished_at": None, "error": "unreadable_manifest"}
    if state == "stale" and manifest is not None:
        return "backup.stale", {
            "reason": "old",
            "finished_at": manifest.finished_at.isoformat(),
            "age_hours": age_hours(manifest, now),
        }
    if state == "missing" and now - started_at >= STALE_AFTER:
        return "backup.stale", {"reason": "missing", "finished_at": None, "age_hours": None}
    return None


async def check_backup_alerts(
    factory: async_sessionmaker[AsyncSession], clock: Clock, started_at: datetime
) -> int:
    """Hourly: notify the owners of every workspace about a stale or failed backup, at most once per 24 hours
    per workspace (`workspaces.last_backup_alert_at`). Returns the number of workspaces alerted."""
    now = clock()
    read = await asyncio.to_thread(read_manifest, get_settings().backup_manifest_path)
    alert = alert_for(read, now, started_at)
    if alert is None:
        return 0
    kind, data = alert
    alerted = 0
    async with factory() as db:
        workspace_ids = list((await db.execute(select(Workspace.id))).scalars())
        for workspace_id in workspace_ids:
            # Row lock: two workers never alert the same workspace twice.
            ws = (
                await db.execute(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
            ).scalar_one_or_none()
            if ws is not None and (
                ws.last_backup_alert_at is None or now - ws.last_backup_alert_at >= ALERT_INTERVAL
            ):
                owners = await members_with_roles(db, workspace_id, [WorkspaceRole.OWNER])
                if owners:
                    await notify(
                        db, workspace_id=workspace_id, kind=kind, recipients=owners, now=now, data=data
                    )
                    ws.last_backup_alert_at = now
                    alerted += 1
            await db.commit()
    if alerted:
        log.warning("Backup alert sent", extra={"kind": kind, "workspaces": alerted})
    return alerted
