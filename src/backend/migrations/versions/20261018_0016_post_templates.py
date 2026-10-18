"""post_templates table (FR-12)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-18 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("body", sa.String(length=3000), nullable=False),
        sa.Column("link_url", sa.String(length=2048), nullable=True),
        sa.Column("link_title", sa.String(length=200), nullable=True),
        sa.Column("link_description", sa.String(length=300), nullable=True),
        sa.Column("campaign_id", sa.UUID(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("use_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_post_templates_workspace_lower_name",
        "post_templates",
        ["workspace_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_post_templates_workspace_updated",
        "post_templates",
        ["workspace_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_post_templates_workspace_updated", table_name="post_templates")
    op.drop_index("uq_post_templates_workspace_lower_name", table_name="post_templates")
    op.drop_table("post_templates")
