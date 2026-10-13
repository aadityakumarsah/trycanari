"""A Solana wallet behind the same three methods as every other wallet.

Deposits and payouts are native SOL, counted in lamports (1 SOL = 1,000,000,000).
A deposit is matched to its bounty by the memo it carries, as with any wallet
here. Only transactions the network has finalized are reported, so a deposit
is credited once and can never be rolled back afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any

import httpx
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from agentbounty.services.wallet import (
    MEMO_MAX_BYTES,
    SETTLED_CONFIRMATIONS,
    IncomingPage,
    IncomingPayment,
    SendResult,
    WalletError,
)

MEMO_PROGRAM = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")
# The newest transactions asked for at a time. More than this between two looks is
# caught up over the following looks.
PAGE = 200
# The RPC reports a memo as "[<length>] <text>".
_MEMO = re.compile(r"^\[\d+\] (.*)$", re.DOTALL)

logger = logging.getLogger(__name__)

# How long to wait before asking a node again that was too busy to answer a question.
RETRY_PAUSES: tuple[float, ...] = (0.5, 1.0, 2.0)
# A node can list a transaction before it can show it. One it still cannot show after
# this long is passed over, so that it does not hold up every deposit after it.
UNREADABLE_FOR_SECONDS = 600


def valid_address(address: str) -> bool:
    """Whether the text is a Solana address: 32 bytes in base58."""
    try:
        Pubkey.from_string(address.strip())
    except (ValueError, TypeError):
        return False
    return 32 <= len(address.strip()) <= 44


def keypair_from(secret: str) -> Keypair:
    """A keypair from either form a Solana tool writes one in: a base58 string, or the
    JSON list of 64 numbers in a keypair file."""
    text = secret.strip()
    try:
        if text.startswith("["):
            return Keypair.from_bytes(bytes(json.loads(text)))
        return Keypair.from_base58_string(text)
    except (ValueError, TypeError) as exc:
        raise ValueError("SOLANA_SECRET_KEY is not a Solana secret key") from exc


def memo_text(reported: str | None) -> str | None:
    if not reported:
        return None
    found = _MEMO.match(reported)
    return (found.group(1) if found else reported).strip() or None


def _busy(exc: Exception) -> bool:
    """Whether the node failed in a way that asking again may cure: it was too busy, it
    was down, or it could not be reached."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


async def rpc(
    url: str,
    method: str,
    params: list[Any],
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    retries: int = 0,
) -> Any:
    """One JSON-RPC call to a Solana node. Raises WalletError with the node's reason.

    `retries` is for questions only: a node that is too busy to answer is asked again
    after a pause. A transaction is never sent twice this way."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(transport=transport, timeout=30.0) as client:
                answer = await client.post(url, json=body)
            answer.raise_for_status()
            reply = answer.json()
            break
        except (httpx.HTTPError, ValueError) as exc:
            if attempt < retries and _busy(exc):
                await asyncio.sleep(RETRY_PAUSES[min(attempt, len(RETRY_PAUSES) - 1)])
                continue
            raise WalletError(f"the Solana RPC could not be reached: {type(exc).__name__}") from exc
    if not isinstance(reply, dict) or "error" in reply:
        error = reply.get("error") if isinstance(reply, dict) else reply
        raise WalletError(f"the Solana RPC refused {method}: {json.dumps(error)[:300]}")
    return reply.get("result")


class SolanaWallet:
    def __init__(
        self,
        rpc_url: str,
        secret_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._rpc_url = rpc_url
        self._keypair = keypair_from(secret_key)
        self._transport = transport

    @property
    def address(self) -> str:
        return str(self._keypair.pubkey())

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        return await rpc(self._rpc_url, method, params, self._transport)

    async def _ask(self, method: str, params: list[Any]) -> Any:
        """A question, which a busy node may be asked again."""
        return await rpc(self._rpc_url, method, params, self._transport, retries=len(RETRY_PAUSES))

    async def get_balance(self) -> int:
        result = await self._ask("getBalance", [self.address, {"commitment": "finalized"}])
        return int(result["value"])

    async def list_incoming(self, since_cursor: str | None) -> IncomingPage:
        """Finalized payments to the wallet since the cursor, which is the newest
        transaction already reported."""
        options: dict[str, Any] = {"limit": PAGE, "commitment": "finalized"}
        if since_cursor:
            options["until"] = since_cursor
        listed = await self._ask("getSignaturesForAddress", [self.address, options]) or []
        payments = []
        cursor = since_cursor or ""
        # Oldest first, so that the place kept is always behind everything not yet read.
        for entry in reversed(listed):
            if entry.get("err") is None:
                received = await self._received(entry["signature"])
                if received is None:
                    # The node lists the transaction and cannot show it yet. Stop here and
                    # keep the place before it: the next pass asks again. Moving on would
                    # leave a deposit unseen until the process was restarted.
                    age = time.time() - float(entry.get("blockTime") or time.time())
                    if age < UNREADABLE_FOR_SECONDS:
                        break
                    logger.error(
                        "transaction %s is still unreadable after %d seconds; passing over it",
                        entry["signature"],
                        int(age),
                    )
                elif received > 0:
                    payments.append(
                        IncomingPayment(
                            txid=entry["signature"],
                            amount_zatoshi=received,
                            memo=memo_text(entry.get("memo")),
                            confirmations=SETTLED_CONFIRMATIONS,
                        )
                    )
            cursor = entry["signature"]
        return IncomingPage(payments=tuple(payments), cursor=cursor)

    async def _received(self, signature: str) -> int | None:
        """Lamports the transaction added to the wallet: nothing, or less, for one it sent.
        None when the node cannot show the transaction yet."""
        found = await self._ask(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "json",
                    "commitment": "finalized",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        if not found or found.get("meta") is None:
            return None
        message = found["transaction"]["message"]
        keys = list(message.get("accountKeys", []))
        loaded = found["meta"].get("loadedAddresses") or {}
        keys += list(loaded.get("writable", [])) + list(loaded.get("readonly", []))
        if self.address not in keys:
            return 0
        index = keys.index(self.address)
        meta = found["meta"]
        return int(meta["postBalances"][index]) - int(meta["preBalances"][index])

    async def send_payment(
        self, to_address: str, amount_zatoshi: int, memo_text: str
    ) -> SendResult:
        if not valid_address(to_address):
            return SendResult(error="the address is not a Solana address")
        if len(memo_text.encode()) > MEMO_MAX_BYTES:
            return SendResult(error="the memo is too long")
        try:
            latest = await self._rpc("getLatestBlockhash", [{"commitment": "finalized"}])
            blockhash = Hash.from_string(latest["value"]["blockhash"])
        except (WalletError, KeyError, ValueError) as exc:
            # Nothing was signed or sent, so this is safe to try again later.
            return SendResult(error=f"could not prepare the payment: {exc}")
        instructions = [
            transfer(
                TransferParams(
                    from_pubkey=self._keypair.pubkey(),
                    to_pubkey=Pubkey.from_string(to_address.strip()),
                    lamports=amount_zatoshi,
                )
            ),
            Instruction(MEMO_PROGRAM, memo_text.encode(), []),
        ]
        message = Message.new_with_blockhash(instructions, self._keypair.pubkey(), blockhash)
        signed = Transaction([self._keypair], message, blockhash)
        # The signature is the transaction's ID, known before it is sent. If the answer to
        # sending is lost, this is what to look for on the chain.
        signature = str(signed.signatures[0])
        try:
            await self._rpc(
                "sendTransaction",
                [
                    base64.b64encode(bytes(signed)).decode(),
                    {"encoding": "base64", "preflightCommitment": "finalized"},
                ],
            )
        except WalletError as exc:
            refused = "refused sendTransaction" in str(exc)
            # Refused by the node before broadcast: definitely not sent. Anything else
            # (a timeout, a dropped connection) may have gone through.
            return SendResult(error=str(exc), ambiguous=not refused)
        return SendResult(txid=signature)
