"""Video: MP4 detection, streaming storage, LinkedIn Videos API upload and the publisher's wait-for-ready."""

import json
import uuid
from pathlib import Path

import httpx
import pytest

from heardloop import media
from heardloop.linkedin import publisher as pub
from heardloop.linkedin.publisher import LinkedInPublisher
from heardloop.publishing.base import PublishRequest, VideoAttachment
from tests.test_linkedin_client import _client

MP4_HEAD = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


@pytest.mark.parametrize(
    ("head", "mime"),
    [
        (MP4_HEAD, "video/mp4"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image/png"),
        (b"RIFF....WEBPVP8 ", None),  # WebP and WebM stay out on purpose
        (b"\x1aE\xdf\xa3" + b"\x00" * 12, None),
    ],
)
def test_detect_media(head: bytes, mime: str | None) -> None:
    kind = media.detect_media(head)
    assert (kind.mime if kind else None) == mime


def test_stream_writer_roundtrip_and_abort(tmp_path: Path) -> None:
    ws = uuid.uuid4()
    w = media.StreamWriter(tmp_path, ws)
    w.write(MP4_HEAD)
    w.write(b"x" * 1000)
    key, digest = w.finish(media.ImageType("video/mp4", "mp4"))
    assert key.endswith(".mp4") and len(digest) == 64 and w.size == len(MP4_HEAD) + 1000
    assert media.path_of(tmp_path, key).stat().st_size == w.size
    assert not list((tmp_path / str(ws)).glob("*.tmp"))

    w2 = media.StreamWriter(tmp_path, ws)
    w2.write(b"junk")
    w2.abort()
    assert not list((tmp_path / str(ws)).glob("*.tmp"))


def _video_flow(final_states: list[str], parts: int = 2):  # type: ignore[no-untyped-def]
    """Fake LinkedIn: initialize -> N parts -> finalize -> status polls -> post."""
    calls: list[str] = []
    states = iter(final_states)
    etags_seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.path}?{req.url.query.decode()}")
        if req.url.path.endswith("/rest/videos") and b"initializeUpload" in req.url.query:
            body = json.loads(req.content)["initializeUploadRequest"]
            assert body["owner"] == "urn:li:person:abc" and body["fileSizeBytes"] > 0
            size = body["fileSizeBytes"]
            step = (size + parts - 1) // parts
            instructions = [
                {
                    "uploadUrl": f"https://www.linkedin.com/dms-uploads/V1/part{i}",
                    "firstByte": i * step,
                    "lastByte": min(size, (i + 1) * step) - 1,
                }
                for i in range(parts)
            ]
            return httpx.Response(
                200,
                json={
                    "value": {
                        "video": "urn:li:video:V1",
                        "uploadInstructions": instructions,
                        "uploadToken": "t",
                    }
                },
            )
        if req.method == "PUT" and "/dms-uploads/" in req.url.path:
            assert req.headers["Authorization"] == "Bearer tok"
            etag = f"etag-{req.url.path[-1]}-{len(req.content)}"
            etags_seen.append(etag)
            return httpx.Response(200, headers={"etag": etag})
        if req.url.path.endswith("/rest/videos") and b"finalizeUpload" in req.url.query:
            body = json.loads(req.content)["finalizeUploadRequest"]
            assert body["video"] == "urn:li:video:V1" and body["uploadedPartIds"] == etags_seen
            return httpx.Response(200)
        if "/rest/videos/" in req.url.path:
            return httpx.Response(200, json={"status": next(states, "AVAILABLE")})
        if req.url.path.endswith("/rest/posts"):
            body = json.loads(req.content)
            assert body["content"] == {"media": {"id": "urn:li:video:V1"}}
            return httpx.Response(201, headers={"x-restli-id": "urn:li:ugcPost:9"})
        return httpx.Response(404)

    return handler, calls


def _req(path: Path, cached: str | None = None) -> PublishRequest:
    return PublishRequest(
        post_id=uuid.uuid4(),
        idempotency_key="k",
        author_urn="urn:li:person:abc",
        access_token="tok",  # noqa: S106
        text="A video",
        video=VideoAttachment(media_id=uuid.uuid4(), path=path, size=path.stat().st_size, cached_urn=cached),
    )


@pytest.fixture
def mp4(tmp_path: Path) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(MP4_HEAD + bytes(range(256)) * 300)  # ~77 KB, split into two parts by the fake
    return p


async def test_video_uploaded_in_parts_then_posted(mp4: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pub, "VIDEO_POLL_SECONDS", 0.0)
    handler, calls = _video_flow(["PROCESSING", "AVAILABLE"])
    result = await LinkedInPublisher(_client(handler)).publish(_req(mp4))
    assert result.success and result.post_urn == "urn:li:ugcPost:9"
    assert result.uploaded_image_urn == "urn:li:video:V1"  # cached by the dispatcher under this name
    assert sum(c.startswith("PUT") for c in calls) == 2
    assert sum("finalizeUpload" in c for c in calls) == 1


async def test_video_still_processing_is_retried_with_cached_urn(
    mp4: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pub, "VIDEO_POLL_SECONDS", 0.0)
    monkeypatch.setattr(pub, "VIDEO_READY_POLLS", 3)
    handler, _calls = _video_flow(["PROCESSING"] * 10)
    result = await LinkedInPublisher(_client(handler)).publish(_req(mp4))
    assert not result.success and result.transient and result.error_code == "video_processing"
    assert result.uploaded_image_urn == "urn:li:video:V1"  # keep it: no second upload next time

    handler2, calls2 = _video_flow(["AVAILABLE"])
    again = await LinkedInPublisher(_client(handler2)).publish(_req(mp4, cached="urn:li:video:V1"))
    assert again.success and not any(c.startswith("PUT") for c in calls2)


async def test_video_processing_failed_is_permanent(mp4: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pub, "VIDEO_POLL_SECONDS", 0.0)
    handler, _ = _video_flow(["PROCESSING_FAILED"])
    result = await LinkedInPublisher(_client(handler)).publish(_req(mp4))
    assert not result.success and not result.transient and result.error_code == "video_processing_failed"


async def test_video_upload_url_on_foreign_host_is_refused(mp4: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if b"initializeUpload" in req.url.query:
            return httpx.Response(
                200,
                json={
                    "value": {
                        "video": "urn:li:video:V1",
                        "uploadInstructions": [
                            {"uploadUrl": "https://evil.example.com/x", "firstByte": 0, "lastByte": 10}
                        ],
                        "uploadToken": "",
                    }
                },
            )
        raise AssertionError("the token must never be sent to another host")

    result = await LinkedInPublisher(_client(handler)).publish(_req(mp4))
    assert not result.success and result.error_code == "video_upload_url_rejected"


async def test_failed_processing_does_not_cache_the_urn(mp4: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pub, "VIDEO_POLL_SECONDS", 0.0)
    handler, _ = _video_flow(["PROCESSING_FAILED"])
    result = await LinkedInPublisher(_client(handler)).publish(_req(mp4))
    assert result.uploaded_image_urn is None  # a dead URN must never be reused on retry
