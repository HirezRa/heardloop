"""LinkedIn analytics export import: extra metric columns, audience JSON, posts.external_source

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-18 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # post_metrics.source is plain text (no PG enum), so the new value "linkedin_export" needs no ALTER TYPE.
    op.add_column("post_metrics", sa.Column("members_reached", sa.BigInteger(), nullable=True))
    op.add_column("post_metrics", sa.Column("profile_viewers", sa.BigInteger(), nullable=True))
    op.add_column("post_metrics", sa.Column("followers_gained", sa.BigInteger(), nullable=True))
    op.add_column("post_metrics", sa.Column("sends", sa.BigInteger(), nullable=True))
    op.add_column(
        "post_metrics", sa.Column("demographics", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("post_metrics", sa.Column("top", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("posts", sa.Column("external_source", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "external_source")
    op.drop_column("post_metrics", "top")
    op.drop_column("post_metrics", "demographics")
    op.drop_column("post_metrics", "sends")
    op.drop_column("post_metrics", "followers_gained")
    op.drop_column("post_metrics", "profile_viewers")
    op.drop_column("post_metrics", "members_reached")
