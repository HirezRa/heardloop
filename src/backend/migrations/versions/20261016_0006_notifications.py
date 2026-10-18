"""notifications, webhook outbox, e-mail preference

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("email_notifications", sa.Boolean(), server_default="true", nullable=False)
    )
    op.add_column("workspaces", sa.Column("webhook_url", sa.String(length=2048), nullable=True))
    op.add_column("workspaces", sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "workspaces", sa.Column("webhook_include_text", sa.Boolean(), server_default="false", nullable=False)
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("post_id", sa.UUID(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_status", sa.String(length=16), nullable=False),
        sa.Column("email_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])
    op.create_index("ix_notifications_email_status", "notifications", ["email_status", "created_at"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_deliveries_due", "webhook_deliveries", ["status", "next_attempt_at"])
    op.create_index(
        "ix_webhook_deliveries_workspace_created", "webhook_deliveries", ["workspace_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_workspace_created", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_due", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_notifications_email_status", table_name="notifications")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_column("workspaces", "webhook_include_text")
    op.drop_column("workspaces", "webhook_secret_encrypted")
    op.drop_column("workspaces", "webhook_url")
    op.drop_column("users", "email_notifications")
