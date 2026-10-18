"""LinkedIn REST client: OAuth 2.0 (3-legged), OpenID userinfo, Posts API.

Docs: https://learn.microsoft.com/linkedin/ - all calls use the pinned `LinkedIn-Version` header.
Never log access tokens, client secrets or full response bodies.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

from heardloop.config import Settings
from heardloop.publishing.base import LinkAttachment, PublishResult

log = logging.getLogger("heardloop.linkedin")

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"  # noqa: S105  # URL, not a secret
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
IMAGES_URL = "https://api.linkedin.com/rest/images"
IMAGE_URN = re.compile(r"^urn:li:image:[A-Za-z0-9_-]{1,100}$")
VIDEOS_URL = "https://api.linkedin.com/rest/videos"
VIDEO_URN = re.compile(r"^urn:li:video:[A-Za-z0-9_-]{1,100}$")
VIDEO_PART_TIMEOUT = httpx.Timeout(120.0, connect=5.0)  # one 4 MB part
REVOKE_URL = "https://www.linkedin.com/oauth/v2/revoke"
SCOPES = ("openid", "profile", "w_member_social")  # no "email": the address is never stored or used
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
DEFAULT_TOKEN_SECONDS = 60 * 24 * 3600  # LinkedIn's documented default when expires_in is missing
POST_URN = re.compile(r"^urn:li:(share|ugcPost):\d{1,30}$")
# "Little text" reserved characters in `commentary` (escape with backslash). '#' is left as-is so
# hashtags keep working - verify against LinkedIn's current docs when adding mentions/hashtag support.
_RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")


def _read_part(path: Path, offset: int, length: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read(length)


def escape_commentary(text: str) -> str:
    return _RESERVED.sub(r"\\\1", text)


class LinkedInError(Exception):
    """A LinkedIn call failed. `code` is safe to log and show; it never contains tokens."""

    def __init__(self, code: str, status: int | None = None, *, transient: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.transient = transient


@dataclass(frozen=True, slots=True)
class TokenResponse:
    access_token: str
    expires_in: int
    scopes: tuple[str, ...]

    def __repr__(self) -> str:
        return f"TokenResponse(<redacted>, expires_in={self.expires_in}, scopes={self.scopes})"


@dataclass(frozen=True, slots=True)
class Member:
    sub: str  # LinkedIn member id -> urn:li:person:{sub}
    name: str

    @property
    def urn(self) -> str:
        return f"urn:li:person:{self.sub}"


def _safe_error(resp: httpx.Response) -> str:
    """Short, token-free description of an error response."""
    try:
        data: Any = resp.json()
    except ValueError:
        return f"http_{resp.status_code}"
    if isinstance(data, dict):
        code = data.get("serviceErrorCode") or data.get("error") or data.get("code")
        if code:
            return f"http_{resp.status_code}:{str(code)[:60]}"
    return f"http_{resp.status_code}"


class LinkedInClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._s = settings
        self._http = http

    def authorization_url(self, state: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._s.linkedin_client_id,
                "redirect_uri": self._s.linkedin_redirect_uri,
                "state": state,
                "scope": " ".join(SCOPES),
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    async def exchange_code(self, code: str) -> TokenResponse:
        try:
            return await self._exchange_code(code)
        except LinkedInError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise LinkedInError(f"token_exchange_failed:{type(exc).__name__}") from None

    async def _exchange_code(self, code: str) -> TokenResponse:
        resp = await self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._s.linkedin_redirect_uri,
                "client_id": self._s.linkedin_client_id,
                "client_secret": self._s.linkedin_client_secret.get_secret_value(),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise LinkedInError(f"token_exchange_failed:{_safe_error(resp)}", resp.status_code)
        data = resp.json()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise LinkedInError("token_exchange_failed:no_token", resp.status_code)
        scopes = tuple(s for s in str(data.get("scope", "")).replace(",", " ").split() if s)
        expires_in = int(data.get("expires_in") or DEFAULT_TOKEN_SECONDS)
        return TokenResponse(access_token=token, expires_in=max(expires_in, 60), scopes=scopes)

    async def revoke_token(self, access_token: str) -> bool:
        """Best-effort: asks LinkedIn to invalidate the token. Failures are logged at INFO and ignored."""
        try:
            resp = await self._http.post(
                REVOKE_URL,
                data={
                    "client_id": self._s.linkedin_client_id,
                    "client_secret": self._s.linkedin_client_secret.get_secret_value(),
                    "token": access_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            log.info("LinkedIn token revoke failed", extra={"error": type(exc).__name__})
            return False
        if resp.status_code != 200:
            log.info("LinkedIn token revoke refused", extra={"status": resp.status_code})
            return False
        return True

    async def userinfo(self, access_token: str) -> Member:
        try:
            return await self._userinfo(access_token)
        except LinkedInError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise LinkedInError(f"userinfo_failed:{type(exc).__name__}") from None

    async def _userinfo(self, access_token: str) -> Member:
        resp = await self._http.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT
        )
        if resp.status_code != 200:
            raise LinkedInError(f"userinfo_failed:{_safe_error(resp)}", resp.status_code)
        data = resp.json()
        sub = data.get("sub")
        if not isinstance(sub, str) or not sub:
            raise LinkedInError("userinfo_failed:no_sub", resp.status_code)
        name = str(
            data.get("name") or " ".join(filter(None, [data.get("given_name"), data.get("family_name")]))
        )
        return Member(sub=sub, name=name[:200] or "LinkedIn member")

    def _rest_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": self._s.linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    async def upload_image(self, access_token: str, owner_urn: str, data: bytes, mime: str) -> str:
        """Images API: initializeUpload -> PUT bytes -> wait until AVAILABLE. Returns urn:li:image:...

        Nothing is public yet at this point, so every failure here is safe to retry (transient) unless
        LinkedIn says the request itself is wrong (4xx).
        """

        def fail(code: str, resp: httpx.Response | None = None) -> LinkedInError:
            status = resp.status_code if resp is not None else None
            transient = status is None or status == 429 or status >= 500
            return LinkedInError(
                f"image_{code}" + (f":{_safe_error(resp)}" if resp is not None else ""),
                status,
                transient=transient,
            )

        try:
            init = await self._http.post(
                f"{IMAGES_URL}?action=initializeUpload",
                json={"initializeUploadRequest": {"owner": owner_urn}},
                headers={**self._rest_headers(access_token), "Content-Type": "application/json"},
                timeout=TIMEOUT,
            )
            if init.status_code != 200:
                raise fail("init_failed", init)
            value = init.json().get("value", {})
            upload_url, image_urn = value.get("uploadUrl"), value.get("image")
            if (
                not isinstance(upload_url, str)
                or not isinstance(image_urn, str)
                or not IMAGE_URN.match(image_urn)
            ):
                raise LinkedInError("image_init_bad_response", init.status_code)
            host = urlparse(upload_url).hostname or ""
            if urlparse(upload_url).scheme != "https" or not (
                host == "linkedin.com" or host.endswith(".linkedin.com")
            ):
                raise LinkedInError("image_upload_url_rejected")  # never send the token elsewhere

            put = await self._http.put(
                upload_url,
                content=data,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": mime},
                timeout=httpx.Timeout(60.0, connect=5.0),
            )
            if put.status_code not in (200, 201):
                raise fail("upload_failed", put)

            for _ in range(10):  # LinkedIn processes the image asynchronously
                check = await self._http.get(
                    f"{IMAGES_URL}/{quote(image_urn, safe='')}",
                    headers=self._rest_headers(access_token),
                    timeout=TIMEOUT,
                )
                if check.status_code == 200:
                    state = check.json().get("status")
                    if state == "AVAILABLE":
                        return image_urn
                    if state == "PROCESSING_FAILED":
                        raise LinkedInError("image_processing_failed", 200)
                elif check.status_code != 429 and check.status_code < 500:
                    raise fail("status_failed", check)
                await asyncio.sleep(1.0)
            raise LinkedInError("image_not_ready", transient=True)
        except LinkedInError:
            raise
        except httpx.HTTPError as exc:
            raise LinkedInError(f"image_network_error:{type(exc).__name__}", transient=True) from None
        except (ValueError, TypeError, AttributeError) as exc:
            raise LinkedInError(f"image_bad_response:{type(exc).__name__}") from None

    def _check_upload_host(self, url: str, code: str) -> None:
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not (host == "linkedin.com" or host.endswith(".linkedin.com")):
            raise LinkedInError(code)  # never send the token elsewhere

    async def video_status(self, access_token: str, video_urn: str) -> str:
        """AVAILABLE | PROCESSING | PROCESSING_FAILED | WAITING_UPLOAD (raises on request errors)."""
        check = await self._http.get(
            f"{VIDEOS_URL}/{quote(video_urn, safe='')}",
            headers=self._rest_headers(access_token),
            timeout=TIMEOUT,
        )
        if check.status_code != 200:
            transient = check.status_code == 429 or check.status_code >= 500
            raise LinkedInError(
                f"video_status_failed:{_safe_error(check)}", check.status_code, transient=transient
            )
        state = check.json().get("status")
        return str(state) if isinstance(state, str) else "PROCESSING"

    async def upload_video(self, access_token: str, owner_urn: str, path: Path, size: int) -> str:
        """Videos API: initializeUpload -> PUT each 4 MB part (ETag) -> finalizeUpload -> urn:li:video:...

        Processing happens afterwards; call `video_status` until AVAILABLE before posting.
        The file is read part by part from disk, never whole into memory.
        """

        def fail(code: str, resp: httpx.Response | None = None) -> LinkedInError:
            status = resp.status_code if resp is not None else None
            transient = status is None or status == 429 or status >= 500
            return LinkedInError(
                f"video_{code}" + (f":{_safe_error(resp)}" if resp is not None else ""),
                status,
                transient=transient,
            )

        try:
            init = await self._http.post(
                f"{VIDEOS_URL}?action=initializeUpload",
                json={
                    "initializeUploadRequest": {
                        "owner": owner_urn,
                        "fileSizeBytes": size,
                        "uploadCaptions": False,
                        "uploadThumbnail": False,
                    }
                },
                headers={**self._rest_headers(access_token), "Content-Type": "application/json"},
                timeout=TIMEOUT,
            )
            if init.status_code != 200:
                raise fail("init_failed", init)
            value = init.json().get("value", {})
            video_urn = value.get("video")
            parts = value.get("uploadInstructions")
            token = value.get("uploadToken", "")
            if (
                not isinstance(video_urn, str)
                or not VIDEO_URN.match(video_urn)
                or not isinstance(parts, list)
            ):
                raise LinkedInError("video_init_bad_response", init.status_code)
            etags: list[str] = []
            for part in parts:
                url, first, last = part["uploadUrl"], int(part["firstByte"]), int(part["lastByte"])
                self._check_upload_host(url, "video_upload_url_rejected")
                chunk = await asyncio.to_thread(_read_part, path, first, last - first + 1)
                put = await self._http.put(
                    url,
                    content=chunk,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/octet-stream",
                    },
                    timeout=VIDEO_PART_TIMEOUT,
                )
                if put.status_code not in (200, 201):
                    raise fail("upload_failed", put)
                etag = put.headers.get("etag")
                if not etag:
                    raise LinkedInError("video_upload_no_etag", put.status_code)
                etags.append(etag)
            fin = await self._http.post(
                f"{VIDEOS_URL}?action=finalizeUpload",
                json={
                    "finalizeUploadRequest": {
                        "video": video_urn,
                        "uploadToken": token,
                        "uploadedPartIds": etags,
                    }
                },
                headers={**self._rest_headers(access_token), "Content-Type": "application/json"},
                timeout=TIMEOUT,
            )
            if fin.status_code not in (200, 204):
                raise fail("finalize_failed", fin)
            return video_urn
        except LinkedInError:
            raise
        except httpx.HTTPError as exc:
            raise LinkedInError(f"video_network_error:{type(exc).__name__}", transient=True) from None
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
            raise LinkedInError(f"video_bad_response:{type(exc).__name__}") from None

    async def create_text_post(
        self,
        access_token: str,
        author_urn: str,
        text: str,
        image_urn: str | None = None,
        link: LinkAttachment | None = None,
    ) -> PublishResult:
        """POST /rest/posts. Success = 201 with the new post URN in `x-restli-id`.

        Content is either an image, or an article (link card) whose thumbnail is the image when given.
        """
        body: dict[str, Any] = {
            "author": author_urn,
            "commentary": escape_commentary(text),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if link is not None:
            article: dict[str, Any] = {"source": link.url, "title": link.title}
            if link.description:
                article["description"] = link.description
            if image_urn and IMAGE_URN.match(image_urn):
                article["thumbnail"] = image_urn
            body["content"] = {"article": article}
        elif image_urn:  # an image or a video URN
            body["content"] = {"media": {"id": image_urn}}
        headers = {
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": self._s.linkedin_api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        try:
            resp = await self._http.post(POSTS_URL, json=body, headers=headers, timeout=TIMEOUT)
        except httpx.ConnectError as exc:
            # Nothing reached LinkedIn: safe to retry.
            return PublishResult.transient_error("network_error", type(exc).__name__)
        except httpx.TimeoutException:
            # The post may or may not exist now. Treat as unknown -> no automatic retry.
            return PublishResult.permanent_error("timeout_unknown_outcome", "LinkedIn did not answer in time")
        except httpx.HTTPError as exc:
            # Connection broke after sending: LinkedIn may have created the post.
            return PublishResult.permanent_error("unknown_outcome", type(exc).__name__)

        if resp.status_code == 201:
            urn = (resp.headers.get("x-restli-id") or "").strip()
            if POST_URN.match(urn):
                return PublishResult.published(urn, 201)
            return PublishResult.permanent_error(
                "no_post_id", "LinkedIn returned 201 without a valid post id", 201
            )
        code = _safe_error(resp)
        if resp.status_code in (429, 503):
            # Rate limited / unavailable: the request was not processed. Safe to retry.
            return PublishResult.transient_error(code, "LinkedIn is busy, will retry", resp.status_code)
        if resp.status_code >= 500:
            # 500/502/504 may hide a created post. Do not retry automatically.
            return PublishResult.permanent_error(
                f"unknown_outcome:{code}", "Check LinkedIn before retrying", resp.status_code
            )
        if resp.status_code == 401:
            return PublishResult.permanent_error("token_invalid", "Reconnect the LinkedIn account", 401)
        if resp.status_code == 403:
            return PublishResult.permanent_error("not_permitted", "Missing w_member_social permission", 403)
        if resp.status_code == 426:
            return PublishResult.permanent_error("api_version_retired", "Update LINKEDIN_API_VERSION", 426)
        return PublishResult.permanent_error(code, "LinkedIn rejected the post", resp.status_code)
