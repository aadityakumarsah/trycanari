"""Where a payment provider tells us what became of an invoice.

This is the one route that changes money state without a session or an API key, so the
signature is the whole of its authentication. An unsigned or mis-signed request is
refused before its body is looked at: without that, anyone who learned an invoice id
could mark it paid.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request

from agentbounty.api.deps import SettingsDep, Transaction
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.services import assurance, billing
from agentbounty.services.errors import ServiceError

logger = logging.getLogger(__name__)

# Left out of the published API description: it is for the provider, not for callers.
router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"], include_in_schema=False)

# What each Stripe event means for our own record of the invoice.
_STATE_OF = {
    "invoice.paid": "paid",
    "invoice.payment_succeeded": "paid",
    "invoice.voided": "void",
    "invoice.marked_uncollectible": "uncollectible",
}


@router.post("/stripe", dependencies=[Depends(limit_by_client)])
async def stripe(
    request: Request,
    session: Transaction,
    settings: SettingsDep,
    stripe_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    if settings.billing_backend != "stripe" or settings.stripe_webhook_secret is None:
        raise ServiceError(404, "not found")
    # The signature covers the bytes as sent, so the body is verified before it is parsed.
    payload = await request.body()
    try:
        billing.verify_signature(
            payload, stripe_signature, settings.stripe_webhook_secret.get_secret_value()
        )
    except billing.BillingError as exc:
        logger.warning("a webhook was refused: %s", exc)
        raise ServiceError(400, "the webhook signature is not valid") from exc
    try:
        event: Any = json.loads(payload)
    except ValueError as exc:
        raise ServiceError(400, "the webhook body is not JSON") from exc
    kind = str(event.get("type") or "") if isinstance(event, dict) else ""
    state = _STATE_OF.get(kind)
    if state is None:
        # An event we do not act on is still a delivery that succeeded, so Stripe is told
        # so rather than left retrying it forever.
        return {"status": "ignored"}
    data = event.get("data") or {}
    invoice_id = str((data.get("object") or {}).get("id") or "")
    if not invoice_id:
        raise ServiceError(400, "the webhook names no invoice")
    settled = await assurance.settle_invoice(session, "stripe", invoice_id, state=state)
    if settled is None:
        # An invoice from another system on the same Stripe account is not ours to record.
        logger.info("a webhook named an invoice we did not issue", extra={"invoice": invoice_id})
        return {"status": "unknown"}
    return {"status": settled.state}
