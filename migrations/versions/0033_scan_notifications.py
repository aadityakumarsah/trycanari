"""Tell a sponsor when a daily scan finds something.

A scan notification is about a scan, not a finding, so finding_id becomes optional and
scan_id joins it. Each keeps its own uniqueness: one notification per account per kind
per finding, and one per account per kind per scan. Postgres treats NULLs as distinct in
a unique index, so the two partial indexes below replace the old whole-table constraint
rather than sitting beside it.

Revision ID: 0033
Revises: 0032
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("notifications", "finding_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("notifications", sa.Column("scan_id", sa.Uuid(), nullable=True))
    op.add_column("notifications", sa.Column("tripped_count", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_notifications_scan_id_scans"), "notifications", "scans", ["scan_id"], ["id"]
    )

    # The old constraint covered every row; the two partial indexes below take its place.
    op.drop_constraint(
        op.f("uq_notifications_account_id_kind_finding_id"), "notifications", type_="unique"
    )
    op.create_index(
        "uq_notifications_account_kind_finding",
        "notifications",
        ["account_id", "kind", "finding_id"],
        unique=True,
        postgresql_where=sa.text("finding_id IS NOT NULL"),
    )
    op.create_index(
        "uq_notifications_account_kind_scan",
        "notifications",
        ["account_id", "kind", "scan_id"],
        unique=True,
        postgresql_where=sa.text("scan_id IS NOT NULL"),
    )

    op.drop_constraint(
        op.f("ck_notifications_ck_notifications_kind_known"), "notifications", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_notifications_kind_known"),
        "notifications",
        "kind IN ('bounty_broken', 'break_verified', 'payout_held', 'payout_sent',"
        " 'reward_paid', 'scan_found')",
    )
    # A notification is about exactly one of the two.
    op.create_check_constraint(
        op.f("ck_notifications_subject_known"),
        "notifications",
        "(finding_id IS NULL) <> (scan_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_notifications_subject_known"), "notifications", type_="check")
    op.execute("DELETE FROM notifications WHERE scan_id IS NOT NULL")
    op.drop_constraint(op.f("ck_notifications_kind_known"), "notifications", type_="check")
    op.create_check_constraint(
        op.f("ck_notifications_ck_notifications_kind_known"),
        "notifications",
        "kind IN ('bounty_broken', 'break_verified', 'payout_held', 'payout_sent', 'reward_paid')",
    )
    op.drop_index("uq_notifications_account_kind_scan", table_name="notifications")
    op.drop_index("uq_notifications_account_kind_finding", table_name="notifications")
    op.create_unique_constraint(
        op.f("uq_notifications_account_id_kind_finding_id"),
        "notifications",
        ["account_id", "kind", "finding_id"],
    )
    op.drop_constraint(op.f("fk_notifications_scan_id_scans"), "notifications", type_="foreignkey")
    op.drop_column("notifications", "tripped_count")
    op.drop_column("notifications", "scan_id")
    op.alter_column("notifications", "finding_id", existing_type=sa.Uuid(), nullable=False)
