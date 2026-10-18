"""Media storage on the media volume (FR-09): images and MP4 videos.

Files get random names; the type is decided by content (magic bytes), not by the file name.
"""

import contextlib
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB keeps uploads quick; LinkedIn accepts larger, we do not need them
MAX_VIDEO_BYTES = 200 * 1024 * 1024  # LinkedIn allows up to 500 MB; 200 MB keeps the media volume sane
MIN_VIDEO_BYTES = 75 * 1024  # LinkedIn's lower bound
_KEY = re.compile(r"^[0-9a-f-]{36}/[0-9a-f]{32}\.(png|jpg|gif|mp4)$")
HEAD_BYTES = 32  # magic numbers plus the first compatible brands of an `ftyp` box
MP4_MAJOR_BRANDS = frozenset({b"isom", b"iso2", b"mp42", b"mp41", b"avc1", b"M4V "})
STILL_IMAGE_BRANDS = frozenset({b"mif1", b"msf1", b"heic", b"heix", b"avif", b"avis"})
ORPHAN_GRACE = timedelta(hours=24)  # unreferenced uploads younger than this may still be attached to a post


@dataclass(frozen=True, slots=True)
class ImageType:
    mime: str
    ext: str

    @property
    def is_video(self) -> bool:
        return self.mime.startswith("video/")

    @property
    def max_bytes(self) -> int:
        return MAX_VIDEO_BYTES if self.is_video else MAX_IMAGE_BYTES


MediaType = ImageType  # same record for images and videos


def detect_media(head: bytes) -> ImageType | None:
    """Image or MP4 from the first bytes. MP4 = ISO base media file with an `ftyp` box at offset 4."""
    image = detect_image(head)
    if image is not None:
        return image
    if is_mp4(head):
        return ImageType("video/mp4", "mp4")
    return None


def is_mp4(head: bytes) -> bool:
    """`ftyp` box with an MP4 major brand (offset 8) and no still-image brand among the compatible ones.

    HEIC/AVIF stills (`mif1`, `heic`, ...) and QuickTime (`qt  `) share the box layout but are not MP4.
    """
    if len(head) < 12 or head[4:8] != b"ftyp":
        return False
    if head[8:12] not in MP4_MAJOR_BRANDS:
        return False
    size = int.from_bytes(head[0:4], "big")
    if size == 1:  # 64-bit box size: compatible brands start later; the major brand decided already
        return True
    if size < 16:
        return False
    compatible = head[16 : min(size, len(head))]
    brands = {compatible[i : i + 4] for i in range(0, len(compatible) - 3, 4)}
    return brands.isdisjoint(STILL_IMAGE_BRANDS)


def detect_image(data: bytes) -> ImageType | None:
    """Magic-byte check. SVG, WebP and anything else are rejected on purpose."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ImageType("image/png", "png")
    if data.startswith(b"\xff\xd8\xff"):
        return ImageType("image/jpeg", "jpg")
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ImageType("image/gif", "gif")
    return None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(root: Path, storage_key: str) -> Path:
    if not _KEY.match(storage_key):
        raise ValueError("invalid storage key")
    path = (root / storage_key).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("storage key escapes media root")
    return path


def save(root: Path, workspace_id: uuid.UUID, data: bytes, kind: ImageType) -> str:
    """Writes atomically and returns the storage key `<workspace>/<random>.<ext>`."""
    key = f"{workspace_id}/{uuid.uuid4().hex}.{kind.ext}"
    path = _path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return key


def load(root: Path, storage_key: str) -> bytes:
    return _path(root, storage_key).read_bytes()


def path_of(root: Path, storage_key: str) -> Path:
    """Resolved path for streaming (videos are served and uploaded from disk, never held in memory)."""
    return _path(root, storage_key)


class StreamWriter:
    """Writes an upload chunk by chunk to a temp file; `finish` renames it into place.

    Usage: w = StreamWriter(root, workspace_id); w.write(chunk)...; key = w.finish(kind) or w.abort().
    """

    def __init__(self, root: Path, workspace_id: uuid.UUID) -> None:
        self._root = root
        self._name = uuid.uuid4().hex
        self._tmp = root / str(workspace_id) / f"{self._name}.tmp"
        self._tmp.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._tmp, "wb")  # noqa: SIM115
        self._hash = hashlib.sha256()
        self.size = 0

    def write(self, chunk: bytes) -> None:
        self._fh.write(chunk)
        self._hash.update(chunk)
        self.size += len(chunk)

    def finish(self, kind: ImageType) -> tuple[str, str]:
        self._fh.close()
        key = f"{self._tmp.parent.name}/{self._name}.{kind.ext}"
        os.replace(self._tmp, _path(self._root, key))
        return key, self._hash.hexdigest()

    def abort(self) -> None:
        with contextlib.suppress(OSError):
            self._fh.close()
            self._tmp.unlink(missing_ok=True)


def remove(root: Path, storage_key: str) -> None:
    with contextlib.suppress(OSError, ValueError):
        _path(root, storage_key).unlink(missing_ok=True)
