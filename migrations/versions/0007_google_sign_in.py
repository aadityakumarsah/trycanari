"""Sponsors who sign in with Google.

Revision ID: 0007
Revises: 0006

A sponsor who arrives through Google is known by the account identifier Google
gives ("sub"), which never changes, rather than by an email address, which can.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sponsors", sa.Column("google_sub", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_sponsors_google_sub", "sponsors", ["google_sub"])


def downgrade() -> None:
    op.drop_constraint("uq_sponsors_google_sub", "sponsors", type_="unique")
    op.drop_column("sponsors", "google_sub")
