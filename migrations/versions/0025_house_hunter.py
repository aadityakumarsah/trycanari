"""The house hunter: the platform's own attacker, run against every bounty that goes live.

Revision ID: 0025
Revises: 0024

One account is the house's. A break it finds is recorded as a finding in a state of its
own, "waived": the sponsor reads it and patches, and nothing is paid for it, because the
payment process pays only findings that are "verified". Each config of a bounty is
hunted once, and `house_hunts` says how that went.

The house goes at what is listed from now on. A config that is live as this is applied
was listed before there was a house hunter, and is recorded as not hunted, so that
switching the hunter on does nothing to a bounty that is already out there. It is hunted
once its sponsor changes it, as any new config is.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_FINDING_STATES = ("verified", "paid")


def upgrade() -> None:
    # A new enum value cannot be used in the transaction that adds it, so it is
    # committed on its own.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE finding_state ADD VALUE IF NOT EXISTS 'waived'")

    op.add_column(
        "sponsors",
        sa.Column("is_house", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "house_hunts",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bounty_id", sa.Uuid(), sa.ForeignKey("bounties.id"), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="running"),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("tries", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "claimed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "bounty_id", "config_hash", name="uq_house_hunts_bounty_id_config_hash"
        ),
        sa.CheckConstraint("state IN ('running', 'done')", name="ck_house_hunts_state_known"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('held', 'broke', 'stopped')",
            name="ck_house_hunts_outcome_known",
        ),
    )
    op.create_index("ix_house_hunts_bounty_id", "house_hunts", ["bounty_id"])
    op.create_index("ix_house_hunts_state", "house_hunts", ["state"])
    op.execute('ALTER TABLE "house_hunts" ENABLE ROW LEVEL SECURITY')
    op.execute(
        "INSERT INTO house_hunts (bounty_id, config_hash, state, outcome, detail, finished_at)"
        " SELECT b.id, c.config_hash, 'done', 'stopped',"
        " 'this config was live before the house hunter was switched on', now()"
        " FROM bounties b JOIN target_configs c ON c.id = b.live_config_id"
        " WHERE b.state = 'live'"
    )


def downgrade() -> None:
    op.drop_table("house_hunts")
    op.drop_column("sponsors", "is_house")
    # PostgreSQL cannot drop an enum value, so the type is rebuilt without it. A break the
    # house found stays on record as paid for, which is the state that pays nothing more.
    op.execute("UPDATE findings SET state = 'paid' WHERE state = 'waived'")
    op.execute("ALTER TABLE findings ALTER COLUMN state DROP DEFAULT")
    op.execute("ALTER TYPE finding_state RENAME TO finding_state_with_waived")
    op.execute("CREATE TYPE finding_state AS ENUM " + str(OLD_FINDING_STATES))
    op.execute(
        "ALTER TABLE findings ALTER COLUMN state TYPE finding_state"
        " USING state::text::finding_state"
    )
    op.execute("ALTER TABLE findings ALTER COLUMN state SET DEFAULT 'verified'")
    op.execute("DROP TYPE finding_state_with_waived")
