"""Hourly media sweeper (worker): uploads nothing refers to, and files the database does not know.

Two cases, both only for items older than `ORPHAN_GRACE` so an upload that is about to be attached to a
post is never touched:

1. A `media_assets` row that no post version lists in `media_ids` -> delete the row, then its file.
2. A file under the media root without a row (failed upload, crashed worker, leftover `*.tmp`) -> delete it.

Logs carry counts only.
"""

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import any_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop import media
from heardloop.models import MediaAsset, PostVersion

log = logging.getLogger("heardloop.media")

Clock = Callable[[], datetime]

BATCH = 200
_DIR = re.compile(r"^[0-9a-f-]{36}$")
_FILE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|mp4|tmp)$")


async def sweep_unreferenced_assets(
    factory: async_sessionmaker[AsyncSession], root: Path, clock: Clock
) -> int:
    """Deletes rows (and files) of assets older than 24 h that no post version references."""
    cutoff = clock() - media.ORPHAN_GRACE
    removed = 0
    while True:
        async with factory() as db:
            referenced = (
                select(PostVersion.id)
                .where(MediaAsset.id == any_(PostVersion.media_ids))
                .correlate(MediaAsset)
                .exists()
            )
            rows = (
                await db.execute(
                    select(MediaAsset.id, MediaAsset.storage_key)
                    .where(MediaAsset.created_at < cutoff, ~referenced)
                    .order_by(MediaAsset.created_at)
                    .limit(BATCH)
                )
            ).all()
            if not rows:
                break
            await db.execute(delete(MediaAsset).where(MediaAsset.id.in_([r[0] for r in rows])))
            await db.commit()
        for (
            _,
            key,
        ) in rows:  # after the commit: a crash leaves a file for the orphan pass, never a dangling row
            await asyncio.to_thread(media.remove, root, key)
        removed += len(rows)
        if len(rows) < BATCH:
            break
    return removed


def _old_files(root: Path, cutoff_ts: float) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    if not root.is_dir():
        return found
    for ws_dir in root.iterdir():
        if not (ws_dir.is_dir() and _DIR.match(ws_dir.name)):
            continue
        for path in ws_dir.iterdir():
            try:
                if _FILE.match(path.name) and path.is_file() and path.stat().st_mtime < cutoff_ts:
                    found.append((f"{ws_dir.name}/{path.name}", path))
            except OSError:
                continue
    return found


async def sweep_orphan_files(factory: async_sessionmaker[AsyncSession], root: Path, clock: Clock) -> int:
    """Deletes files older than 24 h whose storage key is not in `media_assets` (and stale `*.tmp` files)."""
    cutoff_ts = (clock() - media.ORPHAN_GRACE).timestamp()
    candidates = await asyncio.to_thread(_old_files, root, cutoff_ts)
    if not candidates:
        return 0
    known: set[str] = set()
    async with factory() as db:
        for i in range(0, len(candidates), 500):
            keys = [k for k, _ in candidates[i : i + 500]]
            known.update(
                (
                    await db.execute(select(MediaAsset.storage_key).where(MediaAsset.storage_key.in_(keys)))
                ).scalars()
            )
    removed = 0
    for key, path in candidates:
        if key in known:
            continue
        try:
            await asyncio.to_thread(path.unlink, True)
            removed += 1
        except OSError:
            continue
    return removed


async def sweep_media(factory: async_sessionmaker[AsyncSession], root: Path, clock: Clock) -> tuple[int, int]:
    """Both passes; returns (unreferenced assets removed, orphan files removed)."""
    assets = await sweep_unreferenced_assets(factory, root, clock)
    files = await sweep_orphan_files(factory, root, clock)
    if assets or files:
        log.info("Media sweep", extra={"assets_removed": assets, "orphan_files_removed": files})
    return assets, files
