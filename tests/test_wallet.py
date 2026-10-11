from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from agentbounty.services.wallet import (
    MEMO_MAX_BYTES,
    SETTLED_CONFIRMATIONS,
    FakeWallet,
    IncomingPayment,
    OutgoingPayment,
    SendResult,
    WalletBackend,
    WalletError,
    ZcashWallet,
    bech32_encode,
    decode_memo,
    encode_memo,
    make_wallet,
    validate_address,
)
from agentbounty.settings import Settings

# The address of a real testnet wallet made with zipher-cli.
REAL_TESTNET_UNIFIED = (
    "utest1znnw3r0zxy6yy6sg95fuft2hfjzcwxqy8nkwnnas3le9rnj6g2njj2mfue6neu6wjgqeqdlwgp34tzfstl6"
    "zq6780qd6xk6va38fzxfm784c5avgp05qdxxzv309k4wk2pmw4xke5k3sevpgacenahr63dltfk4q6lcs2tg786e7"
    "xeh8s8ddlzahs8l63yc8w0cvyuhf7qf6c9ayue9"
)
TESTNET_SAPLING = bech32_encode("ztestsapling", bytes(range(43)), modern=False)
MAINNET_UNIFIED = bech32_encode("u", bytes(range(64)), modern=True)
MAINNET_SAPLING = bech32_encode("zs", bytes(range(43)), modern=False)
HUNTER = REAL_TESTNET_UNIFIED


# --- addresses ----------------------------------------------------------------------------


@pytest.mark.parametrize("address", [REAL_TESTNET_UNIFIED, TESTNET_SAPLING])
def test_good_testnet_addresses(address: str) -> None:
    assert validate_address(address)
    assert validate_address(address, network="testnet")
    assert not validate_address(address, network="mainnet")


@pytest.mark.parametrize("address", [MAINNET_UNIFIED, MAINNET_SAPLING])
def test_good_mainnet_addresses(address: str) -> None:
    assert validate_address(address, network="mainnet")
    assert not validate_address(address)


BAD_ADDRESSES = [
    ("empty", ""),
    ("not an address", "hello"),
    ("one character changed", REAL_TESTNET_UNIFIED[:-1] + "8"),
    (
        "two characters swapped",
        REAL_TESTNET_UNIFIED[:40]
        + REAL_TESTNET_UNIFIED[41]
        + REAL_TESTNET_UNIFIED[40]
        + REAL_TESTNET_UNIFIED[42:],
    ),
    ("truncated", REAL_TESTNET_UNIFIED[:-4]),
    ("upper case", REAL_TESTNET_UNIFIED.upper()),
    ("mixed case", REAL_TESTNET_UNIFIED[:30] + REAL_TESTNET_UNIFIED[30:].upper()),
    ("surrounding space", f" {REAL_TESTNET_UNIFIED} "),
    ("testnet transparent", "tmEZhbWHTpdKMw5it8YDspUXSMGQyFwovpU"),
    ("mainnet transparent", "t1Hsc1LR8yKnbbe3twRp88p6vFfC5t7DLbs"),
    (
        "sprout",
        "zcU1Cd6zYyZCd2VJF8yKgmzjxdiiU1rgTTjEwoN1CGUWCziPkUTXUjXmX7TMqdMNsTfuiGN1jQoVN4kGxUR4sAPN4XZ7pxb",
    ),
    (
        "sapling checksum on a unified prefix",
        bech32_encode("utest", bytes(range(64)), modern=False),
    ),
    (
        "unified checksum on a sapling prefix",
        bech32_encode("ztestsapling", bytes(range(43)), modern=True),
    ),
    ("sapling of the wrong length", bech32_encode("ztestsapling", bytes(range(42)), modern=False)),
    ("unified that is too short", bech32_encode("utest", bytes(range(8)), modern=True)),
    ("bitcoin", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"),
    ("non-ascii", "utest1" + "é" * 80),
    ("no separator", "utest"),
]


@pytest.mark.parametrize(
    "address", [case[1] for case in BAD_ADDRESSES], ids=[c[0] for c in BAD_ADDRESSES]
)
def test_bad_addresses(address: str) -> None:
    assert not validate_address(address)
    assert not validate_address(address, network="mainnet")


# --- memos --------------------------------------------------------------------------------


def test_memos_are_hex_and_round_trip() -> None:
    assert encode_memo("dep-1a2b") == "6465702d31613262"
    assert decode_memo("6465702d31613262") == "dep-1a2b"
    assert decode_memo(encode_memo("finding ✓ 42")) == "finding ✓ 42"
    # A memo field is padded with zeros to 512 bytes.
    assert decode_memo("6465702d31613262" + "00" * 504) == "dep-1a2b"


@pytest.mark.parametrize(
    "memo_hex",
    ["", "00" * 512, "f6" + "00" * 511, "ff" + "00" * 511, "c328", "not hex"],
    ids=["empty", "all zeros", "the no-memo marker", "arbitrary bytes", "invalid utf-8", "not hex"],
)
def test_memos_that_are_not_text_decode_to_none(memo_hex: str) -> None:
    assert decode_memo(memo_hex) is None


def test_a_memo_must_fit_512_bytes() -> None:
    assert len(encode_memo("x" * MEMO_MAX_BYTES)) == 2 * MEMO_MAX_BYTES
    with pytest.raises(ValueError, match="513 bytes; the limit is 512"):
        encode_memo("x" * (MEMO_MAX_BYTES + 1))
    # The limit is on bytes, not characters.
    with pytest.raises(ValueError, match="514 bytes"):
        encode_memo("é" * 257)


# --- FakeWallet ---------------------------------------------------------------------------


async def test_a_simulated_deposit_is_read_back() -> None:
    wallet = FakeWallet()
    assert await wallet.list_incoming(None) == (await wallet.list_incoming("0"))
    assert (await wallet.list_incoming(None)).payments == ()
    assert await wallet.get_balance() == 0

    txid = wallet.simulate_deposit(1_100_000, "dep-1a2b3c4d", confirmations=3)
    page = await wallet.list_incoming(None)
    assert page.payments == (IncomingPayment(txid, 1_100_000, "dep-1a2b3c4d", 3),)
    assert await wallet.get_balance() == 1_100_000
    assert validate_address(wallet.address)


async def test_deposits_gain_confirmations_as_blocks_are_mined() -> None:
    wallet = FakeWallet()
    txid = wallet.simulate_deposit(500, "dep-x", confirmations=0)
    (pending,) = (await wallet.list_incoming(None)).payments
    assert (pending.txid, pending.confirmations) == (txid, 0)
    # An unmined payment is not spendable.
    assert await wallet.get_balance() == 0

    wallet.mine()
    assert (await wallet.list_incoming(None)).payments[0].confirmations == 1
    assert await wallet.get_balance() == 500
    wallet.mine(4)
    assert (await wallet.list_incoming(None)).payments[0].confirmations == 5


async def test_the_cursor_skips_settled_payments_and_repeats_the_rest() -> None:
    wallet = FakeWallet()
    old = wallet.simulate_deposit(100, "old", confirmations=SETTLED_CONFIRMATIONS + 5)
    recent = wallet.simulate_deposit(200, "recent", confirmations=2)
    unmined = wallet.simulate_deposit(300, None or "", confirmations=0)

    first = await wallet.list_incoming(None)
    assert [p.txid for p in first.payments] == [old, recent, unmined]
    assert first.payments[2].memo is None

    # From the cursor on: the settled payment is gone, the others come again.
    second = await wallet.list_incoming(first.cursor)
    assert [p.txid for p in second.payments] == [recent, unmined]
    assert second.cursor == first.cursor

    # Once they are deep enough they stop being reported, and the cursor has moved on.
    wallet.mine(SETTLED_CONFIRMATIONS + 1)
    third = await wallet.list_incoming(second.cursor)
    assert [(p.txid, p.confirmations) for p in third.payments] == [
        (recent, SETTLED_CONFIRMATIONS + 3),
        (unmined, SETTLED_CONFIRMATIONS + 1),
    ]
    assert int(third.cursor) > int(second.cursor)
    assert (await wallet.list_incoming(third.cursor)).payments == ()
    newest = wallet.simulate_deposit(400, "new", confirmations=1)
    assert [p.txid for p in (await wallet.list_incoming(third.cursor)).payments] == [newest]

    with pytest.raises(ValueError, match="not a wallet cursor"):
        await wallet.list_incoming("yesterday")


async def test_a_sent_payment_is_recorded() -> None:
    wallet = FakeWallet()
    wallet.simulate_deposit(1_000_000, "dep-x")
    result = await wallet.send_payment(HUNTER, 250_000, "finding 42")

    assert result.ok
    assert result == SendResult(txid=result.txid)
    assert wallet.sent == [
        OutgoingPayment(
            txid=result.txid or "",
            to_address=HUNTER,
            amount_zatoshi=250_000,
            memo_text="finding 42",
            memo_hex="66696e64696e67203432",
        )
    ]
    assert await wallet.get_balance() == 750_000
    # A payment out is not a payment in.
    assert len((await wallet.list_incoming(None)).payments) == 1


@pytest.mark.parametrize(
    ("to_address", "amount", "memo", "error"),
    [
        ("t1Hsc1LR8yKnbbe3twRp88p6vFfC5t7DLbs", 100, "", "not a shielded testnet address"),
        (MAINNET_UNIFIED, 100, "", "not a shielded testnet address"),
        (HUNTER, 0, "", "positive integer"),
        (HUNTER, -5, "", "positive integer"),
        (HUNTER, 1.5, "", "positive integer"),
        (HUNTER, True, "", "positive integer"),
        (HUNTER, 100, "x" * 513, "513 bytes; the limit is 512"),
        (HUNTER, 1_000_001, "", "insufficient funds"),
    ],
    ids=[
        "transparent",
        "wrong network",
        "zero",
        "negative",
        "float",
        "bool",
        "memo too long",
        "more than the balance",
    ],
)
async def test_a_send_that_cannot_work_fails_clearly_and_records_nothing(
    to_address: str, amount: Any, memo: str, error: str
) -> None:
    wallet = FakeWallet()
    wallet.simulate_deposit(1_000_000, "dep-x")
    result = await wallet.send_payment(to_address, amount, memo)
    assert (result.ok, result.txid, result.ambiguous) == (False, None, False)
    assert error in (result.error or "")
    assert wallet.sent == []
    assert await wallet.get_balance() == 1_000_000


async def test_a_send_can_be_told_to_fail_once() -> None:
    wallet = FakeWallet()
    wallet.simulate_deposit(1_000, "dep-x")
    wallet.fail_next_send("broadcast timed out", ambiguous=True)
    assert await wallet.send_payment(HUNTER, 100, "") == SendResult(
        error="broadcast timed out", ambiguous=True
    )
    assert wallet.sent == []
    assert (await wallet.send_payment(HUNTER, 100, "")).ok


async def test_the_fake_wallet_is_deterministic() -> None:
    async def scenario() -> tuple[Any, ...]:
        wallet = FakeWallet()
        deposit = wallet.simulate_deposit(900, "dep-x", confirmations=2)
        sent = await wallet.send_payment(HUNTER, 400, "memo")
        return (wallet.address, deposit, sent, await wallet.list_incoming(None))

    assert await scenario() == await scenario()


def test_simulated_deposits_must_be_sane() -> None:
    wallet = FakeWallet()
    for amount in (0, -1, 2.5):
        with pytest.raises(ValueError, match="positive integer"):
            wallet.simulate_deposit(amount)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="negative"):
        wallet.simulate_deposit(5, confirmations=-1)
    with pytest.raises(ValueError, match="at least one"):
        wallet.mine(0)
    with pytest.raises(ValueError, match="513 bytes"):
        wallet.simulate_deposit(5, "x" * 513)


def test_both_backends_have_exactly_the_three_methods() -> None:
    expected = {"list_incoming", "send_payment", "get_balance"}
    declared = {name for name in vars(WalletBackend) if not name.startswith("_")}
    assert declared == expected
    for backend in (FakeWallet, ZcashWallet):
        assert expected <= set(vars(backend))
    fake: WalletBackend = FakeWallet()
    assert isinstance(fake, FakeWallet)


# --- ZcashWallet, against a stand-in for the zipher-cli binary ----------------------------

TIP = 5_000

STUB = '''#!{python}
"""Stands in for zipher-cli: logs how it was called and replays canned JSON."""
import json, os, pathlib, sys

here = pathlib.Path(__file__).parent
arguments = sys.argv[1:]
with open(here / "calls.jsonl", "a") as log:
    log.write(json.dumps({{"argv": arguments, "env": dict(os.environ)}}) + "\\n")
command = " ".join(a for a in arguments if not a.startswith("-") and a not in SKIP)
responses = json.loads((here / "responses.json").read_text())
for key, response in responses.items():
    if command.startswith(key):
        if isinstance(response, dict) and response.get("__raw__") is not None:
            sys.stdout.write(response["__raw__"])
        else:
            print("Wallet is 3 blocks behind. Syncing...")
            print("[engine] synced")
            print(json.dumps(response, indent=2))
        sys.exit(response.get("__exit__", 0) if isinstance(response, dict) else 0)
print(json.dumps({{"ok": False, "error": "unexpected command: " + command}}))
'''


class StubCli:
    """A fake zipher-cli on disk, with the responses a test gives it."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "zipher-cli"
        skip = ["/wallets/escrow", "https://lwd.test:443"]
        self.path.write_text(STUB.format(python=sys.executable).replace("SKIP", repr(skip)))
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR)
        self.respond({})

    def respond(self, responses: dict[str, Any]) -> None:
        defaults = {"sync status": {"ok": True, "data": {"synced_height": TIP, "birthday": 1}}}
        (self.directory / "responses.json").write_text(json.dumps({**defaults, **responses}))

    @property
    def calls(self) -> list[dict[str, Any]]:
        log = self.directory / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def wallet(self, **options: Any) -> ZcashWallet:
        return ZcashWallet(
            str(self.path), "/wallets/escrow", server="https://lwd.test:443", **options
        )


@pytest.fixture
def cli(tmp_path: Path) -> StubCli:
    return StubCli(tmp_path)


def transaction(
    txid: str, value: int, height: int | None, memo: str | None = None, **more: Any
) -> dict[str, Any]:
    return {"txid": txid, "value": value, "height": height, "memo": memo, **more}


async def test_zcash_wallet_reads_the_balance_and_address(cli: StubCli) -> None:
    cli.respond(
        {
            "balance": {
                "ok": True,
                "data": {
                    "transparent": 7,
                    "sapling": 300,
                    "orchard": 4_000,
                    "unconfirmed_orchard": 9,
                },
            },
            "address": {
                "ok": True,
                "data": [{"address": REAL_TESTNET_UNIFIED, "has_orchard": True}],
            },
        }
    )
    wallet = cli.wallet()
    # Shielded and spendable only: transparent and unconfirmed funds are left out.
    assert await wallet.get_balance() == 4_300
    assert await wallet.receive_address() == REAL_TESTNET_UNIFIED
    assert cli.calls[0]["argv"] == [
        "--data-dir",
        "/wallets/escrow",
        "--testnet",
        "--server",
        "https://lwd.test:443",
        "balance",
    ]


async def test_zcash_wallet_lists_incoming_payments(cli: StubCli) -> None:
    cli.respond(
        {
            "transactions": {
                "ok": True,
                "data": [
                    transaction("unmined", 900, 0),
                    transaction("recent", 700, TIP - 2, "dep-recent\x00\x00"),
                    transaction("outgoing", -400, TIP - 3, "finding 42"),
                    transaction("hexmemo", 600, TIP - 9, encode_memo("dep-hex")),
                    transaction("expired", 500, 0, expired_unmined=True),
                    transaction("old", 100, TIP - 100, "dep-old"),
                ],
            }
        }
    )
    wallet = cli.wallet()
    page = await wallet.list_incoming(None)
    assert page.payments == (
        IncomingPayment("old", 100, "dep-old", 101),
        IncomingPayment("hexmemo", 600, "dep-hex", 10),
        IncomingPayment("recent", 700, "dep-recent", 3),
        IncomingPayment("unmined", 900, None, 0),
    )
    assert page.cursor == str(TIP - SETTLED_CONFIRMATIONS + 1)
    # From the cursor on, the settled payment is left out.
    again = await wallet.list_incoming(page.cursor)
    assert [payment.txid for payment in again.payments] == ["hexmemo", "recent", "unmined"]


async def test_zcash_wallet_sends_in_two_steps_and_signs_only_the_second(cli: StubCli) -> None:
    cli.respond(
        {
            "send propose": {"ok": True, "data": {"fee": 10_000}},
            "send confirm": {"ok": True, "data": {"txid": "ab" * 32}},
        }
    )
    wallet = cli.wallet(vault_pass="vault-passphrase", seed="seed words")
    result = await wallet.send_payment(HUNTER, 100_000, "finding 42")
    assert result == SendResult(txid="ab" * 32)

    propose, confirm = cli.calls
    assert propose["argv"][5:] == [
        "send",
        "propose",
        "--to",
        HUNTER,
        "--amount",
        "100000",
        "--memo",
        "finding 42",
    ]
    assert confirm["argv"][5:] == ["send", "confirm"]
    # The proposal needs no seed and gets none. Neither step inherits our environment.
    assert "ZIPHER_VAULT_PASS" not in propose["env"]
    assert "ZIPHER_SEED" not in propose["env"]
    assert (confirm["env"]["ZIPHER_VAULT_PASS"], confirm["env"]["ZIPHER_SEED"]) == (
        "vault-passphrase",
        "seed words",
    )
    for call in cli.calls:
        assert not {"DATABASE_URL", "SECRET_ENCRYPTION_KEY", "MODEL_API_KEY"} & call["env"].keys()


async def test_zcash_wallet_reports_a_failed_proposal_as_safe_to_retry(cli: StubCli) -> None:
    cli.respond({"send propose": {"ok": False, "error": "INSUFFICIENT_FUNDS: have 0, need 110000"}})
    result = await cli.wallet().send_payment(HUNTER, 100_000, "")
    assert (result.txid, result.ambiguous) == (None, False)
    assert "INSUFFICIENT_FUNDS" in (result.error or "")
    # Nothing was signed, and an empty memo is not passed at all.
    assert [call["argv"][5:7] for call in cli.calls] == [["send", "propose"]]
    assert "--memo" not in cli.calls[0]["argv"]


async def test_zcash_wallet_reports_a_failed_confirm_as_ambiguous(cli: StubCli) -> None:
    cli.respond(
        {
            "send propose": {"ok": True, "data": {}},
            "send confirm": {"ok": False, "error": "broadcast failed: connection reset"},
        }
    )
    result = await cli.wallet().send_payment(HUNTER, 100_000, "finding 42")
    assert (result.txid, result.ambiguous) == (None, True)
    assert "connection reset" in (result.error or "")


async def test_zcash_wallet_checks_a_send_before_starting_the_cli(cli: StubCli) -> None:
    wallet = cli.wallet()
    for to_address, amount, memo in (
        ("t1Hsc1LR8yKnbbe3twRp88p6vFfC5t7DLbs", 100, ""),
        (HUNTER, 0, ""),
        (HUNTER, 100, "x" * 513),
    ):
        assert not (await wallet.send_payment(to_address, amount, memo)).ok
    assert cli.calls == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"ok": False, "error": "wallet not found"}, "wallet not found"),
        ({"__raw__": "thread 'main' panicked"}, "no JSON result"),
        ({"ok": True, "data": {"sapling": "many"}}, "unexpected result"),
    ],
    ids=["an error envelope", "no json", "the wrong shape"],
)
async def test_zcash_wallet_read_failures_raise(
    cli: StubCli, response: dict[str, Any], message: str
) -> None:
    cli.respond({"balance": response})
    with pytest.raises(WalletError, match=message):
        await cli.wallet().get_balance()


async def test_zcash_wallet_needs_the_binary_to_exist() -> None:
    wallet = ZcashWallet("/nonexistent/zipher-cli", "/wallets/escrow")
    with pytest.raises(WalletError, match="could not be started"):
        await wallet.get_balance()


async def test_zcash_wallet_on_mainnet_does_not_pass_the_testnet_flag(cli: StubCli) -> None:
    cli.respond({"balance": {"ok": True, "data": {"sapling": 1, "orchard": 2}}})
    assert await cli.wallet(network="mainnet").get_balance() == 3
    assert "--testnet" not in cli.calls[0]["argv"]
    # And it holds payouts to mainnet addresses.
    assert not (await cli.wallet(network="mainnet").send_payment(HUNTER, 5, "")).ok


# --- selection ----------------------------------------------------------------------------


def test_the_wallet_backend_follows_the_settings(settings: Settings) -> None:
    assert settings.wallet_backend == "fake"
    assert isinstance(make_wallet(settings), FakeWallet)

    zcash = settings.model_copy(
        update={
            "wallet_backend": "zcash",
            "zipher_cli_path": "/usr/local/bin/zipher-cli",
            "zipher_data_dir": "/wallets/escrow",
            "zipher_vault_pass": SecretStr("passphrase"),
            "escrow_address": REAL_TESTNET_UNIFIED,
        }
    )
    wallet = make_wallet(zcash)
    assert isinstance(wallet, ZcashWallet)
    assert wallet.network == "testnet"
    with pytest.raises(ValueError, match="ZIPHER_CLI_PATH and ZIPHER_DATA_DIR"):
        make_wallet(settings.model_copy(update={"wallet_backend": "zcash"}))
