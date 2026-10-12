"""Telling both sides of a bounty what happened: the bell, the emails, and a hunter's
own record of their attacks."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select, update

from agentbounty.models import Bounty as BountyRow
from agentbounty.models import Notification
from agentbounty.models import Sponsor as SponsorRow
from agentbounty.money import ZEC
from agentbounty.services import mail, notify
from agentbounty.services.payment import SpendingCaps
from agentbounty.settings import Settings

from .platform import HUNTER, REWARD, STRONG, WEAK, Platform, Sponsor


async def attack(hunter: Sponsor, bounty_id: str, content: str) -> dict[str, str]:
    body = {"payout_address": HUNTER, "fills": [{"slot": "user_message", "content": content}]}
    accepted: dict[str, str] = await hunter.call(
        "POST", f"/v1/bounties/{bounty_id}/attempts", 202, json=body
    )
    return accepted


async def broken(platform: Platform) -> tuple[Sponsor, Sponsor, str, str]:
    """A funded bounty broken by a hunter with an account, not yet paid."""
    sponsor = await platform.sponsor("sponsor@example.com")
    hunter = await platform.sponsor("hunter@example.com")
    await hunter.call("PATCH", "/v1/account", json={"name": "Ada Hunter", "github": "ada"})
    bounty_id = await sponsor.funded(platform)
    accepted = await attack(hunter, bounty_id, STRONG)
    assert (await platform.poll(accepted))["verdict"] == "verified"
    return sponsor, hunter, bounty_id, accepted["attempt_id"]


def kinds(told: dict[str, Any]) -> list[str]:
    return [item["kind"] for item in told["items"]]


async def test_a_break_tells_the_sponsor_who_and_the_hunter_what_happens_next(
    platform: Platform,
) -> None:
    sponsor, hunter, bounty_id, _ = await broken(platform)

    told = await sponsor.call("GET", "/v1/notifications")
    assert told["unread"] == 1
    (note,) = told["items"]
    assert (note["kind"], note["bounty_id"], note["read"]) == ("bounty_broken", bounty_id, False)
    assert note["actor"] == {"name": "Ada Hunter", "picture_url": None, "github": "ada"}
    assert note["amount_zatoshi"] == REWARD
    assert kinds(await hunter.call("GET", "/v1/notifications")) == ["break_verified"]

    await platform.payments.tick()
    # Paying again changes nothing: an account is told of one event once.
    await platform.payments.tick()
    told = await sponsor.call("GET", "/v1/notifications")
    assert sorted(kinds(told)) == ["bounty_broken", "reward_paid"]
    theirs = await hunter.call("GET", "/v1/notifications")
    assert sorted(kinds(theirs)) == ["break_verified", "payout_sent"]
    paid = next(item for item in theirs["items"] if item["kind"] == "payout_sent")
    assert paid["amount_zatoshi"] == REWARD
    assert paid["txid"]
    assert paid["actor"] is None

    one = [told["items"][0]["id"]]
    assert (await sponsor.call("POST", "/v1/notifications/read", json={"ids": one}))["unread"] == 1
    cleared = await sponsor.call("POST", "/v1/notifications/read", json={})
    assert cleared["unread"] == 0
    assert all(item["read"] for item in cleared["items"])
    # Reading yours does not read anyone else's.
    assert (await hunter.call("GET", "/v1/notifications"))["unread"] == 2


async def test_notifications_need_an_account_and_a_weak_attack_makes_none(
    platform: Platform,
) -> None:
    assert (await platform.client.get("/v1/notifications")).status_code == 401
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    await platform.poll(await platform.submit(bounty_id, WEAK))
    assert await sponsor.call("GET", "/v1/notifications") == {"unread": 0, "items": []}

    # A hunter with no account can break it: the sponsor is told, with nobody to name.
    await platform.poll(await platform.submit(bounty_id, STRONG))
    (note,) = (await sponsor.call("GET", "/v1/notifications"))["items"]
    assert (note["kind"], note["actor"]) == ("bounty_broken", None)


async def test_a_payout_that_cannot_be_made_tells_the_hunter_once(platform: Platform) -> None:
    _, hunter, _, _ = await broken(platform)
    capped = platform.payment_service(caps=SpendingCaps(per_payout=REWARD - 1))
    await capped.tick()
    await capped.tick()
    assert sorted(kinds(await hunter.call("GET", "/v1/notifications"))) == [
        "break_verified",
        "payout_held",
    ]


async def test_a_hunter_reads_their_own_attacks_and_what_they_were_paid(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor("sponsor@example.com")
    hunter = await platform.sponsor("hunter@example.com")
    bounty_id = await sponsor.funded(platform)
    await platform.poll(await attack(hunter, bounty_id, WEAK))
    won = await attack(hunter, bounty_id, STRONG)
    await platform.poll(won)

    mine = await hunter.call("GET", "/v1/account/attempts")
    assert mine["totals"] == {
        "attempts": 2,
        "verified": 1,
        "earned_zatoshi": 0,
        "pending_zatoshi": REWARD,
        "credits_spent": 0,
    }
    first, second = mine["items"]
    assert (first["id"], first["verdict"], first["tripped_runs"], first["total_runs"]) == (
        won["attempt_id"],
        "verified",
        5,
        5,
    )
    assert first["canary"] == {"type": "forbidden_tool", "public_label": "unapproved_transfer"}
    assert first["attack"] == [{"slot": "user_message", "content": STRONG}]
    assert (first["bounty_id"], first["reward_zatoshi"], first["payout"]) == (
        bounty_id,
        REWARD,
        None,
    )
    assert first["payout_address"] == HUNTER
    assert (second["verdict"], second["tripped_runs"], second["canary"]) == (
        "not_verified",
        0,
        None,
    )

    await platform.payments.tick()
    mine = await hunter.call("GET", "/v1/account/attempts")
    assert (mine["totals"]["earned_zatoshi"], mine["totals"]["pending_zatoshi"]) == (REWARD, 0)
    payout = mine["items"][0]["payout"]
    assert (payout["state"], payout["amount_zatoshi"], payout["to_address"]) == (
        "sent",
        REWARD,
        HUNTER,
    )
    assert payout["txid"]

    # The full result, without the status token.
    detail = await hunter.call("GET", f"/v1/account/attempts/{won['attempt_id']}")
    assert detail == await platform.status(won)
    assert detail["replies"]
    # The sponsor made neither attempt: they have none, and cannot read the hunter's.
    assert (await sponsor.call("GET", "/v1/account/attempts"))["items"] == []
    await sponsor.call("GET", f"/v1/account/attempts/{won['attempt_id']}", 404)


async def test_a_completed_bounty_shows_who_broke_it_and_never_how(platform: Platform) -> None:
    _, _, bounty_id, _ = await broken(platform)
    await platform.payments.tick()
    listed = await platform.client.get("/v1/bounties?show=completed")
    (over,) = listed.json()
    (by,) = over["broken_by"]
    assert (by["name"], by["github"], by["picture_url"]) == ("Ada Hunter", "ada", None)
    assert by["canary"] == {"type": "forbidden_tool", "public_label": "unapproved_transfer"}
    assert (by["reward_zatoshi"], by["tripped_runs"], by["paid"]) == (REWARD, 5, True)
    assert "hunter@example.com" not in listed.text
    assert "transfer_funds" not in listed.text
    shown = (await platform.client.get(f"/v1/bounties/{bounty_id}")).json()
    assert shown["broken_by"] == over["broken_by"]


# --- email --------------------------------------------------------------------------------


async def prove_addresses(platform: Platform) -> None:
    """Make every account one that signed in with Google, so its address is proven."""
    async with platform.sessions.begin() as session:
        for index, account in enumerate(await session.scalars(select(SponsorRow))):
            account.google_sub = f"google-{index}"


def mailing(platform: Platform) -> Settings:
    settings: Settings = platform.app.state.settings
    return settings.model_copy(update={"public_url": "https://bounty.example"})


async def test_both_sides_are_emailed_once_about_a_break_and_about_the_payout(
    platform: Platform,
) -> None:
    await broken(platform)
    await prove_addresses(platform)
    outbox = mail.FakeEmail()
    settings = mailing(platform)

    assert await notify.send_pending(platform.sessions, outbox, settings) == 2
    by_address = {email.to: email for email in outbox.sent}
    to_sponsor = by_address["sponsor@example.com"]
    assert to_sponsor.subject == "Ada Hunter broke your agent: Untitled agent"
    assert "https://bounty.example/dashboard/b/" in to_sponsor.text
    to_hunter = by_address["hunter@example.com"]
    assert to_hunter.subject == "Verified: you broke Untitled agent"
    assert "payout of 0.01 ZEC" in to_hunter.text
    assert "https://bounty.example/attempts" in to_hunter.html
    # Nothing is sent twice.
    assert await notify.send_pending(platform.sessions, outbox, settings) == 0

    await platform.payments.tick()
    assert await notify.send_pending(platform.sessions, outbox, settings) == 2
    assert sorted(email.subject for email in outbox.sent[2:]) == [
        "Reward paid for Untitled agent",
        "You were paid 0.01 ZEC for Untitled agent",
    ]


async def test_an_unproven_address_and_stale_news_are_never_emailed(platform: Platform) -> None:
    await broken(platform)
    outbox = mail.FakeEmail()
    settings = mailing(platform)
    # Neither account signed in with Google: anyone could have typed those addresses.
    assert await notify.send_pending(platform.sessions, outbox, settings) == 0
    async with platform.sessions.begin() as session:
        states = (await session.scalars(select(Notification.email_state))).all()
    assert states == ["skipped", "skipped"]

    await prove_addresses(platform)
    await platform.payments.tick()
    async with platform.sessions.begin() as session:
        await session.execute(
            update(Notification)
            .where(Notification.email_state == "pending")
            .values(created_at=datetime.now(UTC) - timedelta(days=2))
        )
    assert await notify.send_pending(platform.sessions, outbox, settings) == 0
    assert outbox.sent == []


async def test_an_email_the_provider_refuses_is_retried_only_if_that_could_help(
    platform: Platform,
) -> None:
    await broken(platform)
    await prove_addresses(platform)
    settings = mailing(platform)
    outbox = mail.FakeEmail(fail_with=mail.EmailError("busy", permanent=False))

    async def states() -> list[str]:
        async with platform.sessions.begin() as session:
            return list((await session.scalars(select(Notification.email_state))).all())

    # One try each a pass, and back in the queue.
    assert await notify.send_pending(platform.sessions, outbox, settings) == 0
    assert await states() == ["pending", "pending"]
    for _ in range(notify.EMAIL_MAX_TRIES - 1):
        await notify.send_pending(platform.sessions, outbox, settings)
    assert await states() == ["failed", "failed"]

    await platform.payments.tick()
    outbox.fail_with = mail.EmailError("no such address", permanent=True)
    await notify.send_pending(platform.sessions, outbox, settings)
    assert await states() == ["failed"] * 4


def test_an_email_escapes_what_people_typed() -> None:
    event = mail.Event(
        kind="bounty_broken",
        recipient_name="Sam Sponsor",
        bounty_id="b1",
        bounty_title="<script>alert(1)</script>\nagent",
        actor_name="<b>Eve</b>",
        amount=250_000_000,
        txid=None,
    )
    email = mail.compose(event, "sam@example.com", ZEC, "https://bounty.example/")
    assert email.subject == "<b>Eve</b> broke your agent: <script>alert(1)</script> agent"
    assert "<script>" not in email.html
    assert "&lt;b&gt;Eve&lt;/b&gt;" in email.html
    assert "Hi Sam, " in email.text
    assert "Amount: 2.5 ZEC" in email.text
    assert 'href="https://bounty.example/dashboard/b/b1"' in email.html


async def test_cloudflare_is_sent_the_email_and_its_refusals_are_told_apart() -> None:
    seen: list[httpx.Request] = []
    answer: dict[str, Any] = {"status": 200, "body": {"success": True, "result": {}}}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(answer["status"], json=answer["body"])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    sender = mail.CloudflareEmail("acct", "token", "alerts@example.com", "trycanari", client=client)
    email = mail.Email(to="ada@example.com", subject="Hello", text="plain", html="<p>rich</p>")

    await sender.send(email)
    (request,) = seen
    assert str(request.url) == f"{mail.CLOUDFLARE_API}/accounts/acct/email/sending/send"
    assert request.headers["authorization"] == "Bearer token"
    assert json.loads(request.content) == {
        "to": "ada@example.com",
        "from": {"address": "alerts@example.com", "name": "trycanari"},
        "subject": "Hello",
        "text": "plain",
        "html": "<p>rich</p>",
    }

    answer.update(status=503, body={})
    with pytest.raises(mail.EmailError) as busy:
        await sender.send(email)
    assert busy.value.permanent is False
    answer.update(status=400, body={"success": False, "errors": [{"message": "bad sender"}]})
    with pytest.raises(mail.EmailError) as refused:
        await sender.send(email)
    assert refused.value.permanent is True
    bounced = {"success": True, "result": {"permanent_bounces": ["ada@example.com"]}}
    answer.update(status=200, body=bounced)
    with pytest.raises(mail.EmailError) as gone:
        await sender.send(email)
    assert gone.value.permanent is True
    await sender.aclose()


def test_the_cloudflare_backend_needs_its_account_token_sender_and_site(
    service_settings: Settings,
) -> None:
    base = service_settings.model_dump()
    assert mail.make_sender(service_settings) is None
    with pytest.raises(ValidationError, match="CLOUDFLARE_ACCOUNT_ID"):
        Settings(**{**base, "email_backend": "cloudflare"})
    ready = {
        **base,
        "email_backend": "cloudflare",
        "cloudflare_account_id": "acct",
        "cloudflare_email_token": SecretStr("token"),
        "email_from": "alerts@example.com",
    }
    with pytest.raises(ValidationError, match="PUBLIC_URL"):
        Settings(**ready)
    settings = Settings(**{**ready, "public_url": "https://bounty.example"})
    assert isinstance(mail.make_sender(settings), mail.CloudflareEmail)


# --- demo accounts ------------------------------------------------------------------------


async def test_a_demo_account_is_on_no_leaderboard_and_a_deleted_bounty_in_no_feed(
    platform: Platform,
) -> None:
    _, _, bounty_id, _ = await broken(platform)
    await platform.payments.tick()
    client = platform.client
    assert [who["name"] for who in (await client.get("/v1/leaderboard")).json()] == ["Ada Hunter"]
    assert len((await client.get("/v1/activity")).json()["recent_breaks"]) == 1

    async with platform.sessions.begin() as session:
        await session.execute(
            update(SponsorRow).where(SponsorRow.email == "hunter@example.com").values(is_demo=True)
        )
    assert (await client.get("/v1/leaderboard")).json() == []

    # The break stays counted, but a feed that links to the bounty no longer lists it.
    async with platform.sessions.begin() as session:
        await session.execute(
            update(BountyRow).where(BountyRow.id == bounty_id).values(deleted_at=datetime.now(UTC))
        )
    feed = (await client.get("/v1/activity")).json()
    assert feed["recent_breaks"] == []
    assert feed["stats"]["verified_breaks"] == 1
