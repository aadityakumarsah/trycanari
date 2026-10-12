"""Settings the owner changes while the service runs.

Revision ID: 0018
Revises: 0017

The first one is the model provider: its address and key, which until now came only
from the environment. Values are stored encrypted, one row a setting.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("value_enc", sa.LargeBinary(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("sponsors.id"), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute('ALTER TABLE "platform_settings" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table("platform_settings")
