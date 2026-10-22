"""Fetches a page's Open Graph data so the editor can pre-fill a link card.

LinkedIn's Posts API does not scrape URLs itself: an article post must carry its own title, description and
thumbnail. We fetch them once, the user edits them, and the publisher sends them as `content.article`.

Safety: this is a server-side fetch of a user-supplied URL, so it reuses the webhook destination guard
(nothing that is not globally routable, no own services), re-checks every redirect hop and the connected
peer address, reads at most
`MAX_BYTES`, accepts only HTML and never follows more than `MAX_REDIRECTS` hops.
"""

import asyncio
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from heardloop.delivery import BlockedDestinationError, check_destination, peer_error

MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 8.0
DEADLINE = 12.0
USER_AGENT = "Mozilla/5.0 (compatible; Heardloop link preview)"
TITLE_MAX = 200  # LinkedIn article title limit is generous; keep cards tidy
DESCRIPTION_MAX = 300


@dataclass(slots=True)
class LinkPreview:
    url: str  # final URL after redirects
    title: str | None
    description: str | None
    image_url: str | None
    site_name: str | None


class LinkPreviewError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _HeadDone(Exception):
    """Raised by the parser once <body> starts: nothing else is needed."""


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if tag == "body":
            self._done = True  # everything we need lives in <head>
            raise _HeadDone
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            key = (a.get("property") or a.get("name") or "").lower()
            if key and key not in self.meta and a.get("content"):
                self.meta[key] = a["content"].strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and not self._done:
            self.title += data


def _clean(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    return text[:limit] if text else None


def parse_html(html: str, base_url: str) -> LinkPreview:
    p = _MetaParser()
    try:
        p.feed(html)
    except _HeadDone:
        pass
    except Exception as exc:  # malformed markup must not take the API down
        raise LinkPreviewError("parse_error") from exc
    m = p.meta
    image = m.get("og:image") or m.get("og:image:url") or m.get("twitter:image")
    if image:
        image = urljoin(base_url, image)
        if urlparse(image).scheme not in ("http", "https"):
            image = None
    return LinkPreview(
        url=base_url,
        title=_clean(m.get("og:title") or m.get("twitter:title") or p.title, TITLE_MAX),
        description=_clean(
            m.get("og:description") or m.get("twitter:description") or m.get("description"), DESCRIPTION_MAX
        ),
        image_url=image,
        site_name=_clean(m.get("og:site_name"), 100),
    )


def validate_url(url: str) -> str:
    url = url.strip()
    try:
        u = urlparse(url)
        host = u.hostname
    except ValueError as exc:  # e.g. "http://[::1" (unbalanced brackets)
        raise LinkPreviewError("invalid_url") from exc
    if u.scheme not in ("http", "https") or not host or u.username or u.password:
        raise LinkPreviewError("invalid_url")
    return url


_CHARSET = re.compile(rb"""charset=["']?([A-Za-z0-9_.:-]+)""", re.IGNORECASE)


def decode_html(body: bytes, content_type: str) -> str:
    """Honours the charset from the header, then <meta charset>, then UTF-8 (lossy)."""
    candidates: list[str] = []
    m = _CHARSET.search(content_type.encode("ascii", "ignore"))
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    m = _CHARSET.search(body[:4096])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    for enc in candidates:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


async def _fetch_once(http: httpx.AsyncClient, url: str) -> tuple[str | None, bytes | None, str | None]:
    """Returns (redirect_location, body, content_type)."""
    async with http.stream(
        "GET",
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=TIMEOUT,
        follow_redirects=False,
    ) as res:
        blocked = peer_error(res, allow_private=False)  # peer re-check after connect (DNS rebinding)
        if blocked:
            raise LinkPreviewError(blocked)
        if res.status_code in (301, 302, 303, 307, 308):
            location = res.headers.get("location")
            if not location:
                raise LinkPreviewError("bad_redirect")
            return location, None, None
        if res.status_code != 200:
            raise LinkPreviewError(f"http_{res.status_code}")
        ctype = res.headers.get("content-type", "")
        if "html" not in ctype:
            raise LinkPreviewError("not_html")
        chunks: list[bytes] = []
        size = 0
        async for chunk in res.aiter_bytes():
            size += len(chunk)
            chunks.append(chunk)
            if size >= MAX_BYTES:
                break  # <head> is almost always within the first few KB
        return None, b"".join(chunks), ctype


async def fetch_preview(http: httpx.AsyncClient, url: str) -> LinkPreview:
    url = validate_url(url)
    try:
        return await asyncio.wait_for(_follow(http, url), DEADLINE)
    except TimeoutError as exc:
        raise LinkPreviewError("timeout") from exc
    except BlockedDestinationError as exc:  # pinned-IP check at connect time (DNS rebinding)
        raise LinkPreviewError("blocked_destination") from exc
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise LinkPreviewError(type(exc).__name__.lower()) from exc


async def _follow(http: httpx.AsyncClient, url: str) -> LinkPreview:
    for _ in range(MAX_REDIRECTS + 1):
        blocked = await check_destination(url, allow_private=False)
        if blocked:
            raise LinkPreviewError(blocked)
        location, body, ctype = await _fetch_once(http, url)
        if location is None:
            assert body is not None
            return parse_html(decode_html(body, ctype or ""), url)
        url = validate_url(urljoin(url, location))
    raise LinkPreviewError("too_many_redirects")
