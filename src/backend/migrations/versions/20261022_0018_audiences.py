"""audiences table; audience_id on posts and campaigns (FR-28)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-22 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audiences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("weekend_days", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_audiences_workspace_code",
        "audiences",
        ["workspace_id", sa.text("lower(code)")],
        unique=True,
    )
    op.create_index(
        "uq_audiences_workspace_default",
        "audiences",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.add_column("campaigns", sa.Column("audience_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_campaigns_audience_id_audiences",
        "campaigns",
        "audiences",
        ["audience_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("posts", sa.Column("audience_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_posts_audience_id_audiences", "posts", "audiences", ["audience_id"], ["id"], ondelete="SET NULL"
    )
    # No rows are seeded: posts without an audience keep being reported in the workspace time zone, exactly
    # as before. Owners add audiences in Settings -> Workspace (presets for common markets).


def downgrade() -> None:
    op.drop_constraint("fk_posts_audience_id_audiences", "posts", type_="foreignkey")
    op.drop_column("posts", "audience_id")
    op.drop_constraint("fk_campaigns_audience_id_audiences", "campaigns", type_="foreignkey")
    op.drop_column("campaigns", "audience_id")
    op.drop_index("uq_audiences_workspace_default", table_name="audiences")
    op.drop_index("uq_audiences_workspace_code", table_name="audiences")
    op.drop_table("audiences")
