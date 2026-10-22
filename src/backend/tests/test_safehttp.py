"""Pinned-IP client for user URLs: the address that was checked is the address that is connected to."""

import asyncio
import ipaddress

import httpcore
import httpx
import pytest

from heardloop import delivery, linkpreview
from heardloop.delivery import BlockedDestinationError
from heardloop.safehttp import GuardedTransport

PUBLIC = "8.8.8.8"  # privacy-allow
LAN = "192.168.1.20"  # privacy-allow
METADATA = "169.254.169.254"  # privacy-allow
PRIVATE = "10.0.0.5"  # privacy-allow
LAN_HOST = "receiver.example"
RESPONSE = [b"HTTP/1.1 200 OK\r\n", b"Content-Type: text/plain\r\n", b"Content-Length: 2\r\n", b"\r\n", b"ok"]


class _Recorder(httpcore.AsyncMockBackend):
    """Answers every connection with a canned response and remembers which address was dialled."""

    def __init__(self) -> None:
        super().__init__(RESPONSE)
        self.dialled: list[str] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):  # type: ignore[no-untyped-def]  # noqa: ASYNC109
        self.dialled.append(host)
        return await super().connect_tcp(host, port, timeout, local_address, socket_options)


def _answers(monkeypatch: pytest.MonkeyPatch, table: dict[str, list[str]]) -> list[str]:
    asked: list[str] = []

    async def fake(host: str, *, deadline: float = 0.4) -> list:  # type: ignore[type-arg]
        asked.append(host)
        return [ipaddress.ip_address(a) for a in table.get(host, [])]

    monkeypatch.setattr(delivery, "_resolve_host", fake)
    return asked


def _get(url: str, *, allow_private: bool, inner: _Recorder) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = GuardedTransport(allow_private=allow_private, inner=inner)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as c:
            return await c.get(url)

    return asyncio.run(go())


def test_connects_to_the_checked_address_and_keeps_the_host_name(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _answers(monkeypatch, {"news.example": [PUBLIC]})
    inner = _Recorder()
    res = _get("http://news.example/page", allow_private=False, inner=inner)
    assert res.status_code == 200 and res.text == "ok"
    assert inner.dialled == [PUBLIC]  # the socket goes to the vetted IP, not to a second lookup
    assert asked == ["news.example"]
    assert res.request.headers["host"] == "news.example"


@pytest.mark.parametrize(
    ("answers", "allow_private"),
    [
        ([LAN], False),  # private address for a link preview
        (["127.0.0.1"], True),  # loopback is never allowed
        ([METADATA], True),  # cloud metadata
        ([PUBLIC, PRIVATE], False),  # one bad answer spoils the name
    ],
)
def test_blocked_answers_never_connect(
    monkeypatch: pytest.MonkeyPatch, answers: list[str], allow_private: bool
) -> None:
    _answers(monkeypatch, {"rebind.example": answers})
    inner = _Recorder()
    with pytest.raises(BlockedDestinationError):
        _get("http://rebind.example/", allow_private=allow_private, inner=inner)
    assert inner.dialled == []


def test_webhooks_may_use_the_lan_but_not_own_services(monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, {LAN_HOST: [LAN]})
    inner = _Recorder()
    assert _get(f"http://{LAN_HOST}/hook", allow_private=True, inner=inner).status_code == 200
    with pytest.raises(BlockedDestinationError):
        _get("http://db:5432/", allow_private=True, inner=_Recorder())


def test_dns_rebinding_between_check_and_connect_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """First answer public (passes the early check), second answer private (used for the connection)."""
    answers = iter([[PUBLIC], [LAN]])

    async def flip(host: str, *, deadline: float = 0.4) -> list:  # type: ignore[type-arg]
        return [ipaddress.ip_address(a) for a in next(answers)]

    monkeypatch.setattr(delivery, "_resolve_host", flip)
    assert asyncio.run(delivery.check_destination("http://flip.example/", allow_private=False)) is None
    inner = _Recorder()
    with pytest.raises(BlockedDestinationError):
        _get("http://flip.example/", allow_private=False, inner=inner)
    assert inner.dialled == []


def test_link_preview_reports_a_pinned_block_as_blocked_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([[PUBLIC], [LAN]])

    async def flip(host: str, *, deadline: float = 0.4) -> list:  # type: ignore[type-arg]
        return [ipaddress.ip_address(a) for a in next(answers)]

    monkeypatch.setattr(delivery, "_resolve_host", flip)

    async def go() -> None:
        transport = GuardedTransport(allow_private=False, inner=_Recorder())
        async with httpx.AsyncClient(transport=transport, follow_redirects=False) as c:
            await linkpreview.fetch_preview(c, "https://flip.example/article")

    with pytest.raises(linkpreview.LinkPreviewError) as err:
        asyncio.run(go())
    assert err.value.code == "blocked_destination"
