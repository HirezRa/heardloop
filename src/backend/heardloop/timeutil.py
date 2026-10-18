from datetime import UTC, datetime
from functools import lru_cache
from zoneinfo import available_timezones


def utcnow() -> datetime:
    """The one place the app reads the clock. Always timezone-aware UTC."""
    return datetime.now(UTC)


@lru_cache(maxsize=1)
def available_time_zones() -> frozenset[str]:
    """IANA zone names (from the tzdata package when the OS has none)."""
    return frozenset(available_timezones())
