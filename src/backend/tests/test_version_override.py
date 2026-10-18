"""HEARDLOOP_VERSION_OVERRIDE is honoured only when APP_ENV=development."""

from __future__ import annotations

import pytest

from heardloop import version as version_mod


@pytest.fixture(autouse=True)
def _clear_version_cache() -> None:
    version_mod._version_from_file.cache_clear()
    yield
    version_mod._version_from_file.cache_clear()


def test_override_ignored_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("HEARDLOOP_VERSION_OVERRIDE", "0.8.0")
    assert version_mod.app_version() != "0.8.0"


def test_override_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("HEARDLOOP_VERSION_OVERRIDE", "0.8.0")
    assert version_mod.app_version() == "0.8.0"


def test_bad_override_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("HEARDLOOP_VERSION_OVERRIDE", "not-a-version")
    assert version_mod.app_version() != "not-a-version"
