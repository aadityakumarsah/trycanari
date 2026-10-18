"""Invoices for a managed assessment, paid in fiat.

Money coming in. The wallet and every payout stay in SOL and are untouched by this.

Revision ID: 0034
Revises: 0033
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assurance_invoices",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("engagement_id", sa.Uuid(), nullable=False),
        sa.Column("sponsor_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_invoice_id", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("state", sa.Text(), server_default="open", nullable=False),
        sa.Column("hosted_url", sa.Text(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('open', 'paid', 'void', 'uncollectible')", name="invoice_state_known"
        ),
        sa.CheckConstraint("amount_cents > 0", name="invoice_amount_positive"),
        # A paid invoice knows when, and an unpaid one never pretends to.
        sa.CheckConstraint("(state = 'paid') = (paid_at IS NOT NULL)", name="paid_has_a_time"),
        sa.ForeignKeyConstraint(["engagement_id"], ["assurance_engagements.id"]),
        sa.ForeignKeyConstraint(["sponsor_id"], ["sponsors.id"]),
        sa.PrimaryKeyConstraint("id"),
        # A webhook arrives with the provider's id and must find exactly one row.
        sa.UniqueConstraint("provider", "provider_invoice_id"),
    )
    op.create_index("ix_assurance_invoices_engagement_id", "assurance_invoices", ["engagement_id"])
    op.create_index("ix_assurance_invoices_sponsor_id", "assurance_invoices", ["sponsor_id"])
    op.create_index("ix_assurance_invoices_state", "assurance_invoices", ["state"])
    # One invoice per engagement that is still owed or settled; a voided one may be
    # replaced, so those are left out of the index rather than blocking a reissue.
    op.create_index(
        "uq_assurance_invoices_live",
        "assurance_invoices",
        ["engagement_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('open', 'paid')"),
    )
    op.execute('ALTER TABLE "assurance_invoices" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    op.drop_index("uq_assurance_invoices_live", table_name="assurance_invoices")
    op.drop_index("ix_assurance_invoices_state", table_name="assurance_invoices")
    op.drop_index("ix_assurance_invoices_sponsor_id", table_name="assurance_invoices")
    op.drop_index("ix_assurance_invoices_engagement_id", table_name="assurance_invoices")
    op.drop_table("assurance_invoices")
