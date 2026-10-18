"""per-user interface preferences

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-17 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("ui_prefs", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "ui_prefs")
