"""The Solana wallet, with the RPC node replaced by a stand-in ledger."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction

from agentbounty.money import SOL, ZEC, format_amount, parse_amount
from agentbounty.services.solana import MEMO_PROGRAM, SolanaWallet, keypair_from, valid_address
from agentbounty.services.wallet import escrow_address, make_wallet
from agentbounty.settings import Settings

from .platform import Platform, platform_on

ESCROW = Keypair()
HUNTER = str(Keypair().pubkey())
RPC = "https://rpc.example"


class Node:
    """A Solana RPC node: it keeps the transactions it is told about and checks every
    transaction it is asked to send."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []  # newest first, as the real one lists them
        self.transactions: dict[str, dict[str, Any]] = {}
        self.sent: list[Transaction] = []
        self.balance = 5_000_000_000
        self.blockhash = str(Hash.new_unique())
        self.fail: str | None = None
        self.calls: list[str] = []

    def deposit(
        self, signature: str, lamports: int, memo: str | None, *, failed: bool = False
    ) -> None:
        self.entries.insert(
            0,
            {
                "signature": signature,
                "err": {"InstructionError": [0, "Custom"]} if failed else None,
                "memo": None if memo is None else f"[{len(memo)}] {memo}",
            },
        )
        self.transactions[signature] = {
            "meta": {"preBalances": [9, 100], "postBalances": [9 - lamports, 100 + lamports]},
            "transaction": {"message": {"accountKeys": ["SenderAddress", str(ESCROW.pubkey())]}},
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        self.calls.append(method)
        if self.fail == "down":
            raise httpx.ConnectError("no route", request=request)
        if self.fail == method:
            return httpx.Response(200, json={"error": {"code": -32002, "message": "refused"}})

        def result(value: Any) -> httpx.Response:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": value})

        if method == "getBalance":
            return result({"value": self.balance})
        if method == "getLatestBlockhash":
            return result({"value": {"blockhash": self.blockhash}})
        if method == "getSignaturesForAddress":
            assert params[0] == str(ESCROW.pubkey())
            assert params[1]["commitment"] == "finalized"
            until = params[1].get("until")
            listed = []
            for entry in self.entries:
                if entry["signature"] == until:
                    break
                listed.append(entry)
            return result(listed)
        if method == "getTransaction":
            return result(self.transactions.get(params[0]))
        if method == "sendTransaction":
            self.sent.append(Transaction.from_bytes(base64.b64decode(params[0])))
            return result(str(self.sent[-1].signatures[0]))
        raise AssertionError(method)


@pytest.fixture
def node() -> Node:
    return Node()


@pytest.fixture
def wallet(node: Node) -> SolanaWallet:
    return SolanaWallet(RPC, str(ESCROW), transport=httpx.MockTransport(node.handle))


def test_addresses_and_keys() -> None:
    assert valid_address(HUNTER)
    assert valid_address(f"  {HUNTER} ")
    for bad in ("", "not-an-address", HUNTER[:-4], HUNTER + "1111", "0x" + "ab" * 20, "utest1abc"):
        assert not valid_address(bad), bad
    # A key in either form a Solana tool writes it.
    assert keypair_from(str(ESCROW)).pubkey() == ESCROW.pubkey()
    assert keypair_from(json.dumps(list(bytes(ESCROW)))).pubkey() == ESCROW.pubkey()
    with pytest.raises(ValueError, match="not a Solana secret key"):
        keypair_from("definitely not a key")


def test_money_is_exact_in_either_currency() -> None:
    assert format_amount(1_500_000_000, SOL) == "1.5"
    assert format_amount(1, SOL) == "0.000000001"
    assert format_amount(250_000_000, ZEC) == "2.5"
    assert parse_amount("0.01", SOL) == 10_000_000
    assert parse_amount("0.01", ZEC) == 1_000_000
    with pytest.raises(ValueError, match="at most 9 decimal places"):
        parse_amount("0.0000000001", SOL)
    with pytest.raises(ValueError, match="number of SOL"):
        parse_amount("lots", SOL)


async def test_it_reports_each_finalized_deposit_once_with_its_memo(
    wallet: SolanaWallet, node: Node
) -> None:
    assert await wallet.get_balance() == 5_000_000_000
    first = await wallet.list_incoming(None)
    assert (first.payments, first.cursor) == ((), "")

    node.deposit("sig-1", 110_000_000, "dep-abc")
    node.deposit("sig-2", 5, None)
    node.deposit("sig-failed", 999, "dep-xyz", failed=True)
    node.deposit("sig-out", -1_000, "a payment the wallet itself made")
    page = await wallet.list_incoming(first.cursor)
    # Oldest first. A failed transaction and one that took money out are not deposits.
    assert [(p.txid, p.amount_zatoshi, p.memo) for p in page.payments] == [
        ("sig-1", 110_000_000, "dep-abc"),
        ("sig-2", 5, None),
    ]
    assert all(p.confirmations >= 3 for p in page.payments)
    assert page.cursor == "sig-out"

    # Nothing new: nothing reported again, and the place is kept.
    again = await wallet.list_incoming(page.cursor)
    assert (again.payments, again.cursor) == ((), "sig-out")
    node.deposit("sig-3", 7, "dep-late")
    later = await wallet.list_incoming(again.cursor)
    assert [p.txid for p in later.payments] == ["sig-3"]


async def test_a_payment_is_signed_by_the_escrow_and_carries_the_memo(
    wallet: SolanaWallet, node: Node
) -> None:
    sent = await wallet.send_payment(HUNTER, 250_000_000, "finding 1234")
    assert sent.ok
    (transaction,) = node.sent
    assert sent.txid == str(transaction.signatures[0])
    # Signed by the escrow key, over exactly this message.
    transaction.verify()
    message = transaction.message
    keys = [str(key) for key in message.account_keys]
    assert keys[0] == str(ESCROW.pubkey())
    assert str(message.recent_blockhash) == node.blockhash
    pay, memo = message.instructions
    assert [keys[i] for i in pay.accounts] == [str(ESCROW.pubkey()), HUNTER]
    # The system program's transfer: instruction 2, then the amount, little-endian.
    assert bytes(pay.data) == (2).to_bytes(4, "little") + (250_000_000).to_bytes(8, "little")
    assert keys[memo.program_id_index] == str(MEMO_PROGRAM)
    assert bytes(memo.data) == b"finding 1234"


async def test_a_send_that_fails_says_whether_it_might_have_gone_out(
    wallet: SolanaWallet, node: Node
) -> None:
    refused = await wallet.send_payment("not-an-address", 5, "x")
    assert (refused.ok, refused.ambiguous) == (False, False)
    assert node.calls == []
    assert not (await wallet.send_payment(HUNTER, 5, "x" * 600)).ok

    # The node could not be asked for a blockhash: nothing was signed, so nothing went out.
    node.fail = "getLatestBlockhash"
    early = await wallet.send_payment(HUNTER, 5, "memo")
    assert (early.ok, early.ambiguous, node.sent) == (False, False, [])

    # The node looked at the transaction and said no: definitely not sent.
    node.fail = "sendTransaction"
    said_no = await wallet.send_payment(HUNTER, 5, "memo")
    assert (said_no.ok, said_no.ambiguous) == (False, False)

    # The connection died while sending: it may have gone out, so it must not be repeated.
    class Dying(Node):
        def handle(self, request: httpx.Request) -> httpx.Response:
            if json.loads(request.content)["method"] == "sendTransaction":
                raise httpx.ReadTimeout("gone", request=request)
            return super().handle(request)

    dying = SolanaWallet(RPC, str(ESCROW), transport=httpx.MockTransport(Dying().handle))
    lost = await dying.send_payment(HUNTER, 5, "memo")
    assert (lost.ok, lost.ambiguous) == (False, True)


def solana_settings(base: Settings, **more: Any) -> Settings:
    return base.model_copy(
        update={
            "wallet_backend": "solana",
            "solana_rpc_url": RPC,
            "solana_secret_key": SecretStr(str(ESCROW)),
            "escrow_address": str(ESCROW.pubkey()),
            **more,
        }
    )


def test_settings_for_solana(service_settings: Settings) -> None:
    settings = solana_settings(service_settings)
    assert (settings.currency.symbol, settings.currency.decimals) == ("SOL", 9)
    assert service_settings.currency.symbol == "ZEC"
    assert escrow_address(settings) == str(ESCROW.pubkey())
    assert isinstance(make_wallet(settings), SolanaWallet)
    assert str(ESCROW) not in repr(settings)
    # The key must be the escrow address's own.
    with pytest.raises(ValueError, match="not the key of ESCROW_ADDRESS"):
        make_wallet(solana_settings(service_settings, solana_secret_key=SecretStr(str(Keypair()))))
    with pytest.raises(ValueError, match="not a Solana address"):
        escrow_address(solana_settings(service_settings, escrow_address="utest1abc"))
    base = {
        "app_env": "development",
        "database_url": "postgresql+asyncpg://u@localhost/d",
        "secret_encryption_key": "A" * 43 + "=",
        "model_backend": "fake",
        "wallet_backend": "solana",
    }
    with pytest.raises(ValidationError, match="SOLANA_RPC_URL and ESCROW_ADDRESS"):
        Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
async def site(service_settings: Settings) -> AsyncIterator[Platform]:
    async with platform_on(solana_settings(service_settings)) as platform:
        yield platform


async def test_on_solana_the_site_speaks_sol_and_takes_solana_addresses(site: Platform) -> None:
    told = (await site.client.get("/v1/auth/providers")).json()["currency"]
    assert told == {
        "symbol": "SOL",
        "decimals": 9,
        "unit": "lamports",
        "private": False,
        "network": "devnet",
    }
    sponsor = await site.sponsor()
    bounty_id = await sponsor.live()
    body = {"payout_address": HUNTER, "fills": [{"slot": "user_message", "content": "hello"}]}
    assert (
        await site.client.post(f"/v1/bounties/{bounty_id}/attempts", json=body)
    ).status_code == 202
    zcash = {**body, "payout_address": "utest1qqqqqqqq"}
    refused = await site.client.post(f"/v1/bounties/{bounty_id}/attempts", json=zcash)
    assert (refused.status_code, refused.json()["detail"]) == (
        422,
        "payout_address is not a Solana address",
    )
    # A second bounty, to be funded: the deposit goes to the escrow wallet, and a refund
    # address has to be a Solana one too.
    draft = await sponsor.draft()
    funding = await sponsor.call(
        "POST", f"/v1/bounties/{draft}/fund", json={"refund_address": HUNTER}
    )
    assert funding["escrow_address"] == str(ESCROW.pubkey())
    await sponsor.call(
        "POST", f"/v1/bounties/{draft}/fund", 422, json={"refund_address": "utest1qqqq"}
    )
    # A reward too small for the network to deliver to a new wallet is refused up front.
    dust = await sponsor.call("POST", "/v1/bounties", 422, json={"reward_zatoshi": 999_999})
    assert dust["detail"] == "the reward must be at least 0.001 SOL"
    guide = (await site.client.get("/agent.md")).text
    assert "locks a reward in SOL." in guide
    assert "must be a Solana address" in guide
    assert "lamports" in guide


async def test_the_site_still_speaks_zec_by_default(platform: Platform) -> None:
    told = (await platform.client.get("/v1/auth/providers")).json()["currency"]
    assert (told["symbol"], told["unit"], told["private"], told["network"]) == (
        "ZEC",
        "zatoshi",
        True,
        "demo",
    )
