"""Backup manifest parser, health and alert decisions. No database needed."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from heardloop import backup, delivery
from heardloop.config import get_settings
from heardloop.models import Notification

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def _manifest(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "set": "heardloop-20261018T010000Z",
        "started_at": "2026-10-18T01:00:00Z",
        "finished_at": "2026-10-18T01:00:07Z",
        "status": "ok",
        "error": None,
        "files": [{"name": "heardloop-20261018T010000Z.dump", "bytes": 1234, "sha256": "ab" * 32}],
        "db_bytes": 1234,
        "media_bytes": 99,
        "encrypted": False,
        "pg_dump_version": "17.6",
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, content: object) -> str:
    path = tmp_path / "latest.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return str(path)


def test_ok_manifest_is_parsed(tmp_path: Path) -> None:
    read = backup.read_manifest(_write(tmp_path, _manifest()))
    assert read.state == "ok" and read.manifest is not None
    m = read.manifest
    assert m.status == "ok" and m.db_bytes == 1234 and m.media_bytes == 99 and m.encrypted is False
    assert m.finished_at == datetime(2026, 10, 18, 1, 0, 7, tzinfo=UTC)
    assert m.files[0].size == 1234


def test_failed_manifest_keeps_the_error_text(tmp_path: Path) -> None:
    read = backup.read_manifest(
        _write(tmp_path, _manifest(status="failed", error="pg_dump failed", files=[]))
    )
    assert read.manifest is not None
    assert read.manifest.status == "failed" and read.manifest.error == "pg_dump failed"


def test_unknown_fields_are_ignored(tmp_path: Path) -> None:
    data = _manifest(future_field={"a": 1}, files=[{"name": "x", "bytes": 1, "sha256": "ff", "extra": True}])
    assert backup.read_manifest(_write(tmp_path, data)).state == "ok"


def test_missing_file_and_disabled_path(tmp_path: Path) -> None:
    assert backup.read_manifest(str(tmp_path / "nope.json")).state == "missing"
    assert backup.read_manifest("").state == "disabled"


def test_oversized_file_is_unreadable(tmp_path: Path) -> None:
    padded = _write(tmp_path, json.dumps(_manifest(error="x" * (backup.MAX_MANIFEST_BYTES + 10))))
    assert backup.read_manifest(padded).state == "unreadable"


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "",
        "[]",
        '{"status": "ok"}',  # required fields missing
        json.dumps(_manifest(status="great")),
        json.dumps(_manifest(finished_at="yesterday")),
        json.dumps(_manifest(db_bytes=-5)),
        "\x00\x01\x02",
    ],
)
def test_garbage_is_unreadable(tmp_path: Path, content: str) -> None:
    assert backup.read_manifest(_write(tmp_path, content)).state == "unreadable"


def test_directory_instead_of_file_is_unreadable(tmp_path: Path) -> None:
    assert backup.read_manifest(str(tmp_path)).state == "unreadable"


def test_naive_time_is_read_as_utc_and_error_is_cleaned(tmp_path: Path) -> None:
    raw = _manifest(
        finished_at="2026-10-18T01:00:07", status="failed", error="bad\x1b[31m\nline " + "y" * 500
    )
    m = backup.read_manifest(_write(tmp_path, raw)).manifest
    assert m is not None and m.finished_at.tzinfo is not None
    assert m.error is not None and len(m.error) <= backup.MAX_ERROR_LENGTH
    assert "\x1b" not in m.error and "\n" not in m.error


def test_model_rejects_a_bad_manifest_directly() -> None:
    with pytest.raises(ValidationError):
        backup.BackupManifest.model_validate({"set": "x", "status": "ok"})


def _read(tmp_path: Path, **overrides: object) -> backup.ManifestRead:
    return backup.read_manifest(_write(tmp_path, _manifest(**overrides)))


def test_health_thresholds(tmp_path: Path) -> None:
    def finished(hours: float) -> str:
        return (NOW - timedelta(hours=hours)).isoformat()

    assert backup.health(_read(tmp_path, finished_at=finished(1)), NOW) == "ok"
    assert backup.health(_read(tmp_path, finished_at=finished(35.9)), NOW) == "ok"
    assert backup.health(_read(tmp_path, finished_at=finished(36)), NOW) == "stale"
    assert backup.health(_read(tmp_path, finished_at=finished(1), status="failed"), NOW) == "failed"
    assert backup.health(backup.ManifestRead("missing"), NOW) == "missing"
    assert backup.health(backup.ManifestRead("disabled"), NOW) == "disabled"
    assert backup.health(backup.ManifestRead("unreadable"), NOW) == "unreadable"


def test_age_hours_never_negative(tmp_path: Path) -> None:
    m = _read(tmp_path, finished_at=(NOW + timedelta(hours=2)).isoformat()).manifest
    assert m is not None and backup.age_hours(m, NOW) == 0.0


def test_alert_decisions(tmp_path: Path) -> None:
    long_ago = NOW - timedelta(hours=50)
    just_now = NOW - timedelta(minutes=5)
    fresh = _read(tmp_path, finished_at=(NOW - timedelta(hours=2)).isoformat())
    assert backup.alert_for(fresh, NOW, long_ago) is None

    stale = _read(tmp_path, finished_at=(NOW - timedelta(hours=40)).isoformat())
    kind, data = backup.alert_for(stale, NOW, just_now) or ("", {})
    assert kind == "backup.stale" and data["age_hours"] == 40.0 and data["reason"] == "old"

    failed = _read(tmp_path, status="failed", error="pg_dump failed")
    kind, data = backup.alert_for(failed, NOW, just_now) or ("", {})
    assert kind == "backup.failed" and data["error"] == "pg_dump failed"

    kind, data = backup.alert_for(backup.ManifestRead("unreadable"), NOW, just_now) or ("", {})
    assert kind == "backup.failed" and data["reason"] == "unreadable"

    # Missing: only after the worker has been up for 36 hours.
    assert backup.alert_for(backup.ManifestRead("missing"), NOW, just_now) is None
    kind, data = backup.alert_for(backup.ManifestRead("missing"), NOW, long_ago) or ("", {})
    assert kind == "backup.stale" and data["reason"] == "missing"

    assert backup.alert_for(backup.ManifestRead("disabled"), NOW, long_ago) is None


def test_alert_data_has_no_paths_or_file_names(tmp_path: Path) -> None:
    stale = _read(tmp_path, finished_at=(NOW - timedelta(hours=40)).isoformat())
    _, data = backup.alert_for(stale, NOW, NOW) or ("", {})
    text = json.dumps(data)
    assert "heardloop-2026" not in text and "/backups" not in text and ".dump" not in text


def test_backup_emails_are_plain_and_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user@127.0.0.1:1/none")
    monkeypatch.setenv("HEARDLOOP_TOKEN_KEY", "A" * 43 + "=")
    get_settings.cache_clear()

    def mail(kind: str, data: dict[str, object]) -> tuple[str, str]:
        n = Notification(workspace_id=uuid.uuid4(), user_id=uuid.uuid4(), kind=kind, data=data, post_id=None)
        return delivery.render_email(n, "Main\r\nBcc: someone@example.com")

    subject, body = mail(
        "backup.stale", {"reason": "old", "age_hours": 40.0, "finished_at": "2026-10-17T01:00Z"}
    )
    assert subject.startswith("Backup overdue - Main") and "\n" not in subject and "\r" not in subject
    assert "40.0 hours" in body and "section=settings" in body

    subject, body = mail("backup.stale", {"reason": "missing"})
    assert "No backup has been found" in body

    subject, body = mail("backup.failed", {"error": "pg_dump failed"})
    assert subject.startswith("Backup failed - Main") and "pg_dump failed" in body
