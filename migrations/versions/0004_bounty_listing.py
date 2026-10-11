"""Bounty title and description, shown on the public listing.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bounties",
        sa.Column("title", sa.Text(), server_default="Untitled agent", nullable=False),
    )
    op.add_column(
        "bounties", sa.Column("description", sa.Text(), server_default="", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("bounties", "description")
    op.drop_column("bounties", "title")
