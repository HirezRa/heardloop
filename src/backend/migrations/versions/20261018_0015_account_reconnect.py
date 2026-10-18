"""linkedin_accounts.needs_reconnect_reason / needs_reconnect_at

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-18 08:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "linkedin_accounts", sa.Column("needs_reconnect_reason", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "linkedin_accounts", sa.Column("needs_reconnect_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("linkedin_accounts", "needs_reconnect_at")
    op.drop_column("linkedin_accounts", "needs_reconnect_reason")
