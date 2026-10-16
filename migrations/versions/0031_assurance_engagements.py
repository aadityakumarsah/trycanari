"""Managed agent-assurance engagements.

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assurance_engagements",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column("service", sa.Text(), server_default="sprint", nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("deployment", sa.Text(), server_default="managed", nullable=False),
        sa.Column("untrusted_inputs_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sensitive_data_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "consequential_actions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("workflow_enc", sa.LargeBinary(), nullable=False),
        sa.Column("feared_failure_enc", sa.LargeBinary(), nullable=False),
        sa.Column("identity_controls_enc", sa.LargeBinary(), nullable=False),
        sa.Column("human_approval_enc", sa.LargeBinary(), nullable=False),
        sa.Column("operator_notes_enc", sa.LargeBinary(), nullable=True),
        sa.Column("limitations_enc", sa.LargeBinary(), nullable=True),
        sa.Column("quoted_price_cents", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tested_config_hash", sa.String(length=64), nullable=True),
        sa.Column("tested_config_version", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'requested', 'scoping', 'testing', 'remediation',"
            " 'retest', 'complete', 'cancelled')",
            name="status_known",
        ),
        sa.CheckConstraint("service IN ('sprint', 'continuous')", name="service_known"),
        sa.CheckConstraint("stage IN ('prototype', 'beta', 'production')", name="stage_known"),
        sa.CheckConstraint(
            "deployment IN ('managed', 'private_cloud', 'on_prem')",
            name="deployment_known",
        ),
        sa.CheckConstraint("quoted_price_cents >= 0", name="quoted_price_not_negative"),
        sa.ForeignKeyConstraint(["bounty_id"], ["bounties.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bounty_id"),
    )
    op.create_index("ix_assurance_engagements_bounty_id", "assurance_engagements", ["bounty_id"])
    op.create_index("ix_assurance_engagements_created_at", "assurance_engagements", ["created_at"])
    op.create_index("ix_assurance_engagements_status", "assurance_engagements", ["status"])
    op.execute('ALTER TABLE "assurance_engagements" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_index("ix_assurance_engagements_status", table_name="assurance_engagements")
    op.drop_index("ix_assurance_engagements_created_at", table_name="assurance_engagements")
    op.drop_index("ix_assurance_engagements_bounty_id", table_name="assurance_engagements")
    op.drop_table("assurance_engagements")
