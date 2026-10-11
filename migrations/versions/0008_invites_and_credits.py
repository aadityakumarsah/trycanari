"""Invite-only accounts and a credit allowance for attacks.

Revision ID: 0008
Revises: 0007

A referral code admits a fixed number of new accounts. An attempt made by an
account records who made it and what it cost, so an account's allowance can be
worked out from what it has spent lately: nothing about a balance is stored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "referral_codes",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.Text(), nullable=False),
        # The ambassador who made it. A code made by the operator has no owner.
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("sponsors.id"), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("code", name="uq_referral_codes_code"),
        sa.CheckConstraint("max_uses > 0", name="ck_referral_codes_max_uses_positive"),
    )
    op.create_index("ix_referral_codes_owner_id", "referral_codes", ["owner_id"])
    op.execute('ALTER TABLE "referral_codes" ENABLE ROW LEVEL SECURITY')

    op.add_column(
        "sponsors",
        sa.Column("is_ambassador", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # The code the account joined with.
    op.add_column("sponsors", sa.Column("referral_code", sa.Text(), nullable=True))
    op.create_index("ix_sponsors_referral_code", "sponsors", ["referral_code"])

    op.add_column(
        "attempts",
        sa.Column("account_id", sa.Uuid(), sa.ForeignKey("sponsors.id"), nullable=True),
    )
    op.add_column(
        "attempts",
        sa.Column("cost_credits", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_attempts_account_id", "attempts", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_attempts_account_id", "attempts")
    op.drop_column("attempts", "cost_credits")
    op.drop_column("attempts", "account_id")
    op.drop_index("ix_sponsors_referral_code", "sponsors")
    op.drop_column("sponsors", "referral_code")
    op.drop_column("sponsors", "is_ambassador")
    op.drop_table("referral_codes")
