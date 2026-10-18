"""Widen app_meta.value for update-check JSON (notes up to 4000 chars).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-18 16:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "app_meta",
        "value",
        existing_type=sa.String(length=256),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "app_meta",
        "value",
        existing_type=sa.Text(),
        type_=sa.String(length=256),
        existing_nullable=False,
    )
