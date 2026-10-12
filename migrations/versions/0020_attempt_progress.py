"""How far a replay has got.

Revision ID: 0020
Revises: 0019

An attempt is replayed five times and its results are written all at once at the end,
so until then nothing said how far along it was. This counts the runs finished so far,
for the hunter watching it. It decides nothing: verdicts come from the runs table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attempts",
        sa.Column("runs_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("attempts", "runs_done")
