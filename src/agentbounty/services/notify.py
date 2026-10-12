"""Notifications: telling the two sides of a bounty what happened to it.

A notification is a row written in the same transaction as the event it is about, so
it exists exactly when the event does, and the unique constraint on (account, kind,
finding) means a step that runs twice tells nobody twice. The row is shown under the
bell on the site and is also the email outbox, which `send_pending` drains.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from agentbounty.models import Attempt, Bounty, Finding, Notification, Payout, Sponsor
from agentbounty.services import mail
from agentbounty.settings import Settings

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]

# For the sponsor of the bounty.
BOUNTY_BROKEN = "bounty_broken"
REWARD_PAID = "reward_paid"
# For the hunter who broke it.
BREAK_VERIFIED = "break_verified"
PAYOUT_HELD = "payout_held"
PAYOUT_SENT = "payout_sent"

# An email nobody was told about within this long is no longer news, and is not sent.
EMAIL_FRESH_FOR = timedelta(hours=24)
EMAIL_MAX_TRIES = 5
# How many notifications the bell shows.
SHOWN = 30


async def _tell(
    session: AsyncSession,
    account_id: uuid.UUID,
    kind: str,
    *,
    bounty_id: uuid.UUID,
    finding_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    amount: int | None = None,
    txid: str | None = None,
) -> None:
    await session.execute(
        insert(Notification)
        .values(
            account_id=account_id,
            kind=kind,
            bounty_id=bounty_id,
            finding_id=finding_id,
            actor_id=actor_id,
            amount_zatoshi=amount,
            txid=txid,
        )
        .on_conflict_do_nothing(index_elements=["account_id", "kind", "finding_id"])
    )


async def on_break(
    session: AsyncSession, bounty: Bounty, attempt: Attempt, finding: Finding
) -> None:
    """A break was verified: the sponsor learns who broke the agent, the hunter that the
    payout is under way."""
    await _tell(
        session,
        bounty.sponsor_id,
        BOUNTY_BROKEN,
        bounty_id=bounty.id,
        finding_id=finding.id,
        actor_id=attempt.account_id,
        amount=bounty.reward_zatoshi,
    )
    if attempt.account_id is not None:
        await _tell(
            session,
            attempt.account_id,
            BREAK_VERIFIED,
            bounty_id=bounty.id,
            finding_id=finding.id,
            amount=bounty.reward_zatoshi,
        )


async def on_payout_sent(session: AsyncSession, finding: Finding, payout: Payout) -> None:
    """The reward left the escrow: the hunter is paid, and the sponsor sees it go."""
    attempt = await session.get_one(Attempt, finding.attempt_id)
    bounty = await session.get_one(Bounty, finding.bounty_id)
    if attempt.account_id is not None:
        await _tell(
            session,
            attempt.account_id,
            PAYOUT_SENT,
            bounty_id=bounty.id,
            finding_id=finding.id,
            amount=payout.amount_zatoshi,
            txid=payout.txid,
        )
    await _tell(
        session,
        bounty.sponsor_id,
        REWARD_PAID,
        bounty_id=bounty.id,
        finding_id=finding.id,
        actor_id=attempt.account_id,
        amount=payout.amount_zatoshi,
        txid=payout.txid,
    )


async def on_payout_held(session: AsyncSession, finding_id: uuid.UUID) -> None:
    """The payout could not be made automatically and waits for a person: the hunter is
    told once, however many passes find it still waiting."""
    found = (
        await session.execute(
            select(Finding, Attempt, Bounty)
            .join(Attempt, Attempt.id == Finding.attempt_id)
            .join(Bounty, Bounty.id == Finding.bounty_id)
            .where(Finding.id == finding_id)
        )
    ).one_or_none()
    if found is None:
        return
    finding, attempt, bounty = found
    hunter = attempt.account_id
    if hunter is None:
        return
    await _tell(
        session,
        hunter,
        PAYOUT_HELD,
        bounty_id=bounty.id,
        finding_id=finding.id,
        amount=bounty.reward_zatoshi,
    )


# --- the bell -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Shown:
    notification: Notification
    bounty_title: str
    actor: Sponsor | None


async def listing(session: AsyncSession, account_id: uuid.UUID) -> tuple[list[Shown], int]:
    """The account's latest notifications, newest first, and how many are unread."""
    actor = aliased(Sponsor)
    rows = await session.execute(
        select(Notification, Bounty.title, actor)
        .join(Bounty, Bounty.id == Notification.bounty_id)
        .outerjoin(actor, actor.id == Notification.actor_id)
        .where(Notification.account_id == account_id)
        .order_by(Notification.created_at.desc(), Notification.id)
        .limit(SHOWN)
    )
    unread = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.account_id == account_id, Notification.read_at.is_(None))
    )
    return [Shown(note, title, who) for note, title, who in rows], int(unread or 0)


async def mark_read(
    session: AsyncSession, account_id: uuid.UUID, ids: list[uuid.UUID] | None
) -> None:
    """Mark the account's notifications read: the ones named, or all of them."""
    mine = [Notification.account_id == account_id, Notification.read_at.is_(None)]
    if ids is not None:
        mine.append(Notification.id.in_(ids))
    await session.execute(update(Notification).where(*mine).values(read_at=datetime.now(UTC)))


# --- the email outbox ---------------------------------------------------------------------


async def _claim(
    sessions: SessionFactory, settings: Settings, done: set[uuid.UUID]
) -> tuple[uuid.UUID, mail.Email] | None:
    """Take the oldest unsent notification and commit it as "sending" before anything is
    sent, so a crash from here on can lose that email but never send it twice."""
    while True:
        async with sessions.begin() as session:
            note = await session.scalar(
                select(Notification)
                .where(Notification.email_state == "pending", Notification.id.not_in(done))
                .order_by(Notification.created_at, Notification.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if note is None:
                return None
            done.add(note.id)
            recipient = await session.get_one(Sponsor, note.account_id)
            stale = datetime.now(UTC) - note.created_at > EMAIL_FRESH_FOR
            # Only an address its owner has proven, by signing in with Google, is written
            # to. Anyone can make an account under an address that is not theirs.
            if stale or recipient.google_sub is None:
                note.email_state = "skipped"
                continue
            bounty = await session.get_one(Bounty, note.bounty_id)
            actor = await session.get(Sponsor, note.actor_id) if note.actor_id else None
            note.email_state = "sending"
            note.email_tries += 1
            event = mail.Event(
                kind=note.kind,
                recipient_name=recipient.name,
                bounty_id=str(bounty.id),
                bounty_title=bounty.title,
                actor_name=actor.name if actor else None,
                amount=note.amount_zatoshi,
                txid=note.txid,
            )
            email = mail.compose(event, recipient.email, settings.currency, settings.public_url)
            return note.id, email


async def send_pending(
    sessions: SessionFactory, sender: mail.EmailSender, settings: Settings
) -> int:
    """Send every email that is waiting, each tried once a pass. Returns how many went out."""
    sent = 0
    tried: set[uuid.UUID] = set()
    while (claimed := await _claim(sessions, settings, tried)) is not None:
        note_id, email = claimed
        try:
            await sender.send(email)
            state = "sent"
            sent += 1
        except mail.EmailError as exc:
            state = "failed" if exc.permanent else "pending"
            logger.warning("email for notification %s not sent: %s", note_id, exc)
        async with sessions.begin() as session:
            note = await session.get_one(Notification, note_id, with_for_update=True)
            if state == "pending" and note.email_tries >= EMAIL_MAX_TRIES:
                state = "failed"
            note.email_state = state
    return sent
