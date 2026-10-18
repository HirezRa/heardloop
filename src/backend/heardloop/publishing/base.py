"""Publishing contract. The real LinkedIn Posts API client arrives in phase 2."""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol

MAX_ATTEMPTS = 3
_DELAYS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))


def delay_after(attempt_no: int) -> timedelta:
    """Wait before the next attempt after attempt `attempt_no` (1-based) failed."""
    if attempt_no < 1:
        raise ValueError("attempt_no starts at 1")
    return _DELAYS[min(attempt_no, len(_DELAYS)) - 1]


@dataclass(frozen=True, slots=True)
class PublishResult:
    success: bool
    transient: bool
    post_urn: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    uploaded_image_urn: str | None = None  # set when an image was uploaded, so it can be cached

    @classmethod
    def published(cls, post_urn: str, http_status: int = 201) -> "PublishResult":
        return cls(success=True, transient=False, post_urn=post_urn, http_status=http_status)

    @classmethod
    def transient_error(cls, code: str, message: str, http_status: int | None = None) -> "PublishResult":
        return cls(False, True, None, http_status, code, message)

    @classmethod
    def permanent_error(cls, code: str, message: str, http_status: int | None = None) -> "PublishResult":
        return cls(False, False, None, http_status, code, message)


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    media_id: uuid.UUID
    mime: str
    data: bytes
    cached_urn: str | None  # LinkedIn image URN from an earlier upload by the same owner

    def __repr__(self) -> str:
        return f"ImageAttachment(media_id={self.media_id}, mime={self.mime}, bytes={len(self.data)})"


@dataclass(frozen=True, slots=True)
class VideoAttachment:
    media_id: uuid.UUID
    path: "Path"  # read part by part; videos are never held in memory
    size: int
    cached_urn: str | None  # LinkedIn video URN from an earlier upload by the same owner

    def __repr__(self) -> str:
        return f"VideoAttachment(media_id={self.media_id}, bytes={self.size})"


@dataclass(frozen=True, slots=True)
class LinkAttachment:
    url: str
    title: str
    description: str | None


@dataclass(frozen=True, slots=True)
class PublishRequest:
    post_id: uuid.UUID
    idempotency_key: str
    author_urn: str
    access_token: str  # decrypted just for this call; never logged
    text: str
    image: "ImageAttachment | None" = None
    video: "VideoAttachment | None" = None  # image and video are mutually exclusive
    link: "LinkAttachment | None" = None  # with an image, the image is the card thumbnail

    def __repr__(self) -> str:
        return f"PublishRequest(post_id={self.post_id}, author_urn={self.author_urn}, <redacted>)"


class Publisher(Protocol):
    async def publish(self, request: PublishRequest) -> PublishResult: ...


class NotConfiguredPublisher:
    """Used when LINKEDIN_CLIENT_ID/SECRET are not set: fails clearly instead of pretending to publish."""

    async def publish(self, request: PublishRequest) -> PublishResult:
        return PublishResult.permanent_error(
            "linkedin_not_configured", "Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET in .env"
        )
