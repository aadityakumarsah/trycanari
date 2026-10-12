"""Bounties a sponsor has removed from their dashboard.

Revision ID: 0017
Revises: 0016

A draft or a closed bounty can be deleted. The row stays: the ledger, findings and
payouts that point at it are records of money and are never removed. A deleted
bounty is simply shown to no one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bounties", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("bounties", "deleted_at")
