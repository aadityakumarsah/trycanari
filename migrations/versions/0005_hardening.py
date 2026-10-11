"""Hardening: encrypted transcripts, re-arm checks and the payment audit log.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REARM_STATES = ("queued", "running", "passed", "failed")
PAYMENT_ACTIONS = (
    "deposit_matched",
    "deposit_unmatched",
    "deposit_credited",
    "payout_created",
    "payout_sent",
    "payout_confirmed",
    "payout_retry",
    "payout_unresolved",
    "refund_created",
    "refund_sent",
    "refund_retry",
    "refund_unresolved",
)


def upgrade() -> None:
    # Transcripts and evidence can contain a leaked secret, so they are stored encrypted.
    # The key is not available to a migration, so plaintext written before this revision
    # cannot be carried over: it is dropped, and those runs read back as having no
    # transcript. Nothing has been released on the old columns.
    op.add_column(
        "runs", sa.Column("transcript_enc", sa.LargeBinary(), server_default="", nullable=False)
    )
    op.add_column(
        "runs", sa.Column("evidence_enc", sa.LargeBinary(), server_default="", nullable=False)
    )
    op.alter_column("runs", "transcript_enc", server_default=None)
    op.alter_column("runs", "evidence_enc", server_default=None)
    op.drop_column("runs", "transcript_json")
    op.drop_column("runs", "evidence_json")

    op.create_table(
        "rearm_checks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(*REARM_STATES, name="rearm_state"),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("tripped_runs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bounty_id"], ["bounties.id"], name=op.f("fk_rearm_checks_bounty_id_bounties")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rearm_checks")),
        sa.UniqueConstraint(
            "bounty_id", "config_hash", name=op.f("uq_rearm_checks_bounty_id_config_hash")
        ),
    )
    op.create_index(op.f("ix_rearm_checks_bounty_id"), "rearm_checks", ["bounty_id"])
    op.create_index(op.f("ix_rearm_checks_state"), "rearm_checks", ["state"])

    op.create_table(
        "payment_audit",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("action", sa.Enum(*PAYMENT_ACTIONS, name="payment_action"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=True),
        sa.Column("ref_id", sa.Uuid(), nullable=False),
        sa.Column("amount_zatoshi", sa.BigInteger(), nullable=True),
        sa.Column("txid", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bounty_id"], ["bounties.id"], name=op.f("fk_payment_audit_bounty_id_bounties")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_audit")),
    )
    op.create_index(op.f("ix_payment_audit_bounty_id"), "payment_audit", ["bounty_id"])
    op.create_index(op.f("ix_payment_audit_ref_id"), "payment_audit", ["ref_id"])
    # The audit log is evidence. Like the ledger, it can be added to and nothing else.
    op.execute(
        """
        CREATE FUNCTION payment_audit_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'payment_audit is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER payment_audit_append_only
        BEFORE UPDATE OR DELETE ON payment_audit
        FOR EACH ROW EXECUTE FUNCTION payment_audit_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER payment_audit_no_truncate
        BEFORE TRUNCATE ON payment_audit
        FOR EACH STATEMENT EXECUTE FUNCTION payment_audit_append_only()
        """
    )


def downgrade() -> None:
    op.drop_table("payment_audit")
    op.execute("DROP FUNCTION payment_audit_append_only()")
    op.execute("DROP TYPE payment_action")
    op.drop_table("rearm_checks")
    op.execute("DROP TYPE rearm_state")
    # The plaintext columns come back empty: without the key the ciphertext cannot be opened.
    op.add_column(
        "runs",
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "transcript_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.alter_column("runs", "evidence_json", server_default=None)
    op.alter_column("runs", "transcript_json", server_default=None)
    op.drop_column("runs", "evidence_enc")
    op.drop_column("runs", "transcript_enc")
