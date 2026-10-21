"""Request/response models. Input is validated here; nothing else trusts raw request data."""

import re
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from heardloop.models import CampaignStatus, WorkspaceRole
from heardloop.security.passwords import MAX_LENGTH

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,63}$")


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONTROL_CHARS_KEEP_NEWLINE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]")


def _reject_controls(s: str, *, allow_newline: bool = False) -> str:
    """C0/C1 control characters have no place in names or notes (log and header injection, invisible text).

    Multi-line fields may contain a plain line feed; a carriage return is still refused.
    """
    pattern = _CONTROL_CHARS_KEEP_NEWLINE if allow_newline else _CONTROL_CHARS
    if pattern.search(s):
        raise ValueError("must not contain control characters")
    return s


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 320 or not _EMAIL.match(value):
        raise ValueError("invalid e-mail address")
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class LoginRequest(Strict):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _email(v)


class ChangePasswordRequest(Strict):
    current_password: str = Field(min_length=1, max_length=MAX_LENGTH)
    new_password: str = Field(min_length=1, max_length=MAX_LENGTH)


class MembershipOut(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str
    role: WorkspaceRole


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    locale: str


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class UiPrefs(Strict):
    """Interface preferences, per user. Everything here is cosmetic; secrets and server settings stay in .env.

    `locale` null = the server default (UI_LANGUAGE). Unknown values are rejected, not guessed.
    `dismissed_update` is the latest version the user dismissed on the update banner (e.g. "0.9.1").
    """

    locale: Literal["en", "he"] | None = None
    date_format: Literal["dmy", "mdy", "ymd"] = "dmy"
    time_format: Literal["24h", "12h"] = "24h"
    font_size: Literal["s", "m", "l", "xl"] = "m"
    theme: Literal["system", "light", "dark"] = "system"
    accent: str = "#0a66c2"
    dismissed_update: str | None = None

    @field_validator("accent")
    @classmethod
    def _check_accent(cls, v: str) -> str:
        if not _HEX_COLOR.match(v):
            raise ValueError("accent must look like #1a2b3c")
        return v.lower()

    @field_validator("dismissed_update")
    @classmethod
    def _check_dismissed(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not re.fullmatch(r"\d+\.\d+\.\d+", v):
            raise ValueError("dismissed_update must look like 1.2.3")
        return v


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    csrf_token: str
    mfa_enabled: bool
    ui_prefs: UiPrefs


class MfaChallenge(BaseModel):
    """Login step 1 passed; the client must POST the code to /auth/mfa/verify."""

    mfa_required: bool = True
    csrf_token: str


class MfaCodeRequest(Strict):
    code: str = Field(min_length=6, max_length=16)  # 6-digit TOTP or a recovery code


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class MfaPasswordRequest(Strict):
    password: str = Field(min_length=1, max_length=MAX_LENGTH)


class MfaDisableRequest(Strict):
    password: str = Field(min_length=1, max_length=MAX_LENGTH)
    code: str = Field(min_length=6, max_length=16)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: WorkspaceRole


class AddMemberRequest(Strict):
    email: str = Field(max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    role: WorkspaceRole
    # Used only when the e-mail has no account yet. The new user should change it after first login.
    initial_password: str | None = Field(default=None, max_length=MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _email(v)

    @field_validator("display_name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = _reject_controls(v).strip()
        if not v:
            raise ValueError("display_name is required")
        return v


class UpdateMemberRequest(Strict):
    role: WorkspaceRole


class SessionInfo(BaseModel):
    expires_at: datetime


class WorkspaceSettingsOut(BaseModel):
    workspace_id: uuid.UUID
    name: str
    time_zone: str
    require_approval: bool
    weekly_digest: bool
    can_edit: bool


class BackupStatusOut(BaseModel):
    """Newest backup, from the backup service's manifest. Status and sizes only: no file names or paths.

    `configured` is false when there is no manifest (no backup has run yet, or the status path is empty).
    `status` is `ok`, `failed`, or `unreadable` (a manifest exists but cannot be understood).
    `stale` is true when the last good backup is 36 hours old or more.
    """

    configured: bool
    status: Literal["ok", "failed", "unreadable"] | None = None
    stale: bool = False
    finished_at: datetime | None = None
    age_hours: float | None = None
    db_bytes: int = 0
    media_bytes: int = 0
    encrypted: bool = False
    error: str | None = None


class WorkspaceSettingsUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    time_zone: str | None = Field(default=None, min_length=1, max_length=64)
    require_approval: bool | None = None
    weekly_digest: bool | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = _reject_controls(v).strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("time_zone")
    @classmethod
    def _check_tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in _time_zones():
            raise ValueError("unknown time zone")
        return v


def _time_zones() -> frozenset[str]:
    from heardloop.timeutil import available_time_zones

    return available_time_zones()


class AuditEntryOut(BaseModel):
    id: int
    at: datetime
    action: str
    entity: str
    entity_id: str | None
    actor_id: uuid.UUID | None
    actor_name: str | None
    ip_address: str | None
    changes: dict[str, object] | None


class AuditPurgeRequest(Strict):
    """Owner clears the workspace's activity log, all of it or only entries before a date."""

    password: str = Field(min_length=1, max_length=MAX_LENGTH)
    before: datetime | None = None  # null = everything

    @field_validator("before")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("before must include a time zone offset")
        return v


class AuditPurgeOut(BaseModel):
    deleted: int


class AuditPage(BaseModel):
    items: list[AuditEntryOut]
    next_before_id: int | None


# ---- Posts ----

POST_MAX_CHARS = 3000  # LinkedIn commentary limit


def _post_body(v: str) -> str:
    if not v.strip():
        raise ValueError("body must not be empty")
    if len(v) > POST_MAX_CHARS:
        raise ValueError(f"body must be at most {POST_MAX_CHARS} characters")
    return v


class LinkCard(Strict):
    """A LinkedIn 'article' attachment. The post's image, if any, becomes the card thumbnail."""

    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=300)

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        from heardloop.linkpreview import LinkPreviewError, validate_url

        try:
            return validate_url(v)
        except LinkPreviewError as exc:
            raise ValueError("url must be a full http(s) address without credentials") from exc

    @field_validator("title", "description")
    @classmethod
    def _one_line(cls, v: str | None, info: ValidationInfo) -> str | None:
        cleaned = " ".join(v.split()) if v else v
        if info.field_name == "title" and not cleaned:
            raise ValueError("title is required")
        return cleaned or None


class PostCreate(Strict):
    account_id: uuid.UUID
    body: str = Field(max_length=POST_MAX_CHARS)
    campaign_id: uuid.UUID | None = None
    media_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1)  # one image per post for now
    link: LinkCard | None = None

    @field_validator("body")
    @classmethod
    def _check_body(cls, v: str) -> str:
        return _post_body(v)


class PostUpdate(Strict):
    body: str | None = Field(default=None, max_length=POST_MAX_CHARS)
    account_id: uuid.UUID | None = None
    # Send `"campaign_id": null` to remove the post from its campaign; leave the field out to keep it.
    campaign_id: uuid.UUID | None = None
    # Same rule: null removes the link card, leaving it out keeps the current one.
    link: LinkCard | None = None
    media_ids: list[uuid.UUID] | None = Field(default=None, max_length=1)  # [] removes the image

    # `body` may be empty here (max length is enforced by the field): empty text is valid only for posts
    # created by an import, and `update_post` answers `body_required` for every other post.


class ScheduleRequest(Strict):
    """Either an absolute time with offset, or a wall-clock time in the workspace time zone."""

    scheduled_at: datetime | None = None
    local_time: datetime | None = None  # e.g. 2026-10-16T08:00, interpreted in the workspace time zone

    @field_validator("scheduled_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("scheduled_at must include a time zone offset")
        return v

    @field_validator("local_time")
    @classmethod
    def _naive(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is not None:
            raise ValueError("local_time must not include an offset")
        return v


class RejectRequest(Strict):
    comment: str = Field(min_length=1, max_length=2000)

    @field_validator("comment")
    @classmethod
    def _plain(cls, v: str) -> str:
        return _reject_controls(v, allow_newline=True)


class MediaRef(BaseModel):
    id: uuid.UUID
    url: str
    mime_type: str | None = None


# Letters/digits/underscore plus Hebrew points and generic combining marks (the editor uses \p{M}).
_HASHTAG_WORD = re.compile(r"^[^\W_][\w\u0591-\u05C7\u0300-\u036F]{0,63}$", re.UNICODE)


class HashtagIn(Strict):
    tag: str = Field(min_length=1, max_length=65)

    @field_validator("tag")
    @classmethod
    def _check_tag(cls, v: str) -> str:
        v = _reject_controls(v).strip().lstrip("#").strip()
        if not _HASHTAG_WORD.match(v):
            raise ValueError("a hashtag is one word: letters, digits or '_', up to 64 characters")
        return v


class HashtagsOut(BaseModel):
    saved: list[str]
    recent: list[str]
    recommended_max: int = 5


class LinkPreviewRequest(Strict):
    url: str = Field(min_length=1, max_length=2048)


class LinkPreviewOut(BaseModel):
    url: str
    title: str | None
    description: str | None
    image_url: str | None
    site_name: str | None


class PostOut(BaseModel):
    id: uuid.UUID
    status: str
    account_id: uuid.UUID
    account_name: str
    account_needs_reconnect_reason: str | None = None
    campaign_id: uuid.UUID | None
    body: str
    media: list[MediaRef]
    link: LinkCard | None
    version_count: int
    scheduled_at: datetime | None
    published_at: datetime | None
    linkedin_post_urn: str | None
    linkedin_url: str | None
    external_source: str | None = None  # "linkedin_export" for posts created by an analytics import
    last_error: str | None
    last_review_comment: str | None
    created_at: datetime
    updated_at: datetime


class PostVersionOut(BaseModel):
    """One historical save of a post. At most 200 newest are listed; older rows stay in the database."""

    id: uuid.UUID
    created_at: datetime
    author_id: uuid.UUID
    author_name: str
    body: str
    media_ids: list[uuid.UUID]
    link: LinkCard | None
    is_current: bool
    approved: bool


class RestoreOut(PostOut):
    warnings: list[str] = Field(default_factory=list)


# ---- Campaigns (FR-06, FR-07) ----

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_TAG = re.compile(r"^[\w-]{1,64}$")


def _clean_tags(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    out: list[str] = []
    for tag in v:
        tag = tag.strip().lstrip("#")
        if not _TAG.match(tag):
            raise ValueError("tags: letters, digits, '_' or '-', up to 64 characters")
        if tag.lower() not in (t.lower() for t in out):
            out.append(tag)
    return out


class _CampaignFields(Strict):
    goal: str | None = Field(default=None, max_length=1000)
    audience: str | None = Field(default=None, max_length=1000)
    start_date: date | None = None
    end_date: date | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    color: str | None = None

    @field_validator("goal", "audience")
    @classmethod
    def _check_text(cls, v: str | None) -> str | None:
        return None if v is None else _reject_controls(v, allow_newline=True)

    @field_validator("color")
    @classmethod
    def _check_color(cls, v: str | None) -> str | None:
        if v is not None and not _COLOR.match(v):
            raise ValueError("color must look like #1a2b3c")
        return v.lower() if v else v

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str] | None) -> list[str] | None:
        return _clean_tags(v)


class CampaignCreate(_CampaignFields):
    name: str = Field(min_length=1, max_length=200)
    status: CampaignStatus = CampaignStatus.PLANNED

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _reject_controls(v).strip():
            raise ValueError("name must not be empty")
        return v.strip()


class CampaignUpdate(_CampaignFields):
    """Only the fields that are sent change. Send null for goal/audience/dates to clear them."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: CampaignStatus | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        if v is not None and not _reject_controls(v).strip():
            raise ValueError("name must not be empty")
        return v.strip() if v else v


class CampaignOut(BaseModel):
    id: uuid.UUID
    name: str
    goal: str | None
    audience: str | None
    start_date: date | None
    end_date: date | None
    tags: list[str]
    color: str
    status: str
    post_counts: dict[str, int]  # status -> number of posts
    created_at: datetime


# ---- Calendar (FR-16) ----


class CalendarItem(BaseModel):
    post_id: uuid.UUID
    status: str
    at: datetime  # scheduled time, or publish time for published posts (UTC)
    local_time: str  # same moment as wall-clock time in the workspace time zone, e.g. 2026-10-16T08:00
    snippet: str
    account_name: str
    campaign_id: uuid.UUID | None
    campaign_name: str | None
    color: str | None
    can_reschedule: bool


class CalendarOut(BaseModel):
    time_zone: str
    start: date
    end: date
    items: list[CalendarItem]
    truncated: bool = False  # more than MAX_ITEMS posts in the range


# ---- Metrics and reports (FR-21 to FR-24) ----

METRIC_FIELDS = ("impressions", "reactions", "comments", "reposts", "clicks", "saves", "followers")


class MetricsIn(Strict):
    """A measurement of a published post as read on LinkedIn. Unknown numbers are simply left out."""

    captured_at: datetime | None = None  # default: now
    impressions: int | None = Field(default=None, ge=0, le=10**12)
    reactions: int | None = Field(default=None, ge=0, le=10**12)
    comments: int | None = Field(default=None, ge=0, le=10**12)
    reposts: int | None = Field(default=None, ge=0, le=10**12)
    clicks: int | None = Field(default=None, ge=0, le=10**12)
    saves: int | None = Field(default=None, ge=0, le=10**12)
    followers: int | None = Field(default=None, ge=0, le=10**12)
    note: str | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def _check_note(cls, v: str | None) -> str | None:
        return None if v is None else _reject_controls(v, allow_newline=True)

    @field_validator("captured_at")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v.utcoffset() is None):
            raise ValueError("captured_at must include a time zone offset")
        return v


class DemographicOut(BaseModel):
    category: str
    value: str
    share: float  # 0-1


class TopFieldsOut(BaseModel):
    job_title: str | None = None
    location: str | None = None
    industry: str | None = None


class MetricsOut(BaseModel):
    id: uuid.UUID
    captured_at: datetime
    impressions: int | None
    reactions: int | None
    comments: int | None
    reposts: int | None
    clicks: int | None
    saves: int | None
    followers: int | None
    # Only filled by LinkedIn exports.
    members_reached: int | None = None
    profile_viewers: int | None = None
    followers_gained: int | None = None
    sends: int | None = None
    demographics: list[DemographicOut] | None = None
    top: TopFieldsOut | None = None
    note: str | None
    source: str
    recorded_by_name: str | None


class ImportResultOut(MetricsOut):
    """Result of importing one LinkedIn export: the stored measurement plus the post it belongs to."""

    post_id: uuid.UUID
    created: bool  # true when the post itself was created by the import


class ReportPost(BaseModel):
    post_id: uuid.UUID
    published_at: datetime
    snippet: str
    campaign_id: uuid.UUID | None
    campaign_name: str | None
    media_kind: str  # none | image | video | link
    impressions: int | None
    reactions: int | None
    comments: int | None
    reposts: int | None
    clicks: int | None
    saves: int | None
    members_reached: int | None = None  # from LinkedIn exports
    followers_gained: int | None = None  # from LinkedIn exports
    engagement: int  # reactions + comments + reposts + saves (known values)
    engagement_rate: float | None  # engagement / impressions
    measured_at: datetime | None


class ReportBucket(BaseModel):
    key: str  # a date, a campaign name, a weekday number, an hour, a media kind
    label: str | None = None
    posts: int
    measured: int
    impressions: int
    engagement: int
    members_reached: int = 0  # sum of the latest measurements that have it (exports only)
    followers_gained: int = 0
    avg_impressions: float | None
    avg_engagement_rate: float | None


class ReportOut(BaseModel):
    start: date
    end: date
    time_zone: str
    totals: ReportBucket
    trend: list[ReportBucket]  # per week
    by_campaign: list[ReportBucket]
    by_weekday: list[ReportBucket]  # 0 = Monday ... 6 = Sunday, in the workspace time zone
    by_hour: list[ReportBucket]
    by_media: list[ReportBucket]
    top_posts: list[ReportPost]
    unmeasured: list[ReportPost]  # published, no metrics yet


# ---- Post templates (FR-12) ----


def _template_body(v: str) -> str:
    """Like a post body, but empty is allowed (a template may be a link card only)."""
    if len(v) > POST_MAX_CHARS:
        raise ValueError(f"body must be at most {POST_MAX_CHARS} characters")
    return v


class TemplateCreate(Strict):
    name: str = Field(min_length=1, max_length=80)
    body: str = Field(default="", max_length=POST_MAX_CHARS)
    link: LinkCard | None = None
    campaign_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _reject_controls(v).strip():
            raise ValueError("name must not be empty")
        return v.strip()

    @field_validator("body")
    @classmethod
    def _check_body(cls, v: str) -> str:
        return _template_body(v)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str]) -> list[str]:
        cleaned = _clean_tags(v)
        return cleaned or []


class TemplateUpdate(Strict):
    """Only sent fields change. Send null for link/campaign_id to clear; leave the field out to keep it."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    body: str | None = Field(default=None, max_length=POST_MAX_CHARS)
    link: LinkCard | None = None
    campaign_id: uuid.UUID | None = None
    tags: list[str] | None = Field(default=None, max_length=10)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str | None) -> str | None:
        if v is not None and not _reject_controls(v).strip():
            raise ValueError("name must not be empty")
        return v.strip() if v else v

    @field_validator("body")
    @classmethod
    def _check_body(cls, v: str | None) -> str | None:
        return None if v is None else _template_body(v)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str] | None) -> list[str] | None:
        return _clean_tags(v)


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    body: str
    link: LinkCard | None
    campaign_id: uuid.UUID | None
    campaign_name: str | None
    tags: list[str]
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
    use_count: int
    archived_at: datetime | None


class TemplateUseOut(BaseModel):
    """Filled payload for a new draft. No post is created; the client still saves."""

    body: str
    link: LinkCard | None
    campaign_id: uuid.UUID | None
    hashtags_in_body: list[str]
    placeholders_left: list[str]


class SaveAsTemplateRequest(Strict):
    name: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _reject_controls(v).strip():
            raise ValueError("name must not be empty")
        return v.strip()

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, v: list[str]) -> list[str]:
        cleaned = _clean_tags(v)
        return cleaned or []
