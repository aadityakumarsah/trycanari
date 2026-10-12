"""What a browser wallet needs from the chain, fetched for it by the server.

The page may not talk to anything but this site (its Content-Security-Policy
says so), and a wallet in the browser might be set to a different network than
the one this deployment pays on. So the page builds a deposit, the sponsor's
wallet signs it, and it is this server that asks the right network for a
recent blockhash and hands the signed transaction to it.

Nothing here can move anyone's money: a transaction is only ever forwarded as
its owner signed it.
"""

from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from solders.transaction import Transaction

from agentbounty.api.deps import SettingsDep, SponsorDep
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.services import solana
from agentbounty.services.errors import ServiceError
from agentbounty.services.wallet import WalletError
from agentbounty.settings import Settings

router = APIRouter(prefix="/v1/solana", tags=["sponsor"], dependencies=[Depends(limit_by_client)])

# A Solana transaction is at most 1,232 bytes, which is 1,644 characters of base64.
MAX_TRANSACTION_CHARACTERS = 1_644


class BlockhashOut(BaseModel):
    blockhash: str
    cluster: str


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The signed transaction, base64.
    transaction: str = Field(min_length=1, max_length=MAX_TRANSACTION_CHARACTERS)


class SentOut(BaseModel):
    signature: str


def _rpc_url(settings: Settings) -> str:
    if settings.wallet_backend != "solana" or settings.solana_rpc_url is None:
        raise ServiceError(404, "this platform does not pay on Solana")
    return settings.solana_rpc_url


@router.get("/blockhash")
async def blockhash(request: Request, _: SponsorDep, settings: SettingsDep) -> BlockhashOut:
    """A recent blockhash, which every transaction must name to be accepted."""
    url = _rpc_url(settings)
    try:
        latest = await solana.rpc(
            url,
            "getLatestBlockhash",
            [{"commitment": "finalized"}],
            request.app.state.solana_transport,
        )
    except WalletError as exc:
        raise ServiceError(502, str(exc)) from exc
    return BlockhashOut(blockhash=latest["value"]["blockhash"], cluster=settings.solana_cluster)


@router.post("/send")
async def send(body: SendIn, request: Request, _: SponsorDep, settings: SettingsDep) -> SentOut:
    """Hand a transaction the sponsor's wallet has signed to the network."""
    url = _rpc_url(settings)
    try:
        signed = Transaction.from_bytes(base64.b64decode(body.transaction, validate=True))
        signed.verify()
    except (binascii.Error, ValueError, Exception) as exc:
        raise ServiceError(422, "that is not a fully signed Solana transaction") from exc
    try:
        await solana.rpc(
            url,
            "sendTransaction",
            [body.transaction, {"encoding": "base64", "preflightCommitment": "confirmed"}],
            request.app.state.solana_transport,
        )
    except WalletError as exc:
        reason = str(exc)
        if "insufficient" in reason.lower() or "AccountNotFound" in reason:
            cluster = settings.solana_cluster
            raise ServiceError(
                402, f"that wallet does not have enough SOL on {cluster} for this deposit"
            ) from exc
        raise ServiceError(502, reason) from exc
    return SentOut(signature=str(signed.signatures[0]))
