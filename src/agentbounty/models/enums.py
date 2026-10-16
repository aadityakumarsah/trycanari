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
    SYSTEM_PROMPT_LEAK = "system_prompt_leak"
    FORBIDDEN_TOOL = "forbidden_tool"
    UNAUTHORIZED_ACTION = "unauthorized_action"
    DATA_EXFILTRATION = "data_exfiltration"
    PHISHING_LINK_INJECTION = "phishing_link_injection"
    MALICIOUS_CODE_INJECTION = "malicious_code_injection"
    POLICY_VIOLATION = "policy_violation"


class AttemptState(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class FindingState(enum.StrEnum):
    VERIFIED = "verified"
    PAID = "paid"
    # Found by the house hunter. It is the sponsor's to read and to patch, and nothing
    # is owed for it: the payment process pays only what is "verified".
    WAIVED = "waived"


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


class RearmState(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    # The paid attack no longer breaks the config: the bounty may reopen on it.
    PASSED = "passed"
    FAILED = "failed"


class PaymentAction(enum.StrEnum):
    """Every kind of entry in the payment audit log."""

    DEPOSIT_MATCHED = "deposit_matched"
    DEPOSIT_UNMATCHED = "deposit_unmatched"
    DEPOSIT_CREDITED = "deposit_credited"
    PAYOUT_CREATED = "payout_created"
    PAYOUT_SENT = "payout_sent"
    PAYOUT_CONFIRMED = "payout_confirmed"
    PAYOUT_RETRY = "payout_retry"
    PAYOUT_UNRESOLVED = "payout_unresolved"
    REFUND_CREATED = "refund_created"
    REFUND_SENT = "refund_sent"
    REFUND_RETRY = "refund_retry"
    REFUND_UNRESOLVED = "refund_unresolved"


class LedgerKind(enum.StrEnum):
    DEPOSIT = "deposit"
    PAYOUT = "payout"
    FEE = "fee"
    REFUND = "refund"


def pg_enum[E: enum.StrEnum](enum_class: type[E], name: str) -> Enum:
    """A native PostgreSQL enum whose labels are the members' values."""
    return Enum(enum_class, name=name, values_callable=lambda members: [m.value for m in members])
