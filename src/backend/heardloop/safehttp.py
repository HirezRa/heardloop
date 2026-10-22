"""HTTP client for user-supplied URLs (webhooks, link previews) that pins the vetted IP address.

`delivery.check_destination` resolves a name and checks it, but the HTTP library used to resolve the name a
second time when connecting, so a DNS-rebinding server could answer "public" first and "private" second. Here
the connection itself resolves the name once, checks every address with the same rules, and connects only to
an address it has just checked. TLS still uses the original host name (SNI and certificate check), because
httpcore takes those from the URL, not from the socket address.

No keep-alive: every request resolves and checks again. Environment proxies are ignored
(`trust_env=False`), as a proxy would do its own resolution.
"""

import ipaddress
import typing

import httpcore
import httpx

from heardloop import delivery
from heardloop.delivery import BlockedDestinationError

RESOLVE_TIMEOUT = 5.0  # real DNS for an outside host; the start-up lookup of own services stays short


class _GuardedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, *, allow_private: bool, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._allow_private = allow_private
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109  # httpcore interface
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        name = host.lower().rstrip(".")
        if not name or name in delivery.BLOCKED_HOSTS:
            raise BlockedDestinationError(host)
        addrs = await delivery._resolve_host(name, deadline=RESOLVE_TIMEOUT)
        if not addrs:
            raise httpcore.ConnectError("dns_error")
        # Same rule as check_destination: one bad address makes the whole name unusable.
        if any(delivery._address_error(a, allow_private=self._allow_private) for a in addrs):
            raise BlockedDestinationError(host)
        last: Exception | None = None
        for addr in _ordered(addrs):
            try:
                return await self._inner.connect_tcp(
                    str(addr),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109  # httpcore interface
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedDestinationError("unix socket")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _ordered(
    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """IPv4 first (most self-hosted receivers), duplicates removed, order otherwise kept."""
    seen: set[str] = set()
    out = []
    for addr in sorted(addrs, key=lambda a: a.version):
        if str(addr) not in seen:
            seen.add(str(addr))
            out.append(addr)
    return out


class GuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, allow_private: bool, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=20,
            max_keepalive_connections=0,  # resolve and check again for every request
            keepalive_expiry=0,
            http1=True,
            http2=False,
            network_backend=_GuardedBackend(allow_private=allow_private, inner=inner),
        )


def guarded_client(*, allow_private: bool) -> httpx.AsyncClient:
    """Webhooks: `allow_private=True` (n8n on the LAN). Link previews: `False`."""
    return httpx.AsyncClient(
        transport=GuardedTransport(allow_private=allow_private), follow_redirects=False, trust_env=False
    )
