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
from sqlalchemy import select

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
