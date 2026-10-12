"""Accounts that exist only to try the platform out.

Revision ID: 0021
Revises: 0020

A demo account is never shown to anyone: not on the leaderboard and not in the admin's
list of people. Its money records stay, as every money record does.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sponsors",
        sa.Column("is_demo", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("sponsors", "is_demo")
