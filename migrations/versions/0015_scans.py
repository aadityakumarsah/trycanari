"""Baseline scans: a built-in set of attacks a sponsor runs against their own agent.

Revision ID: 0015
Revises: 0014

One scan per config: it costs model tokens, and an unchanged config would give
the same picture again. The results hold what the agent said, which may include
its secrets, so they are stored encrypted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scans",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bounty_id", sa.Uuid(), sa.ForeignKey("bounties.id"), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("results_enc", sa.LargeBinary(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("bounty_id", "config_hash", name="uq_scans_bounty_id_config_hash"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'done', 'error')", name="ck_scans_state_known"
        ),
    )
    op.create_index("ix_scans_bounty_id", "scans", ["bounty_id"])
    op.create_index("ix_scans_state", "scans", ["state"])
    op.execute('ALTER TABLE "scans" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table("scans")
