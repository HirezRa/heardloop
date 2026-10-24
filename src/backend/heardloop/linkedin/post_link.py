"""Reading a LinkedIn post link without contacting LinkedIn.

The id is taken from the address itself; nothing is fetched (no scraping, no redirect following). A post has
several ids: the address in the browser usually carries the *activity* id, while the analytics export of the
same post carries its *share* or *ugcPost* id. LinkedIn names the export file after the activity id
(`SinglePostAnalytics_<name>_<activity id>.xlsx`), which is the only offline link between the two.
"""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

KINDS = ("activity", "share", "ugcPost")
MAX_LINK = 2048

_HOSTS = frozenset({"linkedin.com", "www.linkedin.com", "m.linkedin.com"})
_SHORT_HOSTS = frozenset({"lnkd.in", "www.lnkd.in"})
# /feed/update/urn:li:activity:123/ (also URL-encoded) and /posts/<slug>-activity-123-AbCd
_URN = re.compile(r"urn:li:(activity|share|ugcPost):(\d{10,25})(?!\d)")
_SLUG = re.compile(r"[-_](activity|share|ugcPost)-(\d{10,25})(?=-|/|$)")
_EXPORT_NAME = re.compile(r"(?:^|_)(\d{15,25})\.xlsx$", re.IGNORECASE)


class PostLinkError(ValueError):
    """`code`: invalid_post_link, short_link or not_linkedin."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PostLink:
    kind: str  # activity | share | ugcPost
    id: str

    @property
    def urn(self) -> str:
        return f"urn:li:{self.kind}:{self.id}"


def parse_post_link(value: str) -> PostLink:
    """The post id in a linkedin.com post address. Short `lnkd.in` links are refused (they need a request)."""
    text = value.strip()
    if not text or len(text) > MAX_LINK or any(ord(ch) < 32 for ch in text):
        raise PostLinkError("invalid_post_link")
    parts = urlsplit(text if "://" in text else f"https://{text}")
    host = (parts.hostname or "").lower()
    if host in _SHORT_HOSTS:
        raise PostLinkError("short_link")
    if parts.scheme not in ("http", "https") or host not in _HOSTS:
        raise PostLinkError("not_linkedin")
    path = unquote(parts.path)
    match = _URN.search(path) or _SLUG.search(path)
    if match is None:
        raise PostLinkError("invalid_post_link")
    return PostLink(kind=match.group(1), id=match.group(2))


def activity_id_from_export_name(name: str) -> str | None:
    """The activity id LinkedIn puts in the export file name, or None when the file was renamed."""
    match = _EXPORT_NAME.search(name.strip())
    return match.group(1) if match else None


def kind_of_url(url: str) -> str:
    """The id kind in an export's Post URL (`share` when the address does not say)."""
    match = _URN.search(unquote(url)) or _SLUG.search(unquote(url))
    return match.group(1) if match else "share"
