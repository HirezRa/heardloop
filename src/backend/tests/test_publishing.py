from datetime import timedelta

import pytest

from heardloop.publishing.base import MAX_ATTEMPTS, NotConfiguredPublisher, PublishResult, delay_after


def test_retry_delays() -> None:
    assert [delay_after(n) for n in (1, 2, 3, 9)] == [
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(minutes=15),
    ]
    assert MAX_ATTEMPTS == 3


def test_delay_rejects_zero() -> None:
    with pytest.raises(ValueError):
        delay_after(0)


def test_result_factories() -> None:
    ok = PublishResult.published("urn:li:share:1")
    assert ok.success and not ok.transient and ok.post_urn == "urn:li:share:1"
    assert PublishResult.transient_error("x", "y").transient
    assert not PublishResult.permanent_error("x", "y").transient


async def test_not_configured_publisher_fails_permanently() -> None:
    result = await NotConfiguredPublisher().publish(None)  # type: ignore[arg-type]
    assert not result.success and not result.transient
    assert result.error_code == "linkedin_not_configured"
