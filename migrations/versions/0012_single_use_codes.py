"""Single-use referral codes, an admin log, and indexes for growth.

Revision ID: 0012
Revises: 0011

A referral code now admits exactly one account, and the database enforces it:
no two accounts can hold the same code. Access an admin grants by hand is kept
in a column of its own rather than as a pretend code. Every admin action is
written to a log that cannot be edited. The indexes serve the queries that are
run on every attack, which would otherwise slow down as attempts pile up.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sponsors",
        sa.Column("hunting_granted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Access granted by hand was recorded as a shared marker where a code would be.
    op.execute(
        "UPDATE sponsors SET hunting_granted = true, referral_code = NULL"
        " WHERE referral_code = 'GRANTED-BY-ADMIN'"
    )
    # Where a code admitted several accounts, the first keeps it. The rest keep their
    # access, which is what they were promised, without the code.
    op.execute(
        """
        UPDATE sponsors SET hunting_granted = true, referral_code = NULL
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY referral_code ORDER BY created_at, id
                ) AS position
                FROM sponsors WHERE referral_code IS NOT NULL
            ) ranked WHERE position > 1
        )
        """
    )
    op.drop_index("ix_sponsors_referral_code", "sponsors")
    op.create_unique_constraint("uq_sponsors_referral_code", "sponsors", ["referral_code"])
    op.drop_constraint("ck_referral_codes_max_uses_positive", "referral_codes", type_="check")
    op.drop_column("referral_codes", "max_uses")

    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("admin_id", sa.Uuid(), sa.ForeignKey("sponsors.id"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        # The account acted on, when there is one.
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("sponsors.id"), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_admin_actions_admin_id", "admin_actions", ["admin_id"])
    op.create_index("ix_admin_actions_created_at", "admin_actions", ["created_at"])
    op.execute('ALTER TABLE "admin_actions" ENABLE ROW LEVEL SECURITY')
    op.execute(
        """
        CREATE FUNCTION admin_actions_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'admin_actions is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER admin_actions_append_only
        BEFORE UPDATE OR DELETE ON admin_actions
        FOR EACH ROW EXECUTE FUNCTION admin_actions_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER admin_actions_no_truncate
        BEFORE TRUNCATE ON admin_actions
        FOR EACH STATEMENT EXECUTE FUNCTION admin_actions_append_only()
        """
    )

    # What every attack asks: what has this account, this address, this bounty done lately?
    op.create_index("ix_attempts_account_submitted", "attempts", ["account_id", "submitted_at"])
    op.create_index("ix_attempts_bounty_submitted", "attempts", ["bounty_id", "submitted_at"])
    op.create_index("ix_attempts_address_submitted", "attempts", ["payout_address", "submitted_at"])
    op.create_index("ix_sponsors_created_at", "sponsors", ["created_at"])
    op.create_index("ix_referral_codes_created_at", "referral_codes", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_referral_codes_created_at", "referral_codes")
    op.drop_index("ix_sponsors_created_at", "sponsors")
    op.drop_index("ix_attempts_address_submitted", "attempts")
    op.drop_index("ix_attempts_bounty_submitted", "attempts")
    op.drop_index("ix_attempts_account_submitted", "attempts")
    op.execute("DROP TRIGGER admin_actions_no_truncate ON admin_actions")
    op.execute("DROP TRIGGER admin_actions_append_only ON admin_actions")
    op.execute("DROP FUNCTION admin_actions_append_only()")
    op.drop_table("admin_actions")
    op.add_column(
        "referral_codes",
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_check_constraint(
        "ck_referral_codes_max_uses_positive", "referral_codes", "max_uses > 0"
    )
    op.drop_constraint("uq_sponsors_referral_code", "sponsors", type_="unique")
    op.create_index("ix_sponsors_referral_code", "sponsors", ["referral_code"])
    op.drop_column("sponsors", "hunting_granted")
