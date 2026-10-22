"""Security hardening without a database: SSRF guard, control characters, MP4 brands, subject."""

import asyncio
import ipaddress
import uuid
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from heardloop import delivery, media
from heardloop.api import schemas
from heardloop.config import get_settings
from heardloop.models import Notification

PUBLIC = "8.8.8.8"  # privacy-allow
LAN = "192.168.1.20"  # privacy-allow
CGNAT = "100.64.1.1"  # privacy-allow
OWN = "172.19.0.9"  # privacy-allow
OTHER_OWN = "172.19.0.3"  # privacy-allow
OTHER_OWN2 = "172.19.0.4"  # privacy-allow
UNSPECIFIED = "0.0.0.0"  # noqa: S104  # privacy-allow
MULTICAST = "224.0.0.1"  # privacy-allow


@pytest.fixture(autouse=True)
def _clean_blocklist() -> None:
    delivery._blocked_ips.clear()


# ---- B8: SSRF guard ----


@pytest.mark.parametrize(
    "url",
    [
        f"http://{CGNAT}/x",  # shared address space (carrier-grade NAT)
        f"http://[::ffff:{CGNAT}]/",  # the same, IPv4-mapped
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
        f"http://{UNSPECIFIED}/",
        "http://[fe80::1]/",
        f"http://{MULTICAST}/",
    ],
)
def test_guard_refuses_non_global_addresses(url: str) -> None:
    assert asyncio.run(delivery.check_destination(url)) == "blocked_destination"
    assert asyncio.run(delivery.check_destination(url, allow_private=False)) == "blocked_destination"


def test_guard_allows_public_and_lan_for_webhooks_only() -> None:
    assert asyncio.run(delivery.check_destination(f"https://{PUBLIC}/hook")) is None
    assert asyncio.run(delivery.check_destination(f"https://{PUBLIC}/hook", allow_private=False)) is None
    assert asyncio.run(delivery.check_destination(f"http://{LAN}:5678/x")) is None
    assert asyncio.run(delivery.check_destination(f"http://{LAN}:5678/x", allow_private=False)) == (
        "blocked_destination"
    )


def test_guard_refuses_own_service_addresses() -> None:
    delivery._blocked_ips.add(ipaddress.ip_address(OWN))
    assert asyncio.run(delivery.check_destination(f"http://{OWN}:8000/")) == "blocked_destination"
    assert asyncio.run(delivery.check_destination(f"http://[::ffff:{OWN}]/")) == "blocked_destination"
    assert asyncio.run(delivery.check_destination(f"http://{LAN}/")) is None  # other LAN hosts stay allowed


def test_refresh_blocked_service_ips_collects_stack_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    table = {"db": OWN, "api": OTHER_OWN, "web": OTHER_OWN2, "heardloop.example.com": PUBLIC}

    class FakeLoop:
        async def getaddrinfo(
            self, host: str, port: object
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            if host not in table:
                raise OSError("unknown host")  # "worker" is not an alias in every container
            return [(2, 1, 6, "", (table[host], 0))]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())
    monkeypatch.setattr(
        delivery, "get_settings", lambda: SimpleNamespace(app_base_url="https://heardloop.example.com")
    )
    asyncio.run(delivery.refresh_blocked_service_ips())
    assert ipaddress.ip_address(OWN) in delivery._blocked_ips
    assert ipaddress.ip_address(PUBLIC) in delivery._blocked_ips  # the public host of APP_BASE_URL
    assert len(delivery._blocked_ips) == 4


class _Stream:
    def __init__(self, peer: object) -> None:
        self._peer = peer

    def get_extra_info(self, name: str) -> object:
        assert name == "server_addr"
        return self._peer


def _response(peer: object | None) -> httpx.Response:
    ext = {} if peer is None else {"network_stream": _Stream(peer)}
    return httpx.Response(200, extensions=ext)


def test_peer_recheck_after_connect() -> None:
    assert delivery.peer_error(_response(None)) is None  # mocked transport: nothing to check
    assert delivery.peer_error(_response((PUBLIC, 443))) is None
    assert delivery.peer_error(_response(("127.0.0.1", 80))) == "blocked_destination"
    assert delivery.peer_error(_response((f"::ffff:{CGNAT}", 80))) == "blocked_destination"
    assert delivery.peer_error(_response((LAN, 80))) is None
    assert delivery.peer_error(_response((LAN, 80)), allow_private=False) == "blocked_destination"
    delivery._blocked_ips.add(ipaddress.ip_address(OWN))
    assert delivery.peer_error(_response((OWN, 8000))) == "blocked_destination"
    assert delivery.peer_error(_response(("not-an-ip", 80))) == "blocked_destination"


# ---- B7: control characters ----


@pytest.mark.parametrize("bad", ["a\r\nb", "a\x00b", "a\x1bb", "a\x7fb", "a\x85b", "a\tb", "a\nb"])
def test_names_reject_control_characters(bad: str) -> None:
    with pytest.raises(ValidationError):
        schemas.CampaignCreate(name=bad)
    with pytest.raises(ValidationError):
        schemas.WorkspaceSettingsUpdate(name=bad)
    with pytest.raises(ValidationError):
        schemas.AddMemberRequest(email="a@example.com", display_name=bad, role="viewer")


def test_multiline_fields_allow_line_feed_but_not_carriage_return() -> None:
    ok = schemas.CampaignCreate(name="Launch", goal="line one\nline two", audience="a\nb")
    assert ok.goal == "line one\nline two"
    with pytest.raises(ValidationError):
        schemas.CampaignCreate(name="Launch", goal="one\r\ntwo")
    with pytest.raises(ValidationError):
        schemas.CampaignUpdate(audience="x\x00y")
    assert schemas.MetricsIn(impressions=1, note="two\nlines").note == "two\nlines"
    with pytest.raises(ValidationError):
        schemas.MetricsIn(impressions=1, note="bad\x1b[31m")
    with pytest.raises(ValidationError):
        schemas.HashtagIn(tag="a\x00b")


def test_digest_subject_has_no_line_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user@127.0.0.1:1/none")
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", "A" * 43 + "=")
    get_settings.cache_clear()
    n = Notification(
        workspace_id=uuid.uuid4(), user_id=uuid.uuid4(), kind="digest.weekly", data={}, post_id=None
    )
    subject, _ = delivery.render_email(n, "Main\r\nBcc: someone@example.com")
    assert "\r" not in subject and "\n" not in subject
    assert subject.startswith("Your week on LinkedIn - Main")


# ---- B9: MP4 brands ----


def _ftyp(major: bytes, compatible: tuple[bytes, ...] = ()) -> bytes:
    size = 16 + 4 * len(compatible)
    return size.to_bytes(4, "big") + b"ftyp" + major + b"\x00\x00\x00\x00" + b"".join(compatible)


@pytest.mark.parametrize("major", [b"isom", b"iso2", b"mp42", b"mp41", b"avc1", b"M4V "])
def test_allowed_mp4_major_brands(major: bytes) -> None:
    kind = media.detect_media(_ftyp(major, (b"isom", b"mp42")))
    assert kind is not None and kind.mime == "video/mp4"


@pytest.mark.parametrize(
    "head",
    [
        _ftyp(b"heic", (b"mif1",)),
        _ftyp(b"mif1", (b"heic",)),
        _ftyp(b"qt  "),
        _ftyp(b"avif"),
        _ftyp(b"3gp5"),
        _ftyp(b"isom", (b"mp42", b"heic")),  # a still-image brand among the compatible ones
        b"\x00\x00\x00\x08ftypisom",  # box too small to hold a brand list
    ],
)
def test_other_ftyp_brands_are_not_mp4(head: bytes) -> None:
    assert media.detect_media(head + b"\x00" * 32) is None


def test_reject_comment_refuses_control_characters_but_keeps_line_feeds() -> None:
    assert (
        schemas.RejectRequest(comment="Please fix\nthe second line").comment == "Please fix\nthe second line"
    )
    for bad in ("bell\x07", "cr\r\nlf", "esc\x1b[31m"):
        with pytest.raises(ValidationError):
            schemas.RejectRequest(comment=bad)


@pytest.mark.parametrize(
    ("kind", "subject"),
    [
        ("security.password_changed", "Your Heardloop password was changed"),
        ("security.mfa_disabled", "Two-step verification was turned off"),
        ("security.sessions_revoked", "Devices were signed out"),
    ],
)
def test_security_notice_emails(kind: str, subject: str) -> None:
    n = Notification(kind=kind, data={"count": 2}, user_id=uuid.uuid4(), workspace_id=uuid.uuid4())
    got_subject, body = delivery.render_email(n, "Acme")
    assert got_subject == subject
    assert "If it was not: change your password" in body and "tab=security" in body
