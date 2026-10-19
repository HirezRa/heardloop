"""Database model (SQLAlchemy 2). All timestamps are timezone-aware UTC."""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from heardloop.domain.workflow import PostStatus, ensure_transition


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    APPROVER = "approver"
    VIEWER = "viewer"


class LinkedInAccountType(StrEnum):
    PERSON = "person"
    ORGANIZATION = "organization"


class LinkedInAccountStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class CampaignStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class MetricSource(StrEnum):
    API = "api"
    MANUAL = "manual"
    LINKEDIN_EXPORT = "linkedin_export"  # numbers read from a LinkedIn "Post analytics" .xlsx export


def _enum(enum_cls: type[StrEnum]) -> SqlEnum:
    # Stored as plain text (no PG enum type) so adding a value never needs an ALTER TYPE.
    return SqlEnum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


def _ts(nullable: bool = False) -> Any:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lower-case
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(16), default="he-IL")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = _ts(nullable=True)
    password_changed_at: Mapped[datetime | None] = _ts(nullable=True)
    # TOTP secret, encrypted (TokenProtector, context "user-mfa:<id>").
    # Secret set but mfa_enabled_at empty = enrolment pending.
    mfa_enabled_at: Mapped[datetime | None] = _ts(nullable=True)
    mfa_last_used_step: Mapped[int | None] = mapped_column(BigInteger)
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Per-user interface preferences (language, date format, font size, theme). Validated by UiPrefs.
    ui_prefs: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = _ts()
    # Set when an owner deactivated and anonymised the member; the row stays so history joins still resolve.
    deactivated_at: Mapped[datetime | None] = _ts(nullable=True)


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = _ts(nullable=True)


class UserSession(Base):
    """Server-side login session. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    # True after a correct password but before the second factor.
    # Such a session can only call /auth/mfa/verify.
    mfa_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = _ts()
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(256))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    time_zone: Mapped[str] = mapped_column(String(64), default="UTC")
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    weekly_digest: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_digest_week: Mapped[str | None] = mapped_column(String(10))  # ISO week already sent, e.g. 2026-W42
    # Outgoing webhook (e.g. n8n). The signing secret is encrypted like the LinkedIn tokens.
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text)
    webhook_include_text: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # When owners were last told about a stale or failed backup (at most one alert per 24 hours).
    last_backup_alert_at: Mapped[datetime | None] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(_enum(WorkspaceRole))


class LinkedInAccount(Base):
    __tablename__ = "linkedin_accounts"
    __table_args__ = (UniqueConstraint("workspace_id", "urn"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    type: Mapped[LinkedInAccountType] = mapped_column(_enum(LinkedInAccountType))
    urn: Mapped[str] = mapped_column(String(128))  # urn:li:person:{id} / urn:li:organization:{id}
    display_name: Mapped[str] = mapped_column(String(200))
    # Encrypted with TokenProtector (context = account id). Never log or return to clients.
    access_token_encrypted: Mapped[str] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    access_token_expires_at: Mapped[datetime] = _ts()
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    status: Mapped[LinkedInAccountStatus] = mapped_column(
        _enum(LinkedInAccountStatus), default=LinkedInAccountStatus.ACTIVE
    )
    # Set when publish fails because the token cannot be used; cleared on reconnect/disconnect.
    needs_reconnect_reason: Mapped[str | None] = mapped_column(String(32))
    needs_reconnect_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime] = _ts()


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (Index("ix_campaigns_workspace_status", "workspace_id", "status"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str | None] = mapped_column(String(1000))
    audience: Mapped[str | None] = mapped_column(String(1000))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    color: Mapped[str] = mapped_column(String(16), default="#2563eb")
    status: Mapped[CampaignStatus] = mapped_column(_enum(CampaignStatus), default=CampaignStatus.PLANNED)
    created_at: Mapped[datetime] = _ts()


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_status_scheduled_at", "status", "scheduled_at"),
        Index("ix_posts_workspace_scheduled_at", "workspace_id", "scheduled_at"),
        # One post per LinkedIn post URN in a workspace (two concurrent imports of the same post).
        Index(
            "ux_posts_workspace_urn",
            "workspace_id",
            "linkedin_post_urn",
            unique=True,
            postgresql_where=text("linkedin_post_urn IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("linkedin_accounts.id", ondelete="RESTRICT"))
    status: Mapped[PostStatus] = mapped_column(_enum(PostStatus), default=PostStatus.DRAFT)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scheduled_at: Mapped[datetime | None] = _ts(nullable=True)
    published_at: Mapped[datetime | None] = _ts(nullable=True)
    linkedin_post_urn: Mapped[str | None] = mapped_column(String(128))
    # Set (to "linkedin_export") on posts that were not published through Heardloop but created by an import.
    external_source: Mapped[str | None] = mapped_column(String(32))
    # Stable per post; prevents double publishing (FR-18).
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: uuid.uuid4().hex)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    # Worker bookkeeping (see heardloop/worker/dispatcher.py).
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = _ts(nullable=True)
    locked_until: Mapped[datetime | None] = _ts(nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012  # optimistic concurrency

    def __init__(self, **kwargs: Any) -> None:
        # Column defaults apply only on INSERT; domain logic needs real values right away.
        kwargs.setdefault("status", PostStatus.DRAFT)
        kwargs.setdefault("attempt_count", 0)
        super().__init__(**kwargs)

    def transition_to(self, to_status: PostStatus, *, require_approval: bool, now: datetime) -> None:
        ensure_transition(self.status, to_status, require_approval=require_approval)
        self.status = to_status
        self.updated_at = now

    def mark_published(self, post_urn: str, now: datetime) -> None:
        if not post_urn:
            raise ValueError("post_urn is required")
        self.transition_to(PostStatus.PUBLISHED, require_approval=False, now=now)
        self.linkedin_post_urn = post_urn
        self.published_at = now
        self.last_error = None
        self.next_attempt_at = None
        self.locked_until = None

    def mark_failed(self, error: str, now: datetime) -> None:
        self.transition_to(PostStatus.FAILED, require_approval=False, now=now)
        self.last_error = error[:1000]
        self.next_attempt_at = None
        self.locked_until = None


class PostVersion(Base):
    __tablename__ = "post_versions"
    __table_args__ = (Index("ix_post_versions_post_created", "post_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(String(3000))
    media_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    # Link card (LinkedIn "article" content). The API does not scrape pages, so title/description are ours.
    # The post's image (media_ids[0]) doubles as the card thumbnail.
    link_url: Mapped[str | None] = mapped_column(String(2048))
    link_title: Mapped[str | None] = mapped_column(String(200))
    link_description: Mapped[str | None] = mapped_column(String(300))
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = _ts()


class MediaAsset(Base):
    __tablename__ = "media_assets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    storage_key: Mapped[str] = mapped_column(String(128))  # random name, never the uploaded file name
    mime_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    linkedin_asset_urn: Mapped[str | None] = mapped_column(String(128))
    # LinkedIn images belong to an owner (member URN); the cached URN is only reused for the same owner.
    linkedin_owner_urn: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _ts()


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("post_versions.id", ondelete="CASCADE"))
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decision: Mapped[ApprovalDecision] = mapped_column(_enum(ApprovalDecision))
    comment: Mapped[str | None] = mapped_column(String(2000))
    decided_at: Mapped[datetime] = _ts()


class PublishAttempt(Base):
    __tablename__ = "publish_attempts"
    __table_args__ = (UniqueConstraint("post_id", "attempt_no"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = _ts()
    finished_at: Mapped[datetime | None] = _ts(nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # sanitised, no tokens


class PostMetric(Base):
    __tablename__ = "post_metrics"
    __table_args__ = (Index("ix_post_metrics_post_captured", "post_id", "captured_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = _ts()
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    reactions: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    reposts: Mapped[int | None] = mapped_column(BigInteger)
    clicks: Mapped[int | None] = mapped_column(BigInteger)
    saves: Mapped[int | None] = mapped_column(BigInteger)
    followers: Mapped[int | None] = mapped_column(BigInteger)  # account followers on the capture day
    # Filled by LinkedIn exports only.
    members_reached: Mapped[int | None] = mapped_column(BigInteger)
    profile_viewers: Mapped[int | None] = mapped_column(BigInteger)
    followers_gained: Mapped[int | None] = mapped_column(BigInteger)  # gained from this post
    sends: Mapped[int | None] = mapped_column(BigInteger)
    demographics: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)  # [{category, value, share}]
    top: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {job_title, location, industry}
    note: Mapped[str | None] = mapped_column(String(200))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[MetricSource] = mapped_column(_enum(MetricSource))


class AuditLogEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_workspace_at", "workspace_id", "at"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    diff_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # must not contain secrets
    ip_address: Mapped[str | None] = mapped_column(String(45))
    at: Mapped[datetime] = _ts()


class AppMeta(Base):
    """Small key/value table for installation-wide facts (e.g. the encryption key fingerprint)."""

    __tablename__ = "app_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # update_check JSON may include release notes
    updated_at: Mapped[datetime] = _ts()


class Notification(Base):
    """One message for one user. In-app always; e-mail is delivered by the worker (outbox)."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index("ix_notifications_email_status", "email_status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))
    post_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # small; never secrets or post text
    created_at: Mapped[datetime] = _ts()
    read_at: Mapped[datetime | None] = _ts(nullable=True)
    email_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|skipped|failed
    email_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class WebhookDelivery(Base):
    """Outbox for the workspace webhook. Retried with backoff by the worker."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),
        Index("ix_webhook_deliveries_workspace_created", "workspace_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = _ts()
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _ts()
    sent_at: Mapped[datetime | None] = _ts(nullable=True)


class Hashtag(Base):
    """Saved hashtag library per workspace. Tags are stored without '#'."""

    __tablename__ = "hashtags"
    __table_args__ = (UniqueConstraint("workspace_id", "tag_key", name="uq_hashtags_workspace_tag"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    tag: Mapped[str] = mapped_column(String(64))  # as the user spelled it
    tag_key: Mapped[str] = mapped_column(String(64))  # casefolded, for uniqueness
    created_at: Mapped[datetime] = _ts()


class PostTemplate(Base):
    """Reusable post starting point: text with placeholders, link card, tags, optional campaign (FR-12)."""

    __tablename__ = "post_templates"
    __table_args__ = (
        Index(
            "uq_post_templates_workspace_lower_name",
            "workspace_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index("ix_post_templates_workspace_updated", "workspace_id", "updated_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(String(3000), default="")
    link_url: Mapped[str | None] = mapped_column(String(2048))
    link_title: Mapped[str | None] = mapped_column(String(200))
    link_description: Mapped[str | None] = mapped_column(String(300))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    use_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    archived_at: Mapped[datetime | None] = _ts(nullable=True)
