"""Two kinds of invite: one to hunt, one to sponsor.

Revision ID: 0022
Revises: 0021

Until now an account needed an invite only to attack, and anyone could list an agent.
A referral code now has a kind. A hunter code lets its account attack. A sponsor code
lets its account list agents, and attack too. An account holds a sponsor code in a
column of its own, so that someone who joined to hunt can be invited to sponsor later
and both codes stay used.

Everyone who has listed an agent already keeps the right to.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "referral_codes",
        sa.Column("kind", sa.Text(), server_default="hunter", nullable=False),
    )
    op.create_check_constraint("kind_known", "referral_codes", "kind IN ('hunter', 'sponsor')")
    op.add_column("sponsors", sa.Column("sponsor_code", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_sponsors_sponsor_code", "sponsors", ["sponsor_code"])
    op.add_column(
        "sponsors",
        sa.Column("sponsoring_granted", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        "UPDATE sponsors SET sponsoring_granted = true"
        " WHERE id IN (SELECT sponsor_id FROM bounties)"
    )


def downgrade() -> None:
    op.drop_column("sponsors", "sponsoring_granted")
    op.drop_constraint("uq_sponsors_sponsor_code", "sponsors", type_="unique")
    op.drop_column("sponsors", "sponsor_code")
    op.drop_constraint("kind_known", "referral_codes", type_="check")
    op.drop_column("referral_codes", "kind")
