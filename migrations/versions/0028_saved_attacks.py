"""Keep verified attacks as durable regression and corpus records.

Revision ID: 0028
Revises: 0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    canary_type = postgresql.ENUM(name="canary_type", create_type=False)
    op.create_table(
        "saved_attacks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column("broken_config_hash", sa.Text(), nullable=False),
        sa.Column("canary_type", canary_type, nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["bounty_id"], ["bounties.id"]),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_saved_attacks_bounty_id"), "saved_attacks", ["bounty_id"])
    op.create_index(
        op.f("ix_saved_attacks_broken_config_hash"), "saved_attacks", ["broken_config_hash"]
    )
    op.create_index(op.f("ix_saved_attacks_canary_type"), "saved_attacks", ["canary_type"])
    op.create_index(
        op.f("ix_saved_attacks_finding_id"), "saved_attacks", ["finding_id"], unique=True
    )
    op.execute(
        """
        INSERT INTO saved_attacks
            (finding_id, bounty_id, broken_config_hash, canary_type, payload_json)
        SELECT findings.id, findings.bounty_id, findings.config_hash,
               canaries.type, attempts.payload_json
        FROM findings
        JOIN attempts ON attempts.id = findings.attempt_id
        JOIN canaries ON canaries.id = findings.canary_id
        """
    )
    op.add_column("scans", sa.Column("corpus_type", canary_type, nullable=True))
    op.execute('ALTER TABLE "saved_attacks" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_column("scans", "corpus_type")
    op.drop_index(op.f("ix_saved_attacks_finding_id"), table_name="saved_attacks")
    op.drop_index(op.f("ix_saved_attacks_canary_type"), table_name="saved_attacks")
    op.drop_index(op.f("ix_saved_attacks_broken_config_hash"), table_name="saved_attacks")
    op.drop_index(op.f("ix_saved_attacks_bounty_id"), table_name="saved_attacks")
    op.drop_table("saved_attacks")
