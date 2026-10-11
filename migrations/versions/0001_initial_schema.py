"""Initial schema: the ten tables, their enum types and the append-only ledger trigger.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUM_TYPES = (
    "bounty_state",
    "canary_type",
    "attempt_state",
    "finding_state",
    "deposit_state",
    "payout_state",
    "ledger_kind",
)


def upgrade() -> None:
    op.create_table(
        "sponsors",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("refund_address", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sponsors")),
        sa.UniqueConstraint("email", name=op.f("uq_sponsors_email")),
    )
    op.create_table(
        "bounties",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sponsor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state",
            sa.Enum("draft", "awaiting_deposit", "live", "paused", "closed", name="bounty_state"),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("reward_zatoshi", sa.BigInteger(), nullable=False),
        sa.Column("fee_rate", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("deposit_code", sa.Text(), nullable=False),
        sa.Column("live_config_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "fee_rate >= 0 AND fee_rate <= 1", name=op.f("ck_bounties_fee_rate_fraction")
        ),
        sa.CheckConstraint("reward_zatoshi > 0", name=op.f("ck_bounties_reward_positive")),
        sa.ForeignKeyConstraint(
            ["sponsor_id"], ["sponsors.id"], name=op.f("fk_bounties_sponsor_id_sponsors")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bounties")),
        sa.UniqueConstraint("deposit_code", name=op.f("uq_bounties_deposit_code")),
    )
    op.create_index(op.f("ix_bounties_sponsor_id"), "bounties", ["sponsor_id"], unique=False)
    op.create_table(
        "attempts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payout_address", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.Enum("queued", "running", "done", "error", name="attempt_state"),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("status_token", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bounty_id"], ["bounties.id"], name=op.f("fk_attempts_bounty_id_bounties")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attempts")),
        sa.UniqueConstraint("status_token", name=op.f("uq_attempts_status_token")),
    )
    op.create_index(op.f("ix_attempts_bounty_id"), "attempts", ["bounty_id"], unique=False)
    op.create_index(op.f("ix_attempts_state"), "attempts", ["state"], unique=False)
    op.create_table(
        "deposits",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=True),
        sa.Column("txid", sa.Text(), nullable=False),
        sa.Column("amount_zatoshi", sa.BigInteger(), nullable=False),
        sa.Column("memo_code", sa.Text(), nullable=True),
        sa.Column("confirmations", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "state",
            sa.Enum("seen", "matched", "unmatched", "credited", name="deposit_state"),
            server_default="seen",
            nullable=False,
        ),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("amount_zatoshi > 0", name=op.f("ck_deposits_amount_positive")),
        sa.ForeignKeyConstraint(
            ["bounty_id"], ["bounties.id"], name=op.f("fk_deposits_bounty_id_bounties")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deposits")),
        sa.UniqueConstraint("txid", name=op.f("uq_deposits_txid")),
    )
    op.create_index(op.f("ix_deposits_bounty_id"), "deposits", ["bounty_id"], unique=False)
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("deposit", "payout", "fee", "refund", name="ledger_kind"),
            nullable=False,
        ),
        sa.Column("amount_zatoshi", sa.BigInteger(), nullable=False),
        sa.Column("ref_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'deposit' AND amount_zatoshi > 0)"
            " OR (kind <> 'deposit' AND amount_zatoshi < 0)",
            name=op.f("ck_ledger_entries_sign_matches_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["bounty_id"], ["bounties.id"], name=op.f("fk_ledger_entries_bounty_id_bounties")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("kind", "ref_id", name=op.f("uq_ledger_entries_kind_ref_id")),
    )
    op.create_index(
        op.f("ix_ledger_entries_bounty_id"), "ledger_entries", ["bounty_id"], unique=False
    )
    op.create_table(
        "target_configs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("system_prompt_enc", sa.LargeBinary(), nullable=False),
        sa.Column("tools_enc", sa.LargeBinary(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("params_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("entry_points_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bounty_id"], ["bounties.id"], name=op.f("fk_target_configs_bounty_id_bounties")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_target_configs")),
        sa.UniqueConstraint(
            "bounty_id", "version", name=op.f("uq_target_configs_bounty_id_version")
        ),
    )
    op.create_index(
        op.f("ix_target_configs_bounty_id"), "target_configs", ["bounty_id"], unique=False
    )
    op.create_index(
        op.f("ix_target_configs_config_hash"), "target_configs", ["config_hash"], unique=False
    )
    op.create_table(
        "canaries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("config_id", sa.Uuid(), nullable=False),
        sa.Column(
            "type", sa.Enum("secret_leak", "forbidden_tool", name="canary_type"), nullable=False
        ),
        sa.Column("secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("rule_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("public_label", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(type = 'secret_leak' AND secret_enc IS NOT NULL AND rule_json IS NULL)"
            " OR (type = 'forbidden_tool' AND rule_json IS NOT NULL AND secret_enc IS NULL)",
            name=op.f("ck_canaries_definition_matches_type"),
        ),
        sa.ForeignKeyConstraint(
            ["config_id"], ["target_configs.id"], name=op.f("fk_canaries_config_id_target_configs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canaries")),
    )
    op.create_index(op.f("ix_canaries_config_id"), "canaries", ["config_id"], unique=False)
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("run_index", sa.Integer(), nullable=False),
        sa.Column("transcript_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tripped_canaries_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_usage", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], name=op.f("fk_runs_attempt_id_attempts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
        sa.UniqueConstraint("attempt_id", "run_index", name=op.f("uq_runs_attempt_id_run_index")),
    )
    op.create_index(op.f("ix_runs_attempt_id"), "runs", ["attempt_id"], unique=False)
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("canary_id", sa.Uuid(), nullable=False),
        sa.Column("tripped_runs", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            sa.Enum("verified", "paid", name="finding_state"),
            server_default="verified",
            nullable=False,
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["attempts.id"], name=op.f("fk_findings_attempt_id_attempts")
        ),
        sa.ForeignKeyConstraint(
            ["canary_id"], ["canaries.id"], name=op.f("fk_findings_canary_id_canaries")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
        sa.UniqueConstraint("attempt_id", name=op.f("uq_findings_attempt_id")),
    )
    op.create_index(op.f("ix_findings_canary_id"), "findings", ["canary_id"], unique=False)
    op.create_table(
        "payouts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("amount_zatoshi", sa.BigInteger(), nullable=False),
        sa.Column("fee_zatoshi", sa.BigInteger(), nullable=False),
        sa.Column("to_address", sa.Text(), nullable=False),
        sa.Column("txid", sa.Text(), nullable=True),
        sa.Column(
            "state",
            sa.Enum("pending", "sent", "confirmed", "failed", name="payout_state"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_zatoshi > 0", name=op.f("ck_payouts_amount_positive")),
        sa.CheckConstraint("fee_zatoshi >= 0", name=op.f("ck_payouts_fee_not_negative")),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], name=op.f("fk_payouts_finding_id_findings")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payouts")),
        sa.UniqueConstraint("finding_id", name=op.f("uq_payouts_finding_id")),
    )
    # bounties and target_configs refer to each other, so this key waits for both tables.
    op.create_foreign_key(
        op.f("fk_bounties_live_config_id_target_configs"),
        "bounties",
        "target_configs",
        ["live_config_id"],
        ["id"],
    )
    # The ledger is append-only. Balances are sums over it, so a changed or
    # deleted row would silently rewrite history.
    op.execute(
        """
        CREATE FUNCTION ledger_entries_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'ledger_entries is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER ledger_entries_append_only
        BEFORE UPDATE OR DELETE ON ledger_entries
        FOR EACH ROW EXECUTE FUNCTION ledger_entries_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER ledger_entries_no_truncate
        BEFORE TRUNCATE ON ledger_entries
        FOR EACH STATEMENT EXECUTE FUNCTION ledger_entries_append_only()
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_bounties_live_config_id_target_configs"), "bounties", type_="foreignkey"
    )
    op.drop_table("payouts")
    op.drop_index(op.f("ix_findings_canary_id"), table_name="findings")
    op.drop_table("findings")
    op.drop_index(op.f("ix_runs_attempt_id"), table_name="runs")
    op.drop_table("runs")
    op.drop_index(op.f("ix_canaries_config_id"), table_name="canaries")
    op.drop_table("canaries")
    op.drop_index(op.f("ix_target_configs_config_hash"), table_name="target_configs")
    op.drop_index(op.f("ix_target_configs_bounty_id"), table_name="target_configs")
    op.drop_table("target_configs")
    op.drop_index(op.f("ix_ledger_entries_bounty_id"), table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index(op.f("ix_deposits_bounty_id"), table_name="deposits")
    op.drop_table("deposits")
    op.drop_index(op.f("ix_attempts_state"), table_name="attempts")
    op.drop_index(op.f("ix_attempts_bounty_id"), table_name="attempts")
    op.drop_table("attempts")
    op.drop_index(op.f("ix_bounties_sponsor_id"), table_name="bounties")
    op.drop_table("bounties")
    op.drop_table("sponsors")
    op.execute("DROP FUNCTION ledger_entries_append_only()")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE {name}")
