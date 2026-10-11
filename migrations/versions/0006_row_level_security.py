"""Row level security on every table.

Revision ID: 0006
Revises: 0005

On a hosted Postgres that exposes the public schema over an auto-generated
HTTP API (Supabase does), a table without row level security can be read and
written by anyone holding the project's public key. Enabling it with no
policies denies those roles everything.

The service is unaffected: it connects as the tables' owner, and an owner is
not subject to row level security unless it is forced.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "sponsors",
    "bounties",
    "target_configs",
    "canaries",
    "attempts",
    "runs",
    "findings",
    "deposits",
    "payouts",
    "refunds",
    "ledger_entries",
    "rearm_checks",
    "payment_audit",
    # Alembic's own bookkeeping sits in the same schema.
    "alembic_version",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
