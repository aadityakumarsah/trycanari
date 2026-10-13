"""Codes for several people, banned accounts, and a token limit per account.

Revision ID: 0023
Revises: 0022

A referral code may now admit more than one account: it says how many, and it is used up
when that many hold it. A code still admits one unless it is made for more. So two
accounts may hold the same code, and what stops one too many is the count taken with the
code's row locked, not a unique constraint.

An admin may ban an account, give it a token limit of its own in place of its tier's,
and wipe what it has spent so its allowance is full again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "referral_codes",
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.create_check_constraint("max_uses_positive", "referral_codes", "max_uses > 0")
    op.drop_constraint("uq_sponsors_referral_code", "sponsors", type_="unique")
    op.create_index("ix_sponsors_referral_code", "sponsors", ["referral_code"])
    op.drop_constraint("uq_sponsors_sponsor_code", "sponsors", type_="unique")
    op.create_index("ix_sponsors_sponsor_code", "sponsors", ["sponsor_code"])

    op.add_column("sponsors", sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sponsors", sa.Column("credit_limit", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "credit_limit_not_negative", "sponsors", "credit_limit IS NULL OR credit_limit >= 0"
    )
    op.add_column(
        "sponsors", sa.Column("credits_reset_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sponsors", "credits_reset_at")
    op.drop_constraint("credit_limit_not_negative", "sponsors", type_="check")
    op.drop_column("sponsors", "credit_limit")
    op.drop_column("sponsors", "banned_at")
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
    op.execute(
        """
        UPDATE sponsors
        SET hunting_granted = true, sponsoring_granted = true, sponsor_code = NULL
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY sponsor_code ORDER BY created_at, id
                ) AS position
                FROM sponsors WHERE sponsor_code IS NOT NULL
            ) ranked WHERE position > 1
        )
        """
    )
    op.drop_index("ix_sponsors_sponsor_code", "sponsors")
    op.create_unique_constraint("uq_sponsors_sponsor_code", "sponsors", ["sponsor_code"])
    op.drop_index("ix_sponsors_referral_code", "sponsors")
    op.create_unique_constraint("uq_sponsors_referral_code", "sponsors", ["referral_code"])
    op.drop_constraint("max_uses_positive", "referral_codes", type_="check")
    op.drop_column("referral_codes", "max_uses")
