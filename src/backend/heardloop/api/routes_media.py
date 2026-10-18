"""Media upload and download for posts (FR-09): images and MP4. Raw request body (no multipart parser)."""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from heardloop import audit, media
from heardloop.api.deps import Db, WorkspaceAccess, client_ip, require_client_header, require_permission
from heardloop.auth.permissions import Permission
from heardloop.config import get_settings
from heardloop.models import MediaAsset, Workspace
from heardloop.timeutil import utcnow

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/media",
    tags=["media"],
    dependencies=[Depends(require_client_header)],
)

ReadAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.WORKSPACE_READ))]
WriteAccess = Annotated[WorkspaceAccess, Depends(require_permission(Permission.POSTS_WRITE))]


WORKSPACE_QUOTA_BYTES = 500 * 1024 * 1024  # keeps a runaway uploader from filling the media volume


class MediaOut(BaseModel):
    id: uuid.UUID
    mime_type: str
    size_bytes: int
    url: str


def media_url(workspace_id: uuid.UUID, media_id: uuid.UUID) -> str:
    return f"/api/v1/workspaces/{workspace_id}/media/{media_id}"


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(access: WriteAccess, request: Request, db: Db) -> MediaOut:
    """Raw body upload of one image (PNG/JPEG/GIF, 5 MB) or one MP4 video (200 MB), streamed to disk."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > media.MAX_VIDEO_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large")
    used = (
        await db.execute(
            select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(
                MediaAsset.workspace_id == access.workspace_id
            )
        )
    ).scalar_one()
    actor_id = access.principal.user.id  # read before the rollback expires loaded instances
    await db.rollback()  # release the connection: the upload below can take minutes
    root = get_settings().media_root
    writer = media.StreamWriter(root, access.workspace_id)
    kind: media.ImageType | None = None
    head = b""
    try:
        async for chunk in request.stream():
            if kind is None:
                head += chunk
                if len(head) >= media.HEAD_BYTES:
                    kind = media.detect_media(head)
                    if kind is None:
                        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type")
            await asyncio.to_thread(writer.write, chunk)  # disk IO off the event loop
            limit = kind.max_bytes if kind else media.MAX_VIDEO_BYTES
            if writer.size > limit:
                code = "video_too_large" if kind and kind.is_video else "image_too_large"
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, code)
            if used + writer.size > WORKSPACE_QUOTA_BYTES:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "media_quota_exceeded")
        if kind is None:
            kind = media.detect_media(head)
        if kind is None:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type")
        if kind.is_video and writer.size < media.MIN_VIDEO_BYTES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "video_too_small")
        key, digest = writer.finish(kind)
    except BaseException:
        writer.abort()
        raise
    asset = MediaAsset(
        workspace_id=access.workspace_id,
        storage_key=key,
        mime_type=kind.mime,
        size_bytes=writer.size,
        sha256=digest,
        created_at=utcnow(),
    )
    # Quota again, now authoritative: other uploads may have finished while this one streamed. The workspace
    # row lock serializes concurrent uploads of one workspace, so the sum cannot be raced past the limit.
    await db.execute(select(Workspace.id).where(Workspace.id == access.workspace_id).with_for_update())
    used_now = (
        await db.execute(
            select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(
                MediaAsset.workspace_id == access.workspace_id
            )
        )
    ).scalar_one()
    if used_now + writer.size > WORKSPACE_QUOTA_BYTES:
        await db.rollback()
        media.remove(root, key)
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "media_quota_exceeded")
    db.add(asset)
    await db.flush()
    audit.record(
        db,
        action="media.uploaded",
        entity="media",
        entity_id=asset.id,
        actor_id=actor_id,
        workspace_id=access.workspace_id,
        ip_address=client_ip(request),
        changes={"mime": kind.mime, "bytes": writer.size},
        now=utcnow(),
    )
    try:
        await db.commit()
    except Exception:
        media.remove(root, key)  # no orphan file when the DB write fails
        raise
    return MediaOut(
        id=asset.id,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        url=media_url(access.workspace_id, asset.id),
    )


@router.get("/{media_id}")
async def download(media_id: uuid.UUID, access: ReadAccess, db: Db) -> FileResponse:
    asset = await db.get(MediaAsset, media_id)
    if asset is None or asset.workspace_id != access.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found")
    try:
        path = media.path_of(get_settings().media_root, asset.storage_key)
        if not path.is_file():
            raise OSError
    except (OSError, ValueError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found") from None
    # FileResponse streams from disk and honours Range requests (video seeking in the browser).
    return FileResponse(
        path,
        media_type=asset.mime_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": "inline",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
