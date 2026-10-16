"""Record scheduled and CI corpus replays for every agent version.

Revision ID: 0029
Revises: 0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "continuous_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column("config_id", sa.Uuid(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="queued", nullable=False),
        sa.Column("results_enc", sa.LargeBinary(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("state IN ('queued', 'running', 'done', 'error')", name="state_known"),
        sa.CheckConstraint(
            "trigger IN ('version', 'ci', 'schedule', 'model_update')", name="trigger_known"
        ),
        sa.ForeignKeyConstraint(["bounty_id"], ["bounties.id"]),
        sa.ForeignKeyConstraint(["config_id"], ["target_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_continuous_runs_bounty_id"), "continuous_runs", ["bounty_id"])
    op.create_index(op.f("ix_continuous_runs_config_hash"), "continuous_runs", ["config_hash"])
    op.create_index(op.f("ix_continuous_runs_config_id"), "continuous_runs", ["config_id"])
    op.create_index(op.f("ix_continuous_runs_state"), "continuous_runs", ["state"])
    op.create_index(
        "ix_continuous_runs_bounty_finished", "continuous_runs", ["bounty_id", "finished_at"]
    )
    op.execute('ALTER TABLE "continuous_runs" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_index("ix_continuous_runs_bounty_finished", table_name="continuous_runs")
    op.drop_index(op.f("ix_continuous_runs_state"), table_name="continuous_runs")
    op.drop_index(op.f("ix_continuous_runs_config_id"), table_name="continuous_runs")
    op.drop_index(op.f("ix_continuous_runs_config_hash"), table_name="continuous_runs")
    op.drop_index(op.f("ix_continuous_runs_bounty_id"), table_name="continuous_runs")
    op.drop_table("continuous_runs")
