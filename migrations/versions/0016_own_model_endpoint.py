"""A sponsor's own model endpoint, per config.

Revision ID: 0016
Revises: 0015

A config may name an OpenAI-compatible endpoint the sponsor hosts; its replays go
there in place of the platform's provider. The key for it is stored encrypted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("target_configs", sa.Column("endpoint_base", sa.Text(), nullable=True))
    op.add_column("target_configs", sa.Column("endpoint_key_enc", sa.LargeBinary(), nullable=True))
    op.create_check_constraint(
        "ck_target_configs_endpoint_has_key",
        "target_configs",
        "(endpoint_base IS NULL) = (endpoint_key_enc IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_target_configs_endpoint_has_key", "target_configs", type_="check")
    op.drop_column("target_configs", "endpoint_key_enc")
    op.drop_column("target_configs", "endpoint_base")
