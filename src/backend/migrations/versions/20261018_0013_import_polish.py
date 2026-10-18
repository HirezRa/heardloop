"""Imported posts: empty text instead of a placeholder sentinel; one post per LinkedIn URN

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-18 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import IntegrityError

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SENTINEL = "Imported from LinkedIn (text not available)"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE post_versions SET body = '' "
            "WHERE id IN (SELECT current_version_id FROM posts "
            "WHERE external_source IS NOT NULL AND current_version_id IS NOT NULL) "
            "AND body = :sentinel"
        ).bindparams(sentinel=SENTINEL)
    )
    try:
        op.create_index(
            "ux_posts_workspace_urn",
            "posts",
            ["workspace_id", "linkedin_post_urn"],
            unique=True,
            postgresql_where=sa.text("linkedin_post_urn IS NOT NULL"),
        )
    except IntegrityError as exc:
        raise RuntimeError(
            "Cannot create unique index ux_posts_workspace_urn: two posts in one workspace share a "
            "linkedin_post_urn. Resolve the duplicates (delete or edit one of each pair), then run the "
            "migration again."
        ) from exc


def downgrade() -> None:
    op.drop_index("ux_posts_workspace_urn", table_name="posts")
    op.execute(
        sa.text(
            "UPDATE post_versions SET body = :sentinel "
            "WHERE id IN (SELECT current_version_id FROM posts "
            "WHERE external_source IS NOT NULL AND current_version_id IS NOT NULL) "
            "AND body = ''"
        ).bindparams(sentinel=SENTINEL)
    )
