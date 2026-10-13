"""Tasks and untrusted tools, a shared library of breaks, watched bounties, and a plan.

Revision ID: 0024
Revises: 0023

A config may say what the agent is asked to do, for attacks that are planted in what a
tool returns and not written to the agent directly. A bounty may share its breaks with
the library that other sponsors' agents are tested against, and may be watched: scanned
again every day. A scan says when it finished and whether it draws on the library. An
account may hold a plan until a date, paid for by a deposit that carries the account's
plan code as its memo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("target_configs", sa.Column("task", sa.Text(), nullable=True))
    op.add_column(
        "bounties",
        sa.Column("shares_breaks", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "bounties", sa.Column("watch", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("scans", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "scans",
        sa.Column("with_library", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("sponsors", sa.Column("plan_code", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_sponsors_plan_code", "sponsors", ["plan_code"])
    op.add_column("sponsors", sa.Column("plan_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deposits", sa.Column("account_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_deposits_account_id_sponsors", "deposits", "sponsors", ["account_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_deposits_account_id_sponsors", "deposits", type_="foreignkey")
    op.drop_column("deposits", "account_id")
    op.drop_column("sponsors", "plan_until")
    op.drop_constraint("uq_sponsors_plan_code", "sponsors", type_="unique")
    op.drop_column("sponsors", "plan_code")
    op.drop_column("scans", "with_library")
    op.drop_column("scans", "finished_at")
    op.drop_column("bounties", "watch")
    op.drop_column("bounties", "shares_breaks")
    op.drop_column("target_configs", "task")
