"""The scaffold a sponsor puts around each entry point.

Revision ID: 0026
Revises: 0025

An attack arrives through an entry point, and the sponsor owns the frame around it: the
name a document is given, the tool whose result carries planted text, the URL of a page
the agent fetches, how many turns a hunter may script. The frame is per slot and is
stored as one object keyed by slot name; a config that declares none keeps NULL, and
hashes exactly as it did before, so attempts and findings bound to an old hash still
mean what they meant.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "target_configs",
        sa.Column("scaffold_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("target_configs", "scaffold_json")
