"""Unit tests for GitHub update-check helpers (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from heardloop.config import Settings
from heardloop.updates import (
    UpdateCheckState,
    classify_request_error,
    fetch_latest_release,
    filter_latest_release,
    is_newer,
    merge_last_ok,
    parse_semver,
    public_update_field,
    public_update_status,
)


def test_parse_and_compare_semver() -> None:
    assert parse_semver("0.9.0") == (0, 9, 0)
    assert parse_semver("v1.2.3") == (1, 2, 3)
    assert parse_semver("1.2") is None
    assert is_newer("0.9.1", "0.9.0")
    assert not is_newer("0.9.0", "0.9.0")
    assert not is_newer("0.8.9", "0.9.0")


def test_filter_ignores_draft_prerelease_and_bad_tag() -> None:
    repo = "HirezRa/heardloop"
    base = {
        "tag_name": "v0.9.1",
        "html_url": f"https://github.com/{repo}/releases/tag/v0.9.1",
        "published_at": "2026-10-18T12:00:00Z",
        "body": "notes",
        "draft": False,
        "prerelease": False,
    }
    assert filter_latest_release(base, repo) is not None
    assert filter_latest_release({**base, "draft": True}, repo) is None
    assert filter_latest_release({**base, "prerelease": True}, repo) is None
    assert filter_latest_release({**base, "tag_name": "v0.9.1-rc1"}, repo) is None
    assert filter_latest_release({**base, "tag_name": "nightly"}, repo) is None
    assert filter_latest_release({**base, "html_url": "https://evil.example/x"}, repo) is None


def _settings(monkeypatch: pytest.MonkeyPatch, **kw: str) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u@127.0.0.1:1/none")
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", "A" * 43 + "=")
    monkeypatch.setenv("UPDATE_CHECK", "true")
    monkeypatch.setenv("UPDATE_CHECK_REPO", "HirezRa/heardloop")
    for k, v in kw.items():
        monkeypatch.setenv(k, v)
    from heardloop.config import get_settings

    get_settings.cache_clear()
    return get_settings()


@pytest.mark.asyncio
async def test_fetch_oversized_and_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    now = datetime(2026, 10, 18, tzinfo=UTC)

    async def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (256 * 1024 + 1))

    transport = httpx.MockTransport(oversized)
    async with httpx.AsyncClient(transport=transport) as http:
        state = await fetch_latest_release(http, settings, now=now)
    assert state.error == "oversized"

    async def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json{{{")

    transport = httpx.MockTransport(garbage)
    async with httpx.AsyncClient(transport=transport) as http:
        state = await fetch_latest_release(http, settings, now=now)
    assert state.error == "bad_json"


@pytest.mark.asyncio
async def test_fetch_private_repo_404(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    now = datetime(2026, 10, 18, tzinfo=UTC)

    async def not_found(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(not_found)
    async with httpx.AsyncClient(transport=transport) as http:
        state = await fetch_latest_release(http, settings, now=now)
    assert state.error == "not_found"


@pytest.mark.asyncio
async def test_fetch_accepts_latest_release(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    now = datetime(2026, 10, 18, tzinfo=UTC)
    body = {
        "tag_name": "v0.9.1",
        "html_url": "https://github.com/HirezRa/heardloop/releases/tag/v0.9.1",
        "published_at": "2026-10-18T12:00:00Z",
        "body": "hello\nhttps://example.com/more",
        "draft": False,
        "prerelease": False,
    }

    async def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(body).encode())

    transport = httpx.MockTransport(ok)
    async with httpx.AsyncClient(transport=transport) as http:
        state = await fetch_latest_release(http, settings, now=now)
    assert state.error is None
    assert state.latest == "0.9.1"
    assert state.notes and "hello" in state.notes


def test_public_update_field_only_when_newer() -> None:
    state = UpdateCheckState(
        checked_at="2026-10-18T00:00:00+00:00",
        latest="0.9.1",
        url="https://github.com/HirezRa/heardloop/releases/tag/v0.9.1",
        notes="n",
    )
    assert public_update_field(state, "0.9.0")["available"] is True
    assert public_update_field(state, "0.9.1") is None
    assert public_update_field(UpdateCheckState(checked_at="t", error="not_found"), "0.9.0") is None


def test_error_keeps_last_ok_and_banner() -> None:
    ok = UpdateCheckState(
        checked_at="2026-10-18T00:00:00+00:00",
        latest="0.9.5",
        url="https://github.com/HirezRa/heardloop/releases/tag/v0.9.5",
        notes="ok",
    )
    ok = merge_last_ok(None, ok)
    err = merge_last_ok(ok, UpdateCheckState(checked_at="2026-10-19T00:00:00+00:00", error="tls"))
    assert err.error == "tls"
    assert err.last_ok_latest == "0.9.5"
    assert public_update_field(err, "0.9.4")["latest"] == "0.9.5"
    status = public_update_status(err, "0.9.4", enabled=True)
    assert status["status"] == "error"
    assert status["error"] == "tls"
    assert status["last_ok_latest"] == "0.9.5"
    assert "update" in status


def test_current_only_after_success() -> None:
    current = UpdateCheckState(
        checked_at="t",
        latest="0.9.4",
        url="https://github.com/HirezRa/heardloop/releases/tag/v0.9.4",
    )
    assert public_update_status(current, "0.9.4", enabled=True)["status"] == "current"
    assert public_update_status(None, "0.9.4", enabled=True)["status"] == "never"
    assert public_update_status(None, "0.9.4", enabled=False)["status"] == "disabled"
    failed = UpdateCheckState(checked_at="t", error="network")
    assert public_update_status(failed, "0.9.4", enabled=True)["status"] == "error"


def test_classify_tls_from_ssl_cause() -> None:
    class FakeSSL(Exception):
        pass

    exc = httpx.ConnectError("connect failed")
    exc.__cause__ = FakeSSL("CERTIFICATE_VERIFY_FAILED")
    assert classify_request_error(exc) == "tls"
    assert classify_request_error(httpx.ConnectTimeout("t")) == "timeout"
    assert classify_request_error(httpx.ConnectError("refused")) == "network"


def test_update_check_token_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, UPDATE_CHECK_TOKEN="ghp_test_token_value")  # noqa: S106
    assert isinstance(s.update_check_token, SecretStr)
