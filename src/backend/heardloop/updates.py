"""GitHub release update check (FR-update): poll api.github.com only; never install from the app."""

from __future__ import annotations

import json
import logging
import re
import ssl
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heardloop.config import Settings, get_settings
from heardloop.models import AppMeta, Notification, Workspace, WorkspaceRole
from heardloop.notify import members_with_roles, notify
from heardloop.timeutil import utcnow
from heardloop.version import app_version

log = logging.getLogger("heardloop.updates")

META_KEY = "update_check"
GITHUB_HOST = "api.github.com"
TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
REPO_RE = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
MAX_BODY_BYTES = 256 * 1024
MAX_NOTES = 4000
TIMEOUT_S = 10.0

Clock = Callable[[], datetime]


class UpdateInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    latest: str
    url: str
    published_at: str | None = None
    notes: str = ""


class UpdateCheckState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    checked_at: str
    error: str | None = None
    latest: str | None = None
    url: str | None = None
    published_at: str | None = None
    notes: str | None = None
    # Preserved across failed attempts so an error never erases the last good release.
    last_ok_at: str | None = None
    last_ok_latest: str | None = None
    last_ok_url: str | None = None
    last_ok_published_at: str | None = None
    last_ok_notes: str | None = None


def parse_semver(value: str) -> tuple[int, int, int] | None:
    text = value.strip().lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        return None
    major, minor, patch = (int(p) for p in text.split("."))
    return major, minor, patch


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_semver(latest), parse_semver(current)
    if a is None or b is None:
        return False
    return a > b


def release_url_ok(url: str, repo: str) -> bool:
    prefix = f"https://github.com/{repo}/releases/"
    return url.startswith(prefix)


def filter_latest_release(payload: dict[str, Any], repo: str) -> UpdateInfo | None:
    """Accept only a non-draft, non-prerelease release whose tag is vX.Y.Z."""
    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name") or "")
    m = TAG_RE.fullmatch(tag)
    if not m:
        return None
    url = str(payload.get("html_url") or "")
    if not release_url_ok(url, repo):
        return None
    body = payload.get("body")
    notes = (body if isinstance(body, str) else "")[:MAX_NOTES]
    published = payload.get("published_at")
    return UpdateInfo(
        latest=m.group(1),
        url=url,
        published_at=str(published) if published else None,
        notes=notes,
    )


def _error_state(now: datetime, code: str) -> UpdateCheckState:
    return UpdateCheckState(checked_at=now.isoformat(), error=code)


def _is_tls_failure(exc: BaseException) -> bool:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        name = type(cur).__name__.lower()
        if "ssl" in name or "certificate" in name or "tls" in name:
            return True
        msg = str(cur).lower()
        if any(tok in msg for tok in ("certificate", "ssl", "tls", "unable to verify", "cert verify")):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def classify_request_error(exc: BaseException) -> str:
    """Map transport failures to stable UI codes (no secrets in the code string)."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if _is_tls_failure(exc):
        return "tls"
    return "network"


def ssl_context_with_extra_ca(extra_ca: str) -> ssl.SSLContext:
    """System trust store plus an optional organisation root CA (PEM path)."""
    ctx = ssl.create_default_context()
    path = Path(extra_ca)
    if not path.is_file():
        raise FileNotFoundError(f"EXTRA_CA_CERT_FILE not found: {extra_ca}")
    ctx.load_verify_locations(cafile=str(path))
    return ctx


async def fetch_latest_release(
    http: httpx.AsyncClient, settings: Settings, *, now: datetime | None = None
) -> UpdateCheckState:
    """Call GitHub releases/latest. Never logs response bodies. Fixed host only."""
    clock = now or utcnow()
    if not settings.update_check:
        return _error_state(clock, "disabled")
    repo = settings.update_check_repo
    if not REPO_RE.fullmatch(repo):
        return _error_state(clock, "bad_repo")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Heardloop/{app_version()}",
    }
    token = settings.update_check_token.get_secret_value().strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://{GITHUB_HOST}/repos/{repo}/releases/latest"
    try:
        res = await http.get(url, headers=headers, timeout=TIMEOUT_S, follow_redirects=False)
    except httpx.HTTPError as exc:
        code = classify_request_error(exc)
        log.warning("Update check request failed", extra={"error": code})
        return _error_state(clock, code)
    if res.status_code == 404:
        return _error_state(clock, "not_found")
    if res.status_code == 429:
        log.warning("Update check HTTP error", extra={"status": res.status_code})
        return _error_state(clock, "rate_limited")
    if res.status_code != 200:
        log.warning("Update check HTTP error", extra={"status": res.status_code})
        return _error_state(clock, "bad_response")
    raw = res.content
    if len(raw) > MAX_BODY_BYTES:
        return _error_state(clock, "oversized")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _error_state(clock, "bad_json")
    if not isinstance(payload, dict):
        return _error_state(clock, "bad_json")
    info = filter_latest_release(payload, repo)
    if info is None:
        return _error_state(clock, "ignored")
    return UpdateCheckState(
        checked_at=clock.isoformat(),
        error=None,
        latest=info.latest,
        url=info.url,
        published_at=info.published_at,
        notes=info.notes,
    )


def merge_last_ok(prev: UpdateCheckState | None, state: UpdateCheckState) -> UpdateCheckState:
    """On success, copy release into last_ok_*; on error, keep previous last_ok_*."""
    if state.error is None and state.latest and state.url:
        return state.model_copy(
            update={
                "last_ok_at": state.checked_at,
                "last_ok_latest": state.latest,
                "last_ok_url": state.url,
                "last_ok_published_at": state.published_at,
                "last_ok_notes": state.notes,
            }
        )
    if prev is None:
        return state
    last_ok_at = prev.last_ok_at
    last_ok_latest = prev.last_ok_latest
    last_ok_url = prev.last_ok_url
    last_ok_published_at = prev.last_ok_published_at
    last_ok_notes = prev.last_ok_notes
    # Older rows stored a success without last_ok_* fields.
    if not last_ok_latest and prev.error is None and prev.latest and prev.url:
        last_ok_at = prev.checked_at
        last_ok_latest = prev.latest
        last_ok_url = prev.url
        last_ok_published_at = prev.published_at
        last_ok_notes = prev.notes
    return state.model_copy(
        update={
            "last_ok_at": last_ok_at,
            "last_ok_latest": last_ok_latest,
            "last_ok_url": last_ok_url,
            "last_ok_published_at": last_ok_published_at,
            "last_ok_notes": last_ok_notes,
        }
    )


async def load_update_state(db: AsyncSession, *, populate_existing: bool = False) -> UpdateCheckState | None:
    row = await db.get(AppMeta, META_KEY, populate_existing=populate_existing)
    if row is None or not row.value:
        return None
    try:
        return UpdateCheckState.model_validate_json(row.value)
    except ValidationError:
        return None


async def store_update_state(db: AsyncSession, state: UpdateCheckState, now: datetime) -> None:
    prev = await load_update_state(db)
    state = merge_last_ok(prev, state)
    row = await db.get(AppMeta, META_KEY)
    payload = state.model_dump_json()
    if row is None:
        db.add(AppMeta(key=META_KEY, value=payload, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now


async def _already_notified(db: AsyncSession, workspace_id: Any, latest: str) -> bool:
    rows = await db.execute(
        select(Notification.data)
        .where(
            Notification.workspace_id == workspace_id,
            Notification.kind == "app.update_available",
        )
        .order_by(Notification.created_at.desc())
        .limit(100)
    )
    return any((data or {}).get("latest") == latest for (data,) in rows.all())


async def notify_owners_of_update(
    db: AsyncSession, state: UpdateCheckState, current: str, now: datetime
) -> int:
    if state.error or not state.latest or not state.url:
        return 0
    if not is_newer(state.latest, current):
        return 0
    data = {"current": current, "latest": state.latest, "url": state.url}
    alerted = 0
    workspace_ids = list((await db.execute(select(Workspace.id))).scalars())
    for workspace_id in workspace_ids:
        if await _already_notified(db, workspace_id, state.latest):
            continue
        owners = await members_with_roles(db, workspace_id, [WorkspaceRole.OWNER])
        if not owners:
            continue
        await notify(
            db,
            workspace_id=workspace_id,
            kind="app.update_available",
            recipients=owners,
            now=now,
            data=data,
        )
        alerted += 1
    return alerted


async def run_update_check(
    factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    clock: Clock = utcnow,
) -> UpdateCheckState:
    settings = get_settings()
    now = clock()
    extra = settings.extra_ca_cert_file.strip()
    if extra:
        ctx = ssl_context_with_extra_ca(extra)
        async with httpx.AsyncClient(follow_redirects=False, verify=ctx) as dedicated:
            state = await fetch_latest_release(dedicated, settings, now=now)
    else:
        state = await fetch_latest_release(http, settings, now=now)
    current = app_version()
    async with factory() as db:
        await store_update_state(db, state, now)
        stored = await load_update_state(db)
        assert stored is not None
        if stored.error is None and stored.latest and is_newer(stored.latest, current):
            n = await notify_owners_of_update(db, stored, current, now)
            if n:
                log.info("Update available notified", extra={"latest": stored.latest, "workspaces": n})
        await db.commit()
        return stored


def public_update_field(state: UpdateCheckState | None, current: str) -> dict[str, Any] | None:
    """Banner payload when a newer release is known (including last success after a failed check)."""
    if state is None:
        return None
    if state.error is None and state.latest and state.url and is_newer(state.latest, current):
        return {
            "available": True,
            "latest": state.latest,
            "url": state.url,
            "published_at": state.published_at,
            "notes": state.notes or "",
        }
    if state.error and state.last_ok_latest and state.last_ok_url and is_newer(state.last_ok_latest, current):
        return {
            "available": True,
            "latest": state.last_ok_latest,
            "url": state.last_ok_url,
            "published_at": state.last_ok_published_at,
            "notes": state.last_ok_notes or "",
        }
    return None


def public_update_status(state: UpdateCheckState | None, current: str, *, enabled: bool) -> dict[str, Any]:
    """Honest owner-facing status for About (never claims 'current' after a failed check)."""
    if not enabled:
        return {"status": "disabled"}
    if state is None:
        return {"status": "never"}
    if state.error:
        out: dict[str, Any] = {
            "status": "error",
            "error": state.error,
            "checked_at": state.checked_at,
            "last_ok_at": state.last_ok_at,
            "last_ok_latest": state.last_ok_latest,
        }
        banner = public_update_field(state, current)
        if banner:
            out["update"] = banner
        return out
    if state.latest and state.url and is_newer(state.latest, current):
        return {
            "status": "newer",
            "latest": state.latest,
            "url": state.url,
            "published_at": state.published_at,
            "notes": state.notes or "",
            "checked_at": state.checked_at,
        }
    if state.latest is not None and state.error is None:
        return {"status": "current", "checked_at": state.checked_at}
    return {"status": "never"}
