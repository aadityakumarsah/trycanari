"""Invoices for a managed assessment, and the webhook that settles them.

Hunters are paid in SOL; this is the money coming the other way. The webhook is the one
route that moves money state with no session and no API key, so most of this file is
about what it refuses.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from agentbounty.models import AssuranceInvoice
from agentbounty.services import billing
from agentbounty.settings import Settings, SettingsError, load_settings

from .platform import Platform
from .test_assurance import quoted

SECRET = "whsec_test_not_a_real_secret"


def event(invoice_id: str, kind: str = "invoice.paid") -> bytes:
    return json.dumps({"type": kind, "data": {"object": {"id": invoice_id}}}).encode()


def stripe_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "billing_backend": "stripe",
            "stripe_secret_key": SecretStr("sk_test_not_real"),
            "stripe_webhook_secret": SecretStr(SECRET),
            "public_url": "https://trycanari.test",
        }
    )


# --- the signature ------------------------------------------------------------------------


def test_a_signature_is_accepted_only_when_it_matches_the_body() -> None:
    body = event("in_1")
    now = int(time.time())
    billing.verify_signature(body, billing.sign(body, SECRET, at=now), SECRET, now=now)

    # A body changed after signing, which is the attack the signature exists to stop.
    header = billing.sign(body, SECRET, at=now)
    with pytest.raises(billing.BillingError):
        billing.verify_signature(event("in_2"), header, SECRET, now=now)

    # Someone else's secret.
    with pytest.raises(billing.BillingError):
        billing.verify_signature(body, header, "whsec_someone_else", now=now)


def test_a_signature_is_required_and_does_not_last_forever() -> None:
    body = event("in_1")
    now = int(time.time())
    for header in (None, "", "garbage", "t=1", f"v1={'0' * 64}", "t=abc,v1=00"):
        with pytest.raises(billing.BillingError):
            billing.verify_signature(body, header, SECRET, now=now)

    # A captured webhook replayed an hour later is refused.
    old = billing.sign(body, SECRET, at=now - 3600)
    with pytest.raises(billing.BillingError):
        billing.verify_signature(body, old, SECRET, now=now)


def test_a_rotating_secret_sends_several_signatures_and_one_of_them_is_ours() -> None:
    """While a signing secret is rotated the header carries one v1 per active secret.
    Keeping only one of them would refuse genuine deliveries for the whole overlap."""
    body = event("in_1")
    now = int(time.time())
    ours = billing.sign(body, SECRET, at=now).split("v1=")[1]
    theirs = billing.sign(body, "whsec_the_other_one", at=now).split("v1=")[1]

    # Ours last, ours first: both are accepted.
    billing.verify_signature(body, f"t={now},v1={theirs},v1={ours}", SECRET, now=now)
    billing.verify_signature(body, f"t={now},v1={ours},v1={theirs}", SECRET, now=now)

    # Several signatures, none of them ours.
    with pytest.raises(billing.BillingError):
        billing.verify_signature(body, f"t={now},v1={theirs},v1={theirs}", SECRET, now=now)


# --- the webhook --------------------------------------------------------------------------


async def post_webhook(
    platform: Platform, body: bytes, *, header: str | None, at: int | None = None
) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if header is not None:
        headers["stripe-signature"] = header
    return await platform.client.post("/v1/webhooks/stripe", content=body, headers=headers)


async def invoiced(platform: Platform) -> str:
    """An open invoice, and the provider id a webhook would name it by."""
    platform.app.state.biller = billing.FakeBilling(provider="stripe")
    _, _, engagement_id, admin = await quoted(platform)
    made = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    assert made.status_code == 201, made.text
    async with platform.sessions.begin() as session:
        invoice = (await session.scalars(select(AssuranceInvoice))).one()
        return invoice.provider_invoice_id


async def test_a_signed_webhook_marks_the_invoice_paid(platform: Platform) -> None:
    platform.app.state.settings = stripe_settings(platform.app.state.settings)
    provider_id = await invoiced(platform)
    body = event(provider_id)
    now = int(time.time())

    answered = await post_webhook(platform, body, header=billing.sign(body, SECRET, at=now))
    assert answered.status_code == 200, answered.text
    assert answered.json()["status"] == "paid"

    async with platform.sessions.begin() as session:
        invoice = (await session.scalars(select(AssuranceInvoice))).one()
        assert invoice.state == "paid"
        assert invoice.paid_at is not None

    # Stripe delivers an event more than once; the second changes nothing.
    again = await post_webhook(platform, body, header=billing.sign(body, SECRET, at=now))
    assert again.json()["status"] == "paid"


async def test_an_unsigned_or_forged_webhook_cannot_mark_anything_paid(
    platform: Platform,
) -> None:
    platform.app.state.settings = stripe_settings(platform.app.state.settings)
    provider_id = await invoiced(platform)
    body = event(provider_id)
    now = int(time.time())

    for header in (None, "t=1,v1=" + "0" * 64, billing.sign(body, "wrong-secret", at=now)):
        refused = await post_webhook(platform, body, header=header)
        assert refused.status_code == 400, refused.text

    # The invoice is untouched by every one of them.
    async with platform.sessions.begin() as session:
        invoice = (await session.scalars(select(AssuranceInvoice))).one()
        assert (invoice.state, invoice.paid_at) == ("open", None)


async def test_the_webhook_ignores_what_is_not_ours_and_what_we_do_not_act_on(
    platform: Platform,
) -> None:
    platform.app.state.settings = stripe_settings(platform.app.state.settings)
    await invoiced(platform)
    now = int(time.time())

    # An invoice from another system on the same Stripe account.
    stranger = event("in_not_ours")
    answered = await post_webhook(platform, stranger, header=billing.sign(stranger, SECRET, at=now))
    assert (answered.status_code, answered.json()["status"]) == (200, "unknown")

    # An event we have no opinion about is still a delivery that succeeded.
    other: Any = event("in_1", kind="customer.created")
    answered = await post_webhook(platform, other, header=billing.sign(other, SECRET, at=now))
    assert (answered.status_code, answered.json()["status"]) == (200, "ignored")


async def test_a_deployment_that_does_not_bill_has_no_webhook(platform: Platform) -> None:
    body = event("in_1")
    now = int(time.time())
    # BILLING_BACKEND is unset in the tests, so the route is not there to be probed.
    answered = await post_webhook(platform, body, header=billing.sign(body, SECRET, at=now))
    assert answered.status_code == 404


# --- settings -----------------------------------------------------------------------------


def test_stripe_needs_its_keys(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("BILLING_BACKEND", "stripe")
    environment.setenv("PUBLIC_URL", "https://trycanari.test")
    with pytest.raises(SettingsError, match="STRIPE_SECRET_KEY"):
        load_settings()

    environment.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    with pytest.raises(SettingsError, match="STRIPE_SECRET_KEY"):
        load_settings()

    # The invoice links back to the site, so it cannot be issued without one.
    environment.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    environment.delenv("PUBLIC_URL")
    with pytest.raises(SettingsError, match="PUBLIC_URL"):
        load_settings()

    environment.setenv("PUBLIC_URL", "https://trycanari.test")
    settings = load_settings()
    assert settings.billing_backend == "stripe"
    # A secret stays out of the repr, like every other one.
    assert SECRET not in repr(settings)


def test_the_fake_backend_never_runs_in_production(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("BILLING_BACKEND", "fake")
    assert load_settings().billing_backend == "fake"

    # Everything else production needs, so the billing guard is the only thing at fault.
    environment.setenv("APP_ENV", "production")
    environment.setenv("MODEL_BACKEND", "real")
    environment.setenv("MODEL_API_BASE", "https://model.test/v1")
    environment.setenv("MODEL_API_KEY", "key-123")
    environment.setenv("WALLET_BACKEND", "zcash")
    environment.setenv("ZIPHER_CLI_PATH", "/usr/local/bin/zipher-cli")
    environment.setenv("ZIPHER_DATA_DIR", "/wallets/escrow")
    environment.setenv("ESCROW_ADDRESS", "utest1escrow")
    environment.setenv("PAYOUT_MAX_ZATOSHI", "500000000")
    environment.setenv("PAYOUT_DAILY_MAX_ZATOSHI", "2000000000")
    with pytest.raises(SettingsError, match="charges nobody"):
        load_settings()

    # Billing nobody at all is a choice production is allowed to make.
    environment.setenv("BILLING_BACKEND", "none")
    assert load_settings().billing_backend == "none"


def test_the_currency_is_an_iso_code(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("BILLING_CURRENCY", "pounds")
    with pytest.raises(SettingsError, match="three-letter"):
        load_settings()


def test_a_deployment_bills_nobody_by_default(environment: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.billing_backend == "none"
    assert billing.make_biller(settings) is None


# --- issuing an invoice at most once ------------------------------------------------------


async def test_a_repeated_issuance_picks_up_the_same_invoice() -> None:
    """A provider replays its first answer for an idempotency key it has seen. Without
    that, a retry after a failure partway through would leave the customer holding two
    payable invoices for one assessment."""
    fake = billing.FakeBilling()
    first = await fake.create_invoice(
        email="a@example.test",
        amount_cents=250_000,
        currency="usd",
        description="Assessment",
        reference="e1",
        issuance="e1:0",
    )
    same = await fake.create_invoice(
        email="a@example.test",
        amount_cents=250_000,
        currency="usd",
        description="Assessment",
        reference="e1",
        issuance="e1:0",
    )
    assert same == first
    assert len(fake.issued) == 1

    # A later lifecycle is a different issuance, and genuinely a different invoice.
    later = await fake.create_invoice(
        email="a@example.test",
        amount_cents=250_000,
        currency="usd",
        description="Assessment",
        reference="e1",
        issuance="e1:1",
    )
    assert later.provider_id != first.provider_id
    assert len(fake.issued) == 2


async def test_the_issuance_id_counts_what_the_engagement_already_has(
    platform: Platform,
) -> None:
    """The id must be the same across a retry that wrote no row, and different once a row
    exists, so it is derived from the rows themselves rather than from the attempt."""
    fake = billing.FakeBilling()
    platform.app.state.biller = fake
    _, _, engagement_id, admin = await quoted(platform)

    made = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    assert made.status_code == 201, made.text
    assert list(fake.by_issuance) == [f"{engagement_id}:0"]

    # Voided, so a replacement may be issued: one row exists, so this is a new lifecycle.
    async with platform.sessions.begin() as session:
        await session.execute(update(AssuranceInvoice).values(state="void", paid_at=None))
    again = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    assert again.status_code == 201, again.text
    assert sorted(fake.by_issuance) == [f"{engagement_id}:0", f"{engagement_id}:1"]
    assert len(fake.issued) == 2


async def test_a_retry_after_a_failure_reuses_the_issuance_and_not_a_new_one(
    platform: Platform,
) -> None:
    """The case the issuance id exists for: the provider may have gone as far as a
    payable invoice before the failure, so the retry must be the same issuance and not a
    second one."""
    fake = billing.FakeBilling(fail_with=billing.BillingError("Stripe timed out", permanent=False))
    platform.app.state.biller = fake
    _, _, engagement_id, admin = await quoted(platform)

    failed = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    assert failed.status_code == 502
    async with platform.sessions.begin() as session:
        assert (await session.scalars(select(AssuranceInvoice))).all() == []

    # The provider recovers; the retry carries the id the failed attempt used.
    fake.fail_with = None
    retried = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    assert retried.status_code == 201, retried.text
    assert list(fake.by_issuance) == [f"{engagement_id}:0"]
    assert len(fake.issued) == 1


# --- an invoice the provider holds that no row points at ----------------------------------


def paid_event(invoice_id: str, *, reference: str, amount: int = 250_000) -> bytes:
    """What Stripe sends for an invoice that was paid, metadata and all."""
    return json.dumps(
        {
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": invoice_id,
                    "amount_paid": amount,
                    "amount_due": amount,
                    "currency": "usd",
                    "hosted_invoice_url": "https://pay.stripe.test/i/orphan",
                    "metadata": {"reference": reference, "issuance": f"{reference}:0"},
                }
            },
        }
    ).encode()


async def test_a_payment_for_an_invoice_we_never_recorded_is_taken_up(
    platform: Platform,
) -> None:
    """Issuing writes its row after the provider call, and an idempotency key only lasts
    as long as the provider keeps it, so an invoice can exist at Stripe with nothing here
    pointing at it. When the money arrives it is taken up rather than ignored."""
    platform.app.state.settings = stripe_settings(platform.app.state.settings)
    platform.app.state.biller = billing.FakeBilling(provider="stripe")
    _, _, engagement_id, _ = await quoted(platform)

    # Nothing was ever issued here: this is the orphan arriving paid.
    async with platform.sessions.begin() as session:
        assert (await session.scalars(select(AssuranceInvoice))).all() == []

    body = paid_event("in_orphan", reference=engagement_id)
    now = int(time.time())
    answered = await post_webhook(platform, body, header=billing.sign(body, SECRET, at=now))
    assert (answered.status_code, answered.json()["status"]) == (200, "paid")

    async with platform.sessions.begin() as session:
        invoice = (await session.scalars(select(AssuranceInvoice))).one()
        assert (invoice.provider_invoice_id, invoice.state) == ("in_orphan", "paid")
        assert (invoice.amount_cents, invoice.currency) == (250_000, "usd")
        assert invoice.paid_at is not None
        assert str(invoice.engagement_id) == engagement_id

    # Delivered twice, as Stripe may: still one row.
    again = await post_webhook(platform, body, header=billing.sign(body, SECRET, at=now))
    assert again.json()["status"] == "paid"
    async with platform.sessions.begin() as session:
        assert len((await session.scalars(select(AssuranceInvoice))).all()) == 1


async def test_an_orphan_is_not_taken_up_over_an_invoice_that_already_exists(
    platform: Platform,
) -> None:
    """Two payable invoices for one assessment is a real discrepancy. Voiding one here
    would be deciding what somebody paid for, so it is left for a person."""
    platform.app.state.settings = stripe_settings(platform.app.state.settings)
    provider_id = await invoiced(platform)
    async with platform.sessions.begin() as session:
        engagement_id = str((await session.scalars(select(AssuranceInvoice))).one().engagement_id)

    body = paid_event("in_a_second_one", reference=engagement_id)
    now = int(time.time())
    answered = await post_webhook(platform, body, header=billing.sign(body, SECRET, at=now))
    assert (answered.status_code, answered.json()["status"]) == (200, "unknown")

    # The invoice we did issue is untouched, and no second row was written.
    async with platform.sessions.begin() as session:
        invoice = (await session.scalars(select(AssuranceInvoice))).one()
        assert (invoice.provider_invoice_id, invoice.state) == (provider_id, "open")


async def test_another_systems_invoice_is_still_ignored(platform: Platform) -> None:
    """Adoption keys off our own metadata, so one Stripe account can serve more than one
    system without this one claiming the others' invoices."""
    platform.app.state.settings = stripe_settings(platform.app.state.settings)
    platform.app.state.biller = billing.FakeBilling(provider="stripe")
    # A real engagement exists, so this shows adoption is keyed off the metadata rather
    # than falling back to whatever engagement happens to be around.
    await quoted(platform)
    now = int(time.time())

    # No metadata at all, and metadata naming something that is not an engagement.
    for body in (
        event("in_someone_elses"),
        paid_event("in_bad_ref", reference="not-a-uuid"),
        paid_event("in_no_such", reference="99999999-9999-4999-8999-999999999999"),
    ):
        answered = await post_webhook(platform, body, header=billing.sign(body, SECRET, at=now))
        assert (answered.status_code, answered.json()["status"]) == (200, "unknown")

    async with platform.sessions.begin() as session:
        assert (await session.scalars(select(AssuranceInvoice))).all() == []
