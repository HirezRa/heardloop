"""Audiences (FR-28): the market a post is written for, with its own time zone and weekend.

Reports compare a post with other posts of the same audience, in that audience's own time zone, instead of
mixing a post for readers in one region with a post for readers eight hours away.
"""

import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from heardloop.models import Audience

# Weekday numbers follow datetime.weekday(): 0 = Monday ... 6 = Sunday.
FRI_SAT = [4, 5]
SAT_SUN = [5, 6]

# Zones whose working week runs Sunday to Thursday. Everything else defaults to a Saturday + Sunday weekend.
_FRI_SAT_ZONES = frozenset(
    {
        "Asia/Jerusalem",
        "Asia/Tel_Aviv",
        "Israel",
        "Asia/Riyadh",
        "Asia/Kuwait",
        "Asia/Bahrain",
        "Asia/Qatar",
        "Asia/Dubai",
        "Asia/Muscat",
        "Asia/Baghdad",
        "Asia/Amman",
        "Africa/Cairo",
        "Asia/Aden",
        "Africa/Tripoli",
        "Asia/Dhaka",
    }
)


def weekend_for_zone(time_zone: str) -> list[int]:
    """A sensible default weekend for a zone; owners can still change it per audience."""
    return list(FRI_SAT) if time_zone in _FRI_SAT_ZONES else list(SAT_SUN)


@dataclass(frozen=True)
class Preset:
    code: str
    name: str
    time_zone: str
    weekend_days: list[int]


# Offered in Settings as one-click starting points. Names are English product copy; the UI translates.
PRESETS: tuple[Preset, ...] = (
    Preset("IL", "Israel", "Asia/Jerusalem", FRI_SAT),
    Preset("US-E", "US East Coast", "America/New_York", SAT_SUN),
    Preset("US-W", "US West Coast", "America/Los_Angeles", SAT_SUN),
    Preset("UK", "United Kingdom", "Europe/London", SAT_SUN),
    Preset("EU", "Central Europe", "Europe/Berlin", SAT_SUN),
    Preset("IN", "India", "Asia/Kolkata", SAT_SUN),
    Preset("GLOBAL", "Global (UTC)", "UTC", SAT_SUN),
)


@dataclass(frozen=True)
class Resolved:
    """The audience a post is reported under, with the zone its publish time is read in."""

    audience: Audience | None
    tz: ZoneInfo

    @property
    def key(self) -> str:
        return str(self.audience.id) if self.audience else "none"

    @property
    def weekend_days(self) -> list[int]:
        return list(self.audience.weekend_days) if self.audience else []


class AudienceMap:
    """All audiences of a workspace, loaded once per request, with the fallback zone for posts without one."""

    def __init__(self, audiences: list[Audience], workspace_tz: ZoneInfo) -> None:
        self.by_id: dict[uuid.UUID, Audience] = {a.id: a for a in audiences}
        self.workspace_tz = workspace_tz
        self._zones: dict[uuid.UUID, ZoneInfo] = {}

    @classmethod
    async def load(cls, db: AsyncSession, workspace_id: uuid.UUID, workspace_tz: ZoneInfo) -> "AudienceMap":
        rows = await db.execute(
            select(Audience)
            .where(Audience.workspace_id == workspace_id)
            .order_by(Audience.position, Audience.created_at)
        )
        return cls(list(rows.scalars()), workspace_tz)

    @property
    def default(self) -> Audience | None:
        return next((a for a in self.by_id.values() if a.is_default), None)

    def zone(self, audience: Audience) -> ZoneInfo:
        zone = self._zones.get(audience.id)
        if zone is None:
            try:
                zone = ZoneInfo(audience.time_zone)
            except (KeyError, ValueError):  # a zone removed from tzdata after it was saved
                zone = self.workspace_tz
            self._zones[audience.id] = zone
        return zone

    def resolve(self, post_audience_id: uuid.UUID | None, campaign_audience_id: uuid.UUID | None) -> Resolved:
        """The post's own audience wins; otherwise its campaign's; otherwise none (workspace zone)."""
        for candidate in (post_audience_id, campaign_audience_id):
            audience = self.by_id.get(candidate) if candidate else None
            if audience is not None:
                return Resolved(audience, self.zone(audience))
        return Resolved(None, self.workspace_tz)
