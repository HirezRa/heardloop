"""Settings from environment variables. Secrets are SecretStr so they never show up in logs or reprs."""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    environment: str = Field(default="production", alias="APP_ENV")
    database_url: SecretStr = Field(alias="DATABASE_URL")
    token_key: SecretStr = Field(alias="HEARDLOOP_TOKEN_KEY")

    linkedin_client_id: str = Field(default="", alias="LINKEDIN_CLIENT_ID")
    linkedin_client_secret: SecretStr = Field(default=SecretStr(""), alias="LINKEDIN_CLIENT_SECRET")
    linkedin_redirect_uri: str = Field(default="", alias="LINKEDIN_REDIRECT_URI")
    # YYYYMM. LinkedIn supports each monthly version for at least a year; bump deliberately.
    linkedin_api_version: str = Field(default="202608", alias="LINKEDIN_API_VERSION")

    media_root: Path = Field(default=Path("/data/media"), alias="MEDIA_ROOT")
    worker_poll_seconds: float = Field(default=15.0, alias="WORKER_POLL_SECONDS", gt=0)
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    # Interface language: "en" (default, left-to-right) or "he" (Hebrew, right-to-left).
    ui_language: str = Field(default="en", alias="UI_LANGUAGE")

    # Links in e-mails and webhook payloads point here.
    app_base_url: str = Field(default="http://localhost:8380", alias="APP_BASE_URL")

    # E-mail notifications (optional). Empty SMTP_HOST = e-mail off, in-app notifications only.
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT", gt=0, lt=65536)
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: SecretStr = Field(default=SecretStr(""), alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    # starttls (port 587), ssl (port 465) or none (local relay only)
    smtp_security: str = Field(default="starttls", alias="SMTP_SECURITY")

    # Newest backup manifest, written by the backup service (mounted read-only). Empty = no backup status in
    # Settings and no staleness alerts.
    backup_manifest_path: str = Field(default="/backups/latest.json", alias="BACKUP_MANIFEST_PATH")

    # Optional check for a newer GitHub release (worker every 12 h). Host is fixed to api.github.com.
    update_check: bool = Field(default=True, alias="UPDATE_CHECK")
    update_check_repo: str = Field(default="HirezRa/heardloop", alias="UPDATE_CHECK_REPO")
    update_check_token: SecretStr = Field(default=SecretStr(""), alias="UPDATE_CHECK_TOKEN")

    # Sessions. Browsers accept Secure cookies on http://localhost, so keep this on.
    cookie_secure: bool = Field(default=True, alias="COOKIE_SECURE")
    session_idle_hours: int = Field(default=12, alias="SESSION_IDLE_HOURS", gt=0)
    session_absolute_days: int = Field(default=7, alias="SESSION_ABSOLUTE_DAYS", gt=0)

    @field_validator("backup_manifest_path", mode="before")
    @classmethod
    def _backup_manifest_path(cls, v: object) -> object:
        return "" if v is None else str(v).strip()

    @field_validator("update_check", mode="before")
    @classmethod
    def _update_check(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and (not v.strip() or v.strip().startswith("<"))):
            return True
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @field_validator("update_check_repo", mode="before")
    @classmethod
    def _update_check_repo(cls, v: object) -> object:
        value = "" if v is None else str(v).strip()
        if not value or value.startswith("<"):
            return "HirezRa/heardloop"
        if not re.fullmatch(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", value):
            raise ValueError("UPDATE_CHECK_REPO must look like owner/repo")
        return value

    @field_validator("update_check_token", mode="before")
    @classmethod
    def _update_check_token(cls, v: object) -> object:
        return "" if v is None or (isinstance(v, str) and v.strip().startswith("<")) else v

    @field_validator("linkedin_client_id", "linkedin_redirect_uri", mode="before")
    @classmethod
    def _drop_placeholder(cls, v: object) -> object:
        # .env.example ships "<client-id>"-style placeholders; treat them as "not set".
        return "" if isinstance(v, str) and v.strip().startswith("<") else v

    @field_validator("ui_language", mode="before")
    @classmethod
    def _ui_language(cls, v: object) -> object:
        value = str(v or "").strip().lower()
        if not value or value.startswith("<"):
            return "en"
        if value not in ("en", "he"):
            raise ValueError("UI_LANGUAGE must be 'en' or 'he'")
        return value

    @field_validator("linkedin_redirect_uri")
    @classmethod
    def _check_redirect(cls, v: str) -> str:
        if not v:
            return v
        from urllib.parse import urlparse

        u = urlparse(v)
        local = u.hostname in ("localhost", "127.0.0.1")
        if u.scheme not in ("https", "http") or (u.scheme == "http" and not local):
            raise ValueError("LINKEDIN_REDIRECT_URI must use https (http only for localhost)")
        if u.path != "/api/v1/linkedin/callback":
            raise ValueError("LINKEDIN_REDIRECT_URI path must be /api/v1/linkedin/callback")
        return v

    @field_validator("smtp_host", "smtp_username", "smtp_from", mode="before")
    @classmethod
    def _drop_smtp_placeholder(cls, v: object) -> object:
        return "" if v is None or (isinstance(v, str) and v.strip().startswith("<")) else str(v).strip()

    @field_validator("smtp_password", mode="before")
    @classmethod
    def _drop_smtp_secret_placeholder(cls, v: object) -> object:
        return "" if v is None or (isinstance(v, str) and v.strip().startswith("<")) else v

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _smtp_port(cls, v: object) -> object:
        return (
            587 if v is None or (isinstance(v, str) and (not v.strip() or v.strip().startswith("<"))) else v
        )

    @field_validator("smtp_security", mode="before")
    @classmethod
    def _smtp_security(cls, v: object) -> object:
        value = str(v or "").strip().lower()
        if not value or value.startswith("<"):
            return "starttls"
        if value not in ("starttls", "ssl", "none"):
            raise ValueError("SMTP_SECURITY must be starttls, ssl or none")
        return value

    @field_validator("app_base_url", mode="before")
    @classmethod
    def _base_url(cls, v: object) -> object:
        value = str(v or "").strip().rstrip("/")
        if not value or value.startswith("<"):
            return "http://localhost:8380"
        if not re.match(r"^https?://[^/\s]+$", value):
            raise ValueError("APP_BASE_URL must look like https://example.com (no path)")
        return value

    @field_validator("linkedin_client_secret", mode="before")
    @classmethod
    def _drop_secret_placeholder(cls, v: object) -> object:
        return "" if isinstance(v, str) and v.strip().startswith("<") else v

    @field_validator("linkedin_api_version", mode="before")
    @classmethod
    def _api_version(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and (not v.strip() or v.strip().startswith("<"))):
            return "202608"
        if not re.fullmatch(r"\d{6}", str(v).strip()):
            raise ValueError("LINKEDIN_API_VERSION must be YYYYMM")
        return str(v).strip()

    @property
    def linkedin_configured(self) -> bool:
        return bool(self.linkedin_client_id and self.linkedin_client_secret.get_secret_value())

    @model_validator(mode="after")
    def _no_plaintext_smtp_login(self) -> "Settings":
        if self.smtp_security == "none" and self.smtp_username:
            raise ValueError(
                "SMTP_SECURITY=none cannot be used with SMTP_USERNAME (password would be sent in clear)"
            )
        return self

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def is_development(self) -> bool:
        return self.environment.lower() == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
