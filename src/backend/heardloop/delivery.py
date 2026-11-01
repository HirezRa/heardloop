"""Worker side of notifications: send pending e-mails and webhooks, remind about expiring LinkedIn tokens.

Each batch is claimed with FOR UPDATE SKIP LOCKED, so several workers never send the same message twice.
Logs carry ids only - never e-mail addresses, webhook URLs, secrets or post text.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import smtplib
import ssl
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop.config import Settings, get_settings
from heardloop.models import (
    LinkedInAccount,
    LinkedInAccountStatus,
    Membership,
    Notification,
    Post,
    PostVersion,
    User,
    WebhookDelivery,
    Workspace,
    WorkspaceRole,
)
from heardloop.notify import members_with_roles, notify, post_link
from heardloop.security.crypto import TokenDecryptionError, TokenProtector

log = logging.getLogger("heardloop.delivery")

Clock = Callable[[], datetime]

EMAIL_BATCH = 20
EMAIL_MAX_ATTEMPTS = 3
WEBHOOK_BATCH = 10
WEBHOOK_TIMEOUT = 10.0
WEBHOOK_DEADLINE = 15.0  # total seconds per call
WEBHOOK_LEASE = timedelta(minutes=2)
CYCLE_BUDGET = timedelta(seconds=60)
DELIVERY_RETENTION = timedelta(days=30)
WEBHOOK_BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))
WEBHOOK_MAX_ATTEMPTS = len(WEBHOOK_BACKOFF) + 1
EXPIRY_WARNING = timedelta(days=7)


def webhook_secret_context(workspace_id: object) -> str:
    return f"workspace-webhook:{workspace_id}"


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Receivers verify: HMAC-SHA256(secret, "<timestamp>.<raw body>") == X-Heardloop-Signature."""
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---- e-mail ----


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpSender:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _send_sync(self, msg: EmailMessage) -> None:
        s = self._s
        context = ssl.create_default_context()
        smtp: smtplib.SMTP
        if s.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=context)
        else:
            smtp = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
        with smtp:
            if s.smtp_security == "starttls":
                smtp.starttls(context=context)
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(msg)

    async def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = self._s.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        await asyncio.to_thread(self._send_sync, msg)


_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def _header_safe(value: str) -> str:
    """One line, no control characters: defence in depth against header injection through names."""
    return _CONTROLS.sub(" ", value).strip()


_SECURITY_EMAILS = {
    "security.password_changed": ("Your Heardloop password was changed", "Your password was changed."),
    "security.mfa_enabled": (
        "Two-step verification was turned on",
        "Two-step verification was turned on for your account.",
    ),
    "security.mfa_disabled": (
        "Two-step verification was turned off",
        "Two-step verification was turned off for your account.",
    ),
    "security.recovery_codes_regenerated": (
        "New recovery codes were created",
        "New recovery codes were created; the old ones no longer work.",
    ),
    "security.sessions_revoked": (
        "Devices were signed out",
        "Other devices were signed out of your account.",
    ),
}


def _security_email(kind: str, d: dict[str, object]) -> tuple[str, list[str]]:
    subject, first = _SECURITY_EMAILS.get(kind, ("Account security change", "Your sign-in settings changed."))
    lines = [first]
    if kind == "security.sessions_revoked" and isinstance(d.get("count"), int):
        lines = [f"{d['count']} other device(s) were signed out of your account."]
    lines += [
        "",
        "If this was you, nothing else to do.",
        "If it was not: change your password, check Settings -> Sign-in & security,",
        "and tell the workspace owner.",
    ]
    return subject, lines


def render_email(n: Notification, workspace_name: str) -> tuple[str, str]:
    """Plain-text e-mail. Subjects are fixed strings plus the workspace name, stripped of line breaks."""
    d = n.data or {}
    link = post_link(n.post_id) if n.post_id else get_settings().app_base_url
    actor = d.get("actor_name") or "Someone"
    lines: list[str]
    if n.kind == "post.submitted":
        subject = "A post is waiting for your approval"
        lines = [f"{actor} submitted a post for approval in {workspace_name}."]
    elif n.kind == "post.approved":
        subject = "Your post was approved"
        lines = [f"{actor} approved your post in {workspace_name}. It can now be scheduled."]
    elif n.kind == "post.rejected":
        subject = "Your post was sent back"
        lines = [
            f"{actor} sent your post back in {workspace_name}.",
            "",
            f"Comment: {d.get('comment') or '-'}",
        ]
    elif n.kind == "post.published":
        subject = "Your post is live on LinkedIn"
        lines = [f"Your post in {workspace_name} was published."]
        if d.get("linkedin_url"):
            lines.append(f"On LinkedIn: {d['linkedin_url']}")
    elif n.kind == "post.failed":
        subject = "A post could not be published"
        lines = [
            f"A post in {workspace_name} could not be published (reason: {d.get('error') or 'unknown'})."
        ]
    elif n.kind == "account.expiring":
        subject = "Your LinkedIn connection expires soon"
        link = f"{get_settings().app_base_url}/?section=accounts"
        lines = [
            f"The LinkedIn connection '{d.get('account_name') or ''}' in {workspace_name} "
            f"expires on {str(d.get('expires_at') or '')[:10]}.",
            "Reconnect it in Accounts so scheduled posts keep working.",
        ]
    elif n.kind == "account.reconnect_needed":
        subject = "Reconnect a LinkedIn account"
        link = f"{get_settings().app_base_url}/?section=accounts"
        lines = [
            f"A LinkedIn connection in {workspace_name} needs reconnecting "
            f"(reason: {d.get('reason') or 'unknown'}).",
            "Posts for that account fail until you reconnect it in Accounts.",
        ]
    elif n.kind == "backup.stale":
        subject = f"Backup overdue - {_header_safe(workspace_name)}"
        link = f"{get_settings().app_base_url}/?section=settings&tab=workspace"
        if d.get("reason") == "missing":
            lines = ["No backup has been found since Heardloop started."]
        else:
            age, finished = d.get("age_hours") or "?", d.get("finished_at") or "?"
            lines = [f"The last backup is {age} hours old (finished {finished})."]
        lines.append("Check the backup service: docker compose logs backup. Run one with scripts/backup-now.")
    elif n.kind == "backup.failed":
        subject = f"Backup failed - {_header_safe(workspace_name)}"
        link = f"{get_settings().app_base_url}/?section=settings&tab=workspace"
        lines = [
            f"The last backup failed (reason: {d.get('error') or 'unknown'}).",
            "Check the backup service: docker compose logs backup. Run one now with scripts/backup-now.",
        ]
    elif n.kind == "app.update_available":
        subject = f"Heardloop v{d.get('latest') or '?'} is available"
        link = f"{get_settings().app_base_url}/?section=settings&tab=about"
        lines = [
            f"Heardloop v{d.get('latest') or '?'} is available (you are on v{d.get('current') or '?'}).",
            "See What's new and How to update in Settings -> About. The app never installs updates itself.",
            "Update steps: https://github.com/HirezRa/heardloop/blob/main/docs/INSTALL.md#update",
        ]
        if d.get("url"):
            lines.append(f"Release: {d['url']}")
    elif n.kind.startswith("security."):
        subject, lines = _security_email(n.kind, d)
        link = f"{get_settings().app_base_url}/?section=settings&tab=security"
    elif n.kind == "digest.weekly":
        subject = f"Your week on LinkedIn - {_header_safe(workspace_name)}"
        link = f"{get_settings().app_base_url}/?section=reports"
        best = d.get("best_post") if isinstance(d.get("best_post"), dict) else None
        lines = [
            f"Published last week: {d.get('published_last_week', 0)}",
            f"Scheduled this week: {d.get('scheduled_this_week', 0)}",
            f"Waiting for approval: {d.get('waiting_approval', 0)}",
        ]
        if best:
            lines.append(
                f"Strongest recent post: {best.get('engagement', 0)} interactions - "
                f"{post_link(uuid.UUID(str(best.get('post_id'))))}"
            )
        if d.get("unmeasured"):
            lines.append(
                f"Published posts without metrics yet: {d['unmeasured']} - add them from the post page."
            )
    else:
        subject = "Heardloop notification"
        lines = [f"Something changed in {workspace_name}."]
    lines += ["", f"Open: {link}", "", "You get this because e-mail notifications are on in Heardloop."]
    return _header_safe(subject), "\n".join(lines)


async def _is_member(db: AsyncSession, workspace_id: object, user_id: object) -> bool:
    found = (
        await db.execute(
            select(Membership.user_id).where(
                Membership.workspace_id == workspace_id, Membership.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    return found is not None


async def deliver_emails(
    factory: async_sessionmaker[AsyncSession], sender: EmailSender | None, clock: Clock
) -> int:
    """One short transaction per e-mail (a crash re-sends at most one), within a per-cycle time budget."""
    deadline = clock() + CYCLE_BUDGET
    sent = 0
    for _ in range(EMAIL_BATCH):
        if clock() >= deadline:
            break
        async with factory() as db:
            n = (
                await db.execute(
                    select(Notification)
                    .where(Notification.email_status == "pending")
                    .order_by(Notification.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if n is None:
                break
            user = await db.get(User, n.user_id)
            ws = await db.get(Workspace, n.workspace_id)
            if (
                sender is None
                or user is None
                or ws is None
                or not user.is_active
                or not user.email_notifications
                or not await _is_member(db, n.workspace_id, n.user_id)  # left the workspace meanwhile
            ):
                n.email_status = "skipped"
                await db.commit()
                continue
            subject, body = render_email(n, ws.name)
            try:
                await sender.send(user.email, subject, body)
            except Exception as exc:  # SMTP and network errors of all kinds
                n.email_attempts += 1
                if n.email_attempts >= EMAIL_MAX_ATTEMPTS:
                    n.email_status = "failed"
                log.warning(
                    "E-mail not sent",
                    extra={
                        "notification_id": str(n.id),
                        "attempt": n.email_attempts,
                        "error": type(exc).__name__,
                    },
                )
                await db.commit()
                if n.email_status == "pending":
                    break  # mail server trouble: try again next cycle instead of hammering it now
                continue
            n.email_status = "sent"
            await db.commit()
            sent += 1
    return sent


# ---- webhook ----

# Never call these from the worker: the stack's own services and addresses that are never a webhook receiver.
BLOCKED_HOSTS = frozenset({"localhost", "db", "api", "worker", "web"})
_SERVICE_HOSTS = ("db", "api", "web", "worker")

# Addresses of this stack's own services (and the public host of APP_BASE_URL), filled by
# `refresh_blocked_service_ips()` at API and worker start-up. A webhook or preview must never reach them.
_blocked_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()


class BlockedDestinationError(Exception):
    """Raised by `safehttp` when a name resolves to an address the server must not call. Not an httpx error on
    purpose: callers record it as `blocked_destination` and never retry it."""


def _unwrap(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        return addr.ipv4_mapped
    return addr


def _parse_ip(raw: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return _unwrap(ipaddress.ip_address(str(raw).split("%")[0]))
    except ValueError:
        return None


_RESOLVE_TIMEOUT = 0.4  # short: outside Docker, "db"/"api" are not real DNS names and must not hang startup


async def _resolve_host(
    host: str, *, deadline: float = _RESOLVE_TIMEOUT
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolves one host with a deadline. Empty on timeout, NXDOMAIN, or literal parse failure."""
    direct = _parse_ip(host)
    if direct is not None:
        return [direct]
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None), timeout=deadline)
    except (OSError, TimeoutError):
        return []
    out: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        addr = _parse_ip(info[4][0])
        if addr is not None:
            out.append(addr)
    return out


async def refresh_blocked_service_ips() -> None:
    """Resolves the stack's own host names and the APP_BASE_URL host; they become forbidden targets."""
    hosts = list(_SERVICE_HOSTS)
    try:
        base_host = urlparse(get_settings().app_base_url).hostname
    except ValueError:
        base_host = None
    if base_host:
        hosts.append(base_host)
    found: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for host in hosts:
        # Outside the compose network (e.g. pytest on the host) these names time out; that is fine.
        found.update(await _resolve_host(host))
    _blocked_ips.clear()
    _blocked_ips.update(found)
    log.info("Blocked own service addresses", extra={"count": len(found)})


def _address_error(addr: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool) -> str | None:
    addr = _unwrap(addr)
    if addr in _blocked_ips:
        return "blocked_destination"
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast or addr.is_reserved:
        return "blocked_destination"
    if addr.is_global or (allow_private and addr.is_private):
        return None
    return "blocked_destination"  # e.g. shared address space 100.64.0.0/10, benchmarking and similar ranges


def peer_error(response: httpx.Response, *, allow_private: bool = True) -> str | None:
    """Peer re-check after connect: the address the socket really reached must pass the same rules.

    A last check on the socket; the pinning itself is done by `safehttp` at connect time. Not available with
    mocked transports (no network stream), where it does nothing.
    """
    stream = response.extensions.get("network_stream")
    if stream is None:
        return None
    try:
        peer = stream.get_extra_info("server_addr")
    except Exception:  # a transport that cannot tell us is treated as "unknown"
        return None
    if not peer:
        return None
    addr = _parse_ip(peer[0])
    if addr is None:
        return "blocked_destination"
    return _address_error(addr, allow_private=allow_private)


async def check_destination(url: str, *, allow_private: bool = True) -> str | None:
    """Returns an error code if a user-supplied URL points somewhere the server must not call (SSRF guard).

    Webhooks keep private network addresses (n8n usually runs on the same Docker network); link previews
    pass `allow_private=False` because a LAN page is useless on LinkedIn and could expose internal services.
    Everything that is not globally routable is refused (`not addr.is_global`: shared address space
    100.64.0.0/10, 0.0.0.0/8, documentation and benchmarking ranges, ...), IPv4-mapped IPv6 addresses are
    unwrapped first. With `allow_private` RFC 1918 / unique-local addresses stay allowed. Always blocked:
    loopback, link-local (incl. the cloud metadata address), unspecified, multicast, reserved, and the
    addresses of this stack's own services (see `refresh_blocked_service_ips`).

    This is the early, friendly check (clear error codes before any request). The connection itself goes
    through `safehttp.guarded_client`, which resolves once more, checks again and connects only to the
    address it checked (IP pinning), so DNS rebinding between this check and the connection cannot reach a
    blocked address. `peer_error` stays as a last check on the socket.
    """
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return "blocked_destination"
    if not host or host in BLOCKED_HOSTS:
        return "blocked_destination"
    addrs = await _resolve_host(host)
    if not addrs:
        return "dns_error"
    for addr in addrs:
        if _address_error(addr, allow_private=allow_private):
            return "blocked_destination"
    return None


async def _final_payload(db: AsyncSession, ws: Workspace, d: WebhookDelivery) -> dict[str, Any]:
    """Post text, reviewer comments and people's/accounts' names are added only if the owner allows it."""
    payload: dict[str, Any] = json.loads(json.dumps(d.payload))
    if not ws.webhook_include_text:
        data = payload.get("data") or {}
        for key in ("comment", "actor_name", "account_name"):
            data.pop(key, None)
        return payload
    post = payload.get("post")
    if post and post.get("id"):
        p = await db.get(Post, uuid.UUID(post["id"]))
        version = await db.get(PostVersion, p.current_version_id) if p and p.current_version_id else None
        post["text"] = version.body if version else None
    return payload


async def _send_one(
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    protector: TokenProtector,
    clock: Clock,
    delivery_id: int,
) -> bool:
    async with factory() as db:
        d = await db.get(WebhookDelivery, delivery_id)
        if d is None or d.status != "pending":
            return False
        ws = await db.get(Workspace, d.workspace_id)
        if ws is None or not ws.webhook_url or not ws.webhook_secret_encrypted:
            d.status, d.last_error = "failed", "webhook_removed"
            await db.commit()
            return False
        try:
            secret = protector.unprotect(ws.webhook_secret_encrypted, webhook_secret_context(ws.id))
        except TokenDecryptionError:
            d.status, d.last_error = "failed", "secret_unreadable"
            await db.commit()
            return False
        url = ws.webhook_url
        payload = await _final_payload(db, ws, d)
        event = d.event
        await db.commit()  # release everything before network IO

    status_code: int | None = None
    error = await check_destination(url)
    if error is None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ts = str(int(clock().timestamp()))
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Heardloop-Webhook/1",
            "X-Heardloop-Event": event,
            "X-Heardloop-Delivery": str(delivery_id),
            "X-Heardloop-Timestamp": ts,
            "X-Heardloop-Signature": sign(secret, ts, body),
        }

        async def post() -> tuple[int, str | None]:
            async with http.stream(
                "POST", url, content=body, headers=headers, timeout=WEBHOOK_TIMEOUT, follow_redirects=False
            ) as res:
                # The response body is never read. The peer is re-checked once the connection exists.
                return res.status_code, peer_error(res)

        try:
            # Total deadline: httpx timeouts are per phase, a slow server could drip forever.
            status_code, peer_blocked = await asyncio.wait_for(post(), WEBHOOK_DEADLINE)
            if peer_blocked:
                status_code, error = None, peer_blocked
            elif status_code is not None and 200 <= status_code < 300:
                error = None
            else:
                error = f"http_{status_code}"
        except TimeoutError:
            error = "timeout"
        except httpx.HTTPError as exc:
            error = type(exc).__name__[:200]
        except (
            BlockedDestinationError
        ):  # pinned-IP check at connect time: the name now points somewhere forbidden
            error = "blocked_destination"

    async with factory() as db:
        d = (
            await db.execute(
                select(WebhookDelivery).where(WebhookDelivery.id == delivery_id).with_for_update()
            )
        ).scalar_one_or_none()
        if d is None or d.status != "pending":
            return False
        d.attempts += 1
        d.last_status_code = status_code
        d.last_error = error
        if error is None:
            d.status, d.sent_at = "sent", clock()
        elif error == "blocked_destination" or d.attempts >= WEBHOOK_MAX_ATTEMPTS:
            d.status = "failed"
            log.warning("Webhook failed", extra={"delivery_id": d.id, "error": error})
        else:
            d.next_attempt_at = clock() + WEBHOOK_BACKOFF[d.attempts - 1]
        await db.commit()
        return error is None


async def deliver_webhooks(
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    protector: TokenProtector,
    clock: Clock,
) -> int:
    """Claim a batch with a short lease, commit, then send each one outside any long transaction."""
    now = clock()
    async with factory() as db:
        batch = list(
            (
                await db.execute(
                    select(WebhookDelivery)
                    .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now)
                    .order_by(WebhookDelivery.id)
                    .limit(WEBHOOK_BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        for d in batch:
            d.next_attempt_at = now + WEBHOOK_LEASE  # another worker skips it while we send
        ids = [d.id for d in batch]
        await db.commit()
    deadline = clock() + CYCLE_BUDGET
    sent = 0
    for delivery_id in ids:
        if clock() >= deadline:
            break  # the lease expires and the rest go out next cycle
        sent += await _send_one(factory, http, protector, clock, delivery_id)
    return sent


async def prune_deliveries(factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
    """Retention: sent/failed webhook payloads are kept 30 days for the deliveries view, then deleted."""
    async with factory() as db:
        await db.execute(
            delete(WebhookDelivery).where(
                WebhookDelivery.status.in_(["sent", "failed"]),
                WebhookDelivery.created_at < clock() - DELIVERY_RETENTION,
            )
        )
        await db.commit()


# ---- reminders ----


async def remind_expiring_tokens(factory: async_sessionmaker[AsyncSession], clock: Clock) -> int:
    """FR-05: tell owners 7 days before a LinkedIn token expires. Once per account and expiry date."""
    now = clock()
    created = 0
    async with factory() as db:
        accounts = list(
            (
                await db.execute(
                    select(LinkedInAccount).where(
                        LinkedInAccount.status == LinkedInAccountStatus.ACTIVE,
                        LinkedInAccount.access_token_expires_at > now,
                        LinkedInAccount.access_token_expires_at <= now + EXPIRY_WARNING,
                    )
                )
            ).scalars()
        )
        for acc in accounts:
            expires = acc.access_token_expires_at.isoformat()
            done = (
                await db.execute(
                    select(Notification.id)
                    .where(
                        Notification.workspace_id == acc.workspace_id,
                        Notification.kind == "account.expiring",
                        Notification.data["account_id"].astext == str(acc.id),
                        Notification.data["expires_at"].astext == expires,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if done is not None:
                continue
            owners = await members_with_roles(db, acc.workspace_id, [WorkspaceRole.OWNER])
            if not owners:
                continue
            await notify(
                db,
                workspace_id=acc.workspace_id,
                kind="account.expiring",
                recipients=owners,
                now=now,
                data={"account_id": str(acc.id), "account_name": acc.display_name, "expires_at": expires},
            )
            created += 1
        await db.commit()
    return created
