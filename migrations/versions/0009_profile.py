"""A name and a picture for an account.

Revision ID: 0009
Revises: 0008

Google gives both when someone signs in with it. An account made with an email
can set its own name.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sponsors", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("sponsors", sa.Column("picture_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sponsors", "picture_url")
    op.drop_column("sponsors", "name")
