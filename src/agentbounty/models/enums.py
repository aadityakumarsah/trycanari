"""Enumerations, each mapped to a native PostgreSQL enum type."""

from __future__ import annotations

import enum

from sqlalchemy import Enum


class BountyState(enum.StrEnum):
    DRAFT = "draft"
    AWAITING_DEPOSIT = "awaiting_deposit"
    LIVE = "live"
    PAUSED = "paused"
    CLOSED = "closed"


class CanaryType(enum.StrEnum):
    SECRET_LEAK = "secret_leak"  # noqa: S105 - a canary type, not a credential
    FORBIDDEN_TOOL = "forbidden_tool"


class AttemptState(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class FindingState(enum.StrEnum):
    VERIFIED = "verified"
    PAID = "paid"


class DepositState(enum.StrEnum):
    SEEN = "seen"
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    CREDITED = "credited"


class PayoutState(enum.StrEnum):
    PENDING = "pending"
    # Handed to the wallet, result not yet recorded. Never sent again automatically.
    SENDING = "sending"
    SENT = "sent"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class LedgerKind(enum.StrEnum):
    DEPOSIT = "deposit"
    PAYOUT = "payout"
    FEE = "fee"
    REFUND = "refund"


def pg_enum[E: enum.StrEnum](enum_class: type[E], name: str) -> Enum:
    """A native PostgreSQL enum whose labels are the members' values."""
    return Enum(enum_class, name=name, values_callable=lambda members: [m.value for m in members])
