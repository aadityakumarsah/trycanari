"""Core loop: sponsor API keys, attempt claims, one finding per config.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sponsors", sa.Column("api_key_hash", sa.Text(), nullable=True))
    op.create_unique_constraint(op.f("uq_sponsors_api_key_hash"), "sponsors", ["api_key_hash"])

    op.add_column("attempts", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "attempts",
        sa.Column("tries", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )

    # A finding records which bounty and config it broke, so the database itself can
    # refuse a second finding for the same hole. Existing rows take both from their attempt.
    op.add_column("findings", sa.Column("bounty_id", sa.Uuid(), nullable=True))
    op.add_column("findings", sa.Column("config_hash", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE findings
        SET bounty_id = attempts.bounty_id, config_hash = attempts.config_hash
        FROM attempts
        WHERE attempts.id = findings.attempt_id
        """
    )
    op.alter_column("findings", "bounty_id", nullable=False)
    op.alter_column("findings", "config_hash", nullable=False)
    op.create_foreign_key(
        op.f("fk_findings_bounty_id_bounties"), "findings", "bounties", ["bounty_id"], ["id"]
    )
    op.create_unique_constraint(
        op.f("uq_findings_bounty_id_config_hash"), "findings", ["bounty_id", "config_hash"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_findings_bounty_id_config_hash"), "findings", type_="unique")
    op.drop_constraint(op.f("fk_findings_bounty_id_bounties"), "findings", type_="foreignkey")
    op.drop_column("findings", "config_hash")
    op.drop_column("findings", "bounty_id")
    op.drop_column("attempts", "tries")
    op.drop_column("attempts", "started_at")
    op.drop_constraint(op.f("uq_sponsors_api_key_hash"), "sponsors", type_="unique")
    op.drop_column("sponsors", "api_key_hash")
