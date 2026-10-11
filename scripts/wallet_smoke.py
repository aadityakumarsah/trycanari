"""Manual smoke test of the real wallet backend on testnet. Run by hand, never in CI.

    WALLET_BACKEND=zcash uv run python scripts/wallet_smoke.py <shielded testnet address>

It prints the wallet's address, shows its spendable balance, and sends
0.001 ZEC with a memo to the address you pass. The wallet needs testnet funds
first: send some to the printed address from a faucet and wait for them to
confirm. The usual settings are read from the environment (see .env.example).
"""

from __future__ import annotations

import asyncio
import sys

from agentbounty.services.wallet import ZcashWallet, make_wallet, validate_address
from agentbounty.settings import load_settings

AMOUNT_ZATOSHI = 100_000  # 0.001 ZEC
MEMO = "agentbounty wallet smoke test"


def say(line: str) -> None:
    sys.stdout.write(line + "\n")


async def main(to_address: str) -> int:
    settings = load_settings()
    if settings.wallet_backend != "zcash" or settings.zcash_network != "testnet":
        say("Refusing to run: set WALLET_BACKEND=zcash and ZCASH_NETWORK=testnet.")
        return 2
    if not validate_address(to_address, network="testnet"):
        say(f"Not a shielded testnet address: {to_address}")
        return 2
    wallet = make_wallet(settings)
    assert isinstance(wallet, ZcashWallet)  # noqa: S101 - the settings check above selected it

    say(f"address:  {await wallet.receive_address()}")
    balance = await wallet.get_balance()
    say(f"balance:  {balance} zatoshi ({balance / 100_000_000:.8f} ZEC) spendable")
    page = await wallet.list_incoming(None)
    say(f"incoming: {len(page.payments)} payment(s); next cursor {page.cursor}")
    for payment in page.payments[-5:]:
        say(
            f"  {payment.txid[:16]}…  {payment.amount_zatoshi} zatoshi"
            f"  {payment.confirmations} confirmation(s)  memo {payment.memo!r}"
        )

    say(f"sending:  {AMOUNT_ZATOSHI} zatoshi to {to_address[:24]}… with memo {MEMO!r}")
    result = await wallet.send_payment(to_address, AMOUNT_ZATOSHI, MEMO)
    if result.ok:
        say(f"sent:     txid {result.txid}")
        return 0
    say(f"failed:   {result.error}")
    if result.ambiguous:
        say(
            "          The payment may still have been broadcast. Check the wallet before retrying."
        )
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        say("usage: wallet_smoke.py <shielded testnet address>")
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
