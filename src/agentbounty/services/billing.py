"""Invoices for a managed assessment, paid by card or bank transfer.

This is the money coming in. Hunters are still paid in SOL through the wallet
(services/wallet.py); a sponsor buying an assessment is a company with a finance
department, and asking it to send cryptocurrency is asking it not to buy.

A backend is a class with three methods and nothing else, so another provider is
another class. The invoice itself is a row this service owns: the provider holds the
payment, we hold the record of what was billed and whether it cleared.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from agentbounty.settings import Settings

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"
# A signature older than this is refused, so a captured webhook cannot be replayed later.
SIGNATURE_TOLERANCE_SECONDS = 300


@dataclass(frozen=True)
class Invoice:
    """What a provider gave back when an invoice was created."""

    provider_id: str
    hosted_url: str | None
    amount_cents: int
    currency: str


class BillingError(RuntimeError):
    """A provider refused or could not be reached. Permanent means do not try again."""

    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


class BillingBackend(Protocol):
    # Which provider holds the payment. Stored on the invoice, so a webhook from one
    # provider can never settle a row another one issued.
    provider: str

    async def create_invoice(
        self,
        *,
        email: str,
        amount_cents: int,
        currency: str,
        description: str,
        reference: str,
        issuance: str,
    ) -> Invoice: ...

    async def void_invoice(self, provider_id: str) -> None: ...

    async def aclose(self) -> None: ...


@dataclass
class FakeBilling:
    """Keeps every invoice it is asked for and charges nobody. The hosted URL is local,
    so a demo can click it without reaching a provider."""

    provider: str = "fake"
    issued: list[Invoice] = field(default_factory=list)
    # Keyed by issuance, the way a provider keys on an idempotency key.
    by_issuance: dict[str, Invoice] = field(default_factory=dict)
    voided: list[str] = field(default_factory=list)
    # Set by a test to make the next calls fail the way a provider would.
    fail_with: BillingError | None = None
    _next: int = 0

    async def create_invoice(
        self,
        *,
        email: str,
        amount_cents: int,
        currency: str,
        description: str,
        reference: str,
        issuance: str,
    ) -> Invoice:
        # A real provider replays its first answer for a key it has seen before.
        if (seen := self.by_issuance.get(issuance)) is not None:
            return seen
        if self.fail_with is not None:
            raise self.fail_with
        self._next += 1
        invoice = Invoice(
            provider_id=f"in_fake_{self._next:06d}",
            hosted_url=f"/billing/fake/{reference}",
            amount_cents=amount_cents,
            currency=currency,
        )
        self.issued.append(invoice)
        self.by_issuance[issuance] = invoice
        logger.info(
            "invoice (nobody charged, fake backend): %s %s",
            amount_cents,
            currency,
            extra={"to": email, "reference": reference},
        )
        return invoice

    async def void_invoice(self, provider_id: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.voided.append(provider_id)

    async def aclose(self) -> None:
        return None


class StripeBilling:
    """Stripe invoices over the REST API. The key needs write access to customers and
    invoices; nothing here reads a card number, so no card data passes through us."""

    provider = "stripe"

    def __init__(self, secret_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._key = secret_key
        self._client = client or httpx.AsyncClient(timeout=20)

    async def _post(
        self, path: str, form: dict[str, str], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        # Stripe replays the original response for a key it has already seen, so a retry
        # after a failure somewhere in the middle picks the same objects back up instead
        # of making a second customer and a second payable invoice.
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = await self._client.post(
                f"{STRIPE_API}{path}", content=urlencode(form), headers=headers
            )
        except httpx.HTTPError as exc:
            raise BillingError(f"Stripe could not be reached: {exc}", permanent=False) from exc
        try:
            answer: Any = response.json()
        except ValueError:
            answer = None
        status = response.status_code
        # Stripe rate-limits with 429 and has its own outages; both are worth another go.
        if status == 429 or status >= 500:
            raise BillingError(f"Stripe answered {status}", permanent=False)
        if status >= 400 or not isinstance(answer, dict):
            problem = ""
            if isinstance(answer, dict):
                problem = str((answer.get("error") or {}).get("message") or "")
            raise BillingError(f"Stripe refused the request ({status}): {problem}", permanent=True)
        return answer

    async def create_invoice(
        self,
        *,
        email: str,
        amount_cents: int,
        currency: str,
        description: str,
        reference: str,
        issuance: str,
    ) -> Invoice:
        customer = await self._post(
            "/customers", {"email": email}, idempotency_key=f"{issuance}:customer"
        )
        customer_id = str(customer.get("id") or "")
        if not customer_id:
            raise BillingError("Stripe returned a customer with no id", permanent=True)
        # collection_method=send_invoice is the one a finance team can pay by transfer.
        invoice = await self._post(
            "/invoices",
            {
                "customer": customer_id,
                "collection_method": "send_invoice",
                "days_until_due": "30",
                "description": description,
                # Our own ids travel with the invoice, so a webhook can name the
                # engagement even for an invoice no row of ours ended up pointing at.
                "metadata[reference]": reference,
                "metadata[issuance]": issuance,
            },
            idempotency_key=f"{issuance}:invoice",
        )
        invoice_id = str(invoice.get("id") or "")
        if not invoice_id:
            raise BillingError("Stripe returned an invoice with no id", permanent=True)
        await self._post(
            "/invoiceitems",
            {
                "customer": customer_id,
                "invoice": invoice_id,
                "amount": str(amount_cents),
                "currency": currency,
                "description": description,
            },
            idempotency_key=f"{issuance}:item",
        )
        finalized = await self._post(
            f"/invoices/{invoice_id}/finalize", {}, idempotency_key=f"{issuance}:finalize"
        )
        hosted = finalized.get("hosted_invoice_url")
        return Invoice(
            provider_id=invoice_id,
            hosted_url=str(hosted) if hosted else None,
            amount_cents=amount_cents,
            currency=currency,
        )

    async def void_invoice(self, provider_id: str) -> None:
        await self._post(f"/invoices/{provider_id}/void", {})

    async def aclose(self) -> None:
        await self._client.aclose()


def make_biller(settings: Settings) -> BillingBackend | None:
    """The configured backend, or None when this deployment issues no invoices."""
    if settings.billing_backend == "fake":
        return FakeBilling()
    if settings.billing_backend == "stripe":
        # The settings refuse to load without it.
        assert settings.stripe_secret_key is not None  # noqa: S101
        return StripeBilling(settings.stripe_secret_key.get_secret_value())
    return None


def verify_signature(
    payload: bytes, header: str | None, secret: str, *, now: float | None = None
) -> None:
    """Check a Stripe webhook really came from Stripe.

    Without this an unsigned POST could mark any invoice paid, so a webhook that does not
    verify is refused rather than logged and accepted. The signed value is the timestamp
    and the raw body, which is why the body must be read before it is parsed.
    """
    if not header:
        raise BillingError("the webhook carried no signature", permanent=True)
    timestamp: str | None = None
    # While a signing secret is being rotated the header carries one v1 per active
    # secret, so every candidate is kept: collapsing them to one would refuse a genuine
    # delivery for the whole of the overlap.
    candidates: list[str] = []
    for piece in header.split(","):
        name, _, value = piece.partition("=")
        if not _:
            continue
        if name.strip() == "t":
            timestamp = value.strip()
        elif name.strip() == "v1":
            candidates.append(value.strip())
    if not timestamp or not candidates:
        raise BillingError("the webhook signature is malformed", permanent=True)
    try:
        signed_at = int(timestamp)
    except ValueError:
        raise BillingError("the webhook signature is malformed", permanent=True) from None
    # A signature captured once must not work forever.
    if abs((now if now is not None else time.time()) - signed_at) > SIGNATURE_TOLERANCE_SECONDS:
        raise BillingError("the webhook signature has expired", permanent=True)
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    # Each comparison is constant time: a timing difference would leak the signature a
    # byte at a time. Stopping at the first match only reveals which candidate matched,
    # and the caller supplied those.
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        raise BillingError("the webhook signature does not match", permanent=True)


def sign(payload: bytes, secret: str, *, at: int) -> str:
    """The header Stripe would send for this body. Used by the tests and by nothing else."""
    digest = hmac.new(secret.encode(), f"{at}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={at},v1={digest}"
