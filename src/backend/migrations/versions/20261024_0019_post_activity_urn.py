"""posts.linkedin_activity_urn for posts imported from their LinkedIn link (FR-29)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-24 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("linkedin_activity_urn", sa.String(length=128), nullable=True))
    op.create_index(
        "ux_posts_workspace_activity_urn",
        "posts",
        ["workspace_id", "linkedin_activity_urn"],
        unique=True,
        postgresql_where=sa.text("linkedin_activity_urn IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_posts_workspace_activity_urn", table_name="posts")
    op.drop_column("posts", "linkedin_activity_urn")
