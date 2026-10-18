"""post metrics: saves, followers, note, recorded_by; weekly digest flag

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-17 23:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("post_metrics", sa.Column("saves", sa.BigInteger(), nullable=True))
    op.add_column("post_metrics", sa.Column("followers", sa.BigInteger(), nullable=True))
    op.add_column("post_metrics", sa.Column("note", sa.String(length=200), nullable=True))
    op.add_column("post_metrics", sa.Column("recorded_by", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_post_metrics_recorded_by_users",
        "post_metrics",
        "users",
        ["recorded_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "workspaces", sa.Column("weekly_digest", sa.Boolean(), server_default="true", nullable=False)
    )
    op.add_column("workspaces", sa.Column("last_digest_week", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "last_digest_week")
    op.drop_column("workspaces", "weekly_digest")
    op.drop_constraint("fk_post_metrics_recorded_by_users", "post_metrics", type_="foreignkey")
    op.drop_column("post_metrics", "recorded_by")
    op.drop_column("post_metrics", "note")
    op.drop_column("post_metrics", "followers")
    op.drop_column("post_metrics", "saves")
