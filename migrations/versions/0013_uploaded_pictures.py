"""A profile picture the person uploaded themselves.

Revision ID: 0013
Revises: 0012

Kept apart from the picture Google supplies, which is refreshed at every sign-in
and would otherwise overwrite it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sponsors", sa.Column("uploaded_picture_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sponsors", "uploaded_picture_url")
