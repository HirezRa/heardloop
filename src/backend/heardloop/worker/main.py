"""Worker process. Run: python -m heardloop.worker.main"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from heardloop.auth.service import prune_expired_sessions
from heardloop.backup import check_backup_alerts
from heardloop.config import get_settings
from heardloop.db import create_engine, create_session_factory
from heardloop.delivery import (
    SmtpSender,
    deliver_emails,
    deliver_webhooks,
    prune_deliveries,
    refresh_blocked_service_ips,
    remind_expiring_tokens,
)
from heardloop.digest import send_weekly_digests
from heardloop.keycheck import ensure_token_key
from heardloop.linkedin.client import LinkedInClient
from heardloop.linkedin.publisher import LinkedInPublisher
from heardloop.logging_setup import configure_logging
from heardloop.media_gc import sweep_media
from heardloop.publishing.base import NotConfiguredPublisher, Publisher
from heardloop.security.crypto import TokenProtector
from heardloop.timeutil import utcnow
from heardloop.updates import run_update_check
from heardloop.worker.dispatcher import run_once

log = logging.getLogger("heardloop.worker")
UPDATE_CHECK_INTERVAL = timedelta(hours=12)


async def _wait_for_schema(engine: AsyncEngine, stop: asyncio.Event) -> None:
    """The API container runs `alembic upgrade head`; wait until the posts table exists."""
    while not stop.is_set():
        try:
            async with engine.connect() as conn:
                found = (await conn.execute(text("SELECT to_regclass('public.app_meta')"))).scalar()
            if found:
                return
            log.info("Waiting for database migrations")
        except Exception:
            log.warning("Database not ready yet", exc_info=True)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=5)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    protector = TokenProtector(settings.token_key.get_secret_value())
    http = httpx.AsyncClient(follow_redirects=False)
    publisher: Publisher = (
        LinkedInPublisher(LinkedInClient(settings, http))
        if settings.linkedin_configured
        else NotConfiguredPublisher()
    )
    if not settings.linkedin_configured:
        log.warning("LinkedIn is not configured; due posts will fail with linkedin_not_configured")
    sender = SmtpSender(settings) if settings.email_configured else None
    if sender is None:
        log.info("E-mail is not configured (SMTP_HOST / SMTP_FROM); notifications are in-app only")
    worker_started_at = utcnow()
    next_reminder_check = worker_started_at
    next_update_check = worker_started_at  # run once at start, then every 12 h

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(sig, stop.set)

    await _wait_for_schema(engine, stop)
    await ensure_token_key(factory, settings.token_key.get_secret_value())
    await refresh_blocked_service_ips()
    log.info("Worker started", extra={"poll_seconds": settings.worker_poll_seconds})

    while not stop.is_set():
        try:
            handled = await run_once(factory, publisher, utcnow, protector)
            if handled:
                log.info("Processed due posts", extra={"count": handled})
        except Exception:
            log.exception("Worker cycle failed")
        # Notifications run after publishing so a slow mail server never delays a post.
        # Separate guards: a broken mail server must not stop webhooks or reminders.
        try:
            await deliver_emails(factory, sender, utcnow)
        except Exception:
            log.exception("E-mail delivery failed")
        try:
            await deliver_webhooks(factory, http, protector, utcnow)
        except Exception:
            log.exception("Webhook delivery failed")
        if utcnow() >= next_update_check:
            next_update_check = utcnow() + UPDATE_CHECK_INTERVAL
            try:
                await run_update_check(factory, http, clock=utcnow)
            except Exception:
                log.exception("Update check failed")
        if utcnow() >= next_reminder_check:
            next_reminder_check = utcnow() + timedelta(hours=1)
            try:
                await remind_expiring_tokens(factory, utcnow)
                await prune_deliveries(factory, utcnow)
                await send_weekly_digests(factory, utcnow)
            except Exception:
                log.exception("Hourly notification tasks failed")
            try:
                await check_backup_alerts(factory, utcnow, worker_started_at)
            except Exception:
                log.exception("Backup status check failed")
            # Housekeeping, guarded separately so one failing job never blocks the others.
            jobs: list[tuple[str, Callable[[], Awaitable[object]]]] = [
                ("session sweep", lambda: prune_expired_sessions(factory, utcnow())),
                ("media sweep", lambda: sweep_media(factory, settings.media_root, utcnow)),
                ("service address refresh", refresh_blocked_service_ips),
            ]
            for name, job in jobs:
                try:
                    await job()
                except Exception:
                    log.exception("Hourly job failed", extra={"job": name})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)

    await http.aclose()
    await engine.dispose()
    log.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
