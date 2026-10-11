"""Payments: refunds, a closing marker on bounties, and the 'sending' payout state.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PAYOUT_STATES = ("pending", "sent", "confirmed", "failed")


def upgrade() -> None:
    # Between "pending" and "sent": the payment has been handed to the wallet and the
    # result is not recorded yet. A new enum value cannot be used in the transaction
    # that adds it, so it is committed on its own.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE payout_state ADD VALUE IF NOT EXISTS 'sending' AFTER 'pending'")

    op.add_column(
        "bounties", sa.Column("close_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "refunds",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column("amount_zatoshi", sa.BigInteger(), nullable=False),
        sa.Column("to_address", sa.Text(), nullable=False),
        sa.Column("txid", sa.Text(), nullable=True),
        sa.Column(
            "state",
            postgresql.ENUM(name="payout_state", create_type=False),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_zatoshi > 0", name=op.f("ck_refunds_amount_positive")),
        sa.ForeignKeyConstraint(
            ["bounty_id"], ["bounties.id"], name=op.f("fk_refunds_bounty_id_bounties")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
        sa.UniqueConstraint("bounty_id", name=op.f("uq_refunds_bounty_id")),
    )


def downgrade() -> None:
    op.drop_table("refunds")
    op.drop_column("bounties", "close_requested_at")
    # PostgreSQL cannot drop an enum value, so the type is rebuilt without it. A payout
    # caught mid-send goes back to "failed": it must not be picked up and sent again.
    op.execute("UPDATE payouts SET state = 'failed' WHERE state = 'sending'")
    op.execute("ALTER TABLE payouts ALTER COLUMN state DROP DEFAULT")
    op.execute("ALTER TYPE payout_state RENAME TO payout_state_with_sending")
    op.execute("CREATE TYPE payout_state AS ENUM " + str(OLD_PAYOUT_STATES))
    op.execute(
        "ALTER TABLE payouts ALTER COLUMN state TYPE payout_state USING state::text::payout_state"
    )
    op.execute("ALTER TABLE payouts ALTER COLUMN state SET DEFAULT 'pending'")
    op.execute("DROP TYPE payout_state_with_sending")
