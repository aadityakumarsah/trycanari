"""Notifications: what an account is told, in the site's bell and by email.

Revision ID: 0019
Revises: 0018

One row per account and event. The row is written in the transaction that makes the
event true (a finding, a payout sent), so a notification exists exactly when its event
does. The same row is the email outbox: `email_state` says whether it has gone out.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = "'bounty_broken', 'break_verified', 'payout_held', 'payout_sent', 'reward_paid'"


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("bounty_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("amount_zatoshi", sa.BigInteger(), nullable=True),
        sa.Column("txid", sa.Text(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_state", sa.Text(), server_default="pending", nullable=False),
        sa.Column("email_tries", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["sponsors.id"], name="fk_notifications_account_id_sponsors"
        ),
        sa.ForeignKeyConstraint(
            ["bounty_id"], ["bounties.id"], name="fk_notifications_bounty_id_bounties"
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.id"], name="fk_notifications_finding_id_findings"
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["sponsors.id"], name="fk_notifications_actor_id_sponsors"
        ),
        # An account is told of one event once, however often the step that tells it runs.
        sa.UniqueConstraint(
            "account_id", "kind", "finding_id", name="uq_notifications_account_id_kind_finding_id"
        ),
        sa.CheckConstraint(f"kind IN ({KINDS})", name="ck_notifications_kind_known"),
        sa.CheckConstraint(
            "email_state IN ('pending', 'sending', 'sent', 'failed', 'skipped')",
            name="ck_notifications_email_state_known",
        ),
    )
    op.create_index(
        "ix_notifications_account_created", "notifications", ["account_id", "created_at"]
    )
    op.create_index("ix_notifications_email_state", "notifications", ["email_state"])
    op.execute('ALTER TABLE "notifications" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_table("notifications")
