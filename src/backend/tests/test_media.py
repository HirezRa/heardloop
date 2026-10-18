import uuid
from pathlib import Path

import httpx
import pytest

from heardloop import media
from heardloop.linkedin.client import LinkedInError
from heardloop.linkedin.publisher import LinkedInPublisher
from heardloop.publishing.base import ImageAttachment, PublishRequest, PublishResult
from tests.test_linkedin_client import _client

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (PNG, "image/png"),
        (JPG, "image/jpeg"),
        (b"GIF89a....", "image/gif"),
        (b"<svg></svg>", None),
        (b"", None),
    ],
)
def test_detect_image(data: bytes, mime: str | None) -> None:
    kind = media.detect_image(data)
    assert (kind.mime if kind else None) == mime


def test_save_load_and_path_safety(tmp_path: Path) -> None:
    ws = uuid.uuid4()
    kind = media.detect_image(PNG)
    assert kind is not None
    key = media.save(tmp_path, ws, PNG, kind)
    assert key.startswith(f"{ws}/") and key.endswith(".png")
    assert media.load(tmp_path, key) == PNG
    for bad in ("../etc/passwd", f"{ws}/../../x.png", "x/y.png", f"{ws}/{'a' * 32}.svg"):
        with pytest.raises(ValueError):
            media.load(tmp_path, bad)


def _image_flow(upload_host: str = "www.linkedin.com", init_status: int = 200, final: str = "AVAILABLE"):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.host}{req.url.path}")
        if "initializeUpload" in str(req.url):
            return httpx.Response(
                init_status,
                json={
                    "value": {
                        "uploadUrl": f"https://{upload_host}/dms-uploads/abc",
                        "image": "urn:li:image:C4D10",
                    }
                },
            )
        if req.method == "PUT":
            assert req.headers["Authorization"] == "Bearer tok"
            return httpx.Response(201)
        if "/rest/images/" in req.url.path:
            return httpx.Response(200, json={"status": final})
        if req.url.path.endswith("/rest/posts"):
            import json

            body = json.loads(req.content)
            assert body["content"]["media"]["id"] == "urn:li:image:C4D10"
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:7"})
        return httpx.Response(404)

    return handler, calls


async def test_upload_image_happy_path() -> None:
    handler, calls = _image_flow()
    urn = await _client(handler).upload_image("tok", "urn:li:person:a", PNG, "image/png")
    assert urn == "urn:li:image:C4D10"
    assert [c.split(" ")[0] for c in calls] == ["POST", "PUT", "GET"]


async def test_upload_url_on_foreign_host_is_refused() -> None:
    handler, calls = _image_flow(upload_host="evil.example.com")
    with pytest.raises(LinkedInError) as info:
        await _client(handler).upload_image("tok", "urn:li:person:a", PNG, "image/png")
    assert info.value.code == "image_upload_url_rejected"
    assert not any(c.startswith("PUT") for c in calls)  # the token never went to the foreign host


@pytest.mark.parametrize(("status", "transient"), [(500, True), (429, True), (400, False)])
async def test_upload_image_error_classes(status: int, transient: bool) -> None:
    handler, _ = _image_flow(init_status=status)
    with pytest.raises(LinkedInError) as info:
        await _client(handler).upload_image("tok", "urn:li:person:a", PNG, "image/png")
    assert info.value.transient is transient


def _req(cached: str | None = None) -> PublishRequest:
    return PublishRequest(
        post_id=uuid.uuid4(),
        idempotency_key="k",
        author_urn="urn:li:person:a",
        access_token="tok",  # noqa: S106  # fake
        text="Hi",
        image=ImageAttachment(media_id=uuid.uuid4(), mime="image/png", data=PNG, cached_urn=cached),
    )


async def test_publisher_uploads_then_posts_and_reports_urn() -> None:
    handler, calls = _image_flow()
    result = await LinkedInPublisher(_client(handler)).publish(_req())
    assert result.success and result.uploaded_image_urn == "urn:li:image:C4D10"
    assert any("initializeUpload" in c or c.startswith("PUT") for c in calls)


async def test_publisher_reuses_cached_image() -> None:
    handler, calls = _image_flow()
    result = await LinkedInPublisher(_client(handler)).publish(_req(cached="urn:li:image:C4D10"))
    assert result.success and result.uploaded_image_urn is None
    assert not any(c.startswith("PUT") for c in calls)


async def test_publisher_image_failure_before_post_is_retryable() -> None:
    handler, calls = _image_flow(init_status=503)
    result: PublishResult = await LinkedInPublisher(_client(handler)).publish(_req())
    assert not result.success and result.transient
    assert not any("/rest/posts" in c for c in calls)


async def test_publisher_processing_failed_is_permanent() -> None:
    handler, _ = _image_flow(final="PROCESSING_FAILED")
    result = await LinkedInPublisher(_client(handler)).publish(_req())
    assert not result.success and not result.transient and result.error_code == "image_processing_failed"


async def test_status_poll_fails_fast_on_client_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "initializeUpload" in str(req.url):
            return httpx.Response(
                200, json={"value": {"uploadUrl": "https://www.linkedin.com/u/1", "image": "urn:li:image:A1"}}
            )
        if req.method == "PUT":
            return httpx.Response(201)
        return httpx.Response(403, json={})

    with pytest.raises(LinkedInError) as info:
        await _client(handler).upload_image("tok", "urn:li:person:a", PNG, "image/png")
    assert info.value.code.startswith("image_status_failed") and not info.value.transient
