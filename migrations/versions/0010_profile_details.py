"""What a person says about themselves.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("github", "company", "role", "location", "website", "bio")


def upgrade() -> None:
    for column in COLUMNS:
        op.add_column("sponsors", sa.Column(column, sa.Text(), nullable=True))


def downgrade() -> None:
    for column in reversed(COLUMNS):
        op.drop_column("sponsors", column)
