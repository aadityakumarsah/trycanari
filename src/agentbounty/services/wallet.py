"""The wallet, behind three methods.

WalletBackend is everything the rest of the system knows about money on the
chain: read incoming payments, send one, read the balance. FakeWallet is an
in-memory chain for tests and local development. ZcashWallet drives the
zipher-cli light wallet. Replacing zipher-cli with another wallet means
writing another class with the same three methods and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from agentbounty.settings import Settings

Network = Literal["testnet", "mainnet"]

MEMO_MAX_BYTES = 512

# list_incoming re-reports a payment until it is this deep, because its confirmation
# count is still worth watching and a shallow block can be reorganised away.
SETTLED_CONFIRMATIONS = 20


@dataclass(frozen=True)
class IncomingPayment:
    txid: str
    amount_zatoshi: int
    # The memo as text, or None if there was none or it was not text.
    memo: str | None
    # 0 while the transaction is unmined.
    confirmations: int


@dataclass(frozen=True)
class IncomingPage:
    payments: tuple[IncomingPayment, ...]
    # Pass to the next list_incoming call. A payment not yet settled is reported again,
    # with its current confirmations, so callers must treat a txid they know as an update.
    cursor: str


@dataclass(frozen=True)
class SendResult:
    """Exactly one of `txid` and `error` is set."""

    txid: str | None = None
    error: str | None = None
    # True when the payment may have been broadcast even though no txid came back.
    # The caller must not send it again without checking the chain.
    ambiguous: bool = False

    @property
    def ok(self) -> bool:
        return self.txid is not None


class WalletError(RuntimeError):
    """The wallet could not be read."""


class WalletBackend(Protocol):
    async def list_incoming(self, since_cursor: str | None) -> IncomingPage:
        """Incoming shielded payments from the cursor on, oldest first."""
        ...

    async def send_payment(
        self, to_address: str, amount_zatoshi: int, memo_text: str
    ) -> SendResult:
        """Send a shielded payment. Never raises for a failed send: the result says why."""
        ...

    async def get_balance(self) -> int:
        """Spendable shielded balance in zatoshi."""
        ...


# --- memos --------------------------------------------------------------------------------


def encode_memo(text: str) -> str:
    """The memo as hex. Raises ValueError if it does not fit a memo field."""
    data = text.encode()
    if len(data) > MEMO_MAX_BYTES:
        raise ValueError(f"memo is {len(data)} bytes; the limit is {MEMO_MAX_BYTES}")
    return data.hex()


def decode_memo(memo_hex: str) -> str | None:
    """Memo hex as text, without the zero padding a memo field carries.

    None for an empty memo, and for one that is not UTF-8 text (a memo can hold
    arbitrary bytes; those are never one of ours).
    """
    try:
        data = bytes.fromhex(memo_hex).rstrip(b"\x00")
        # 0xF6 followed by zeros is how the protocol writes "no memo".
        if not data or data == b"\xf6":
            return None
        return data.decode()
    except ValueError:
        return None


# --- addresses ----------------------------------------------------------------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32, _BECH32M = 1, 0x2BC830A3
_SAPLING_DATA_CHARACTERS = 75  # 43 bytes in 5-bit groups, plus the 6-character checksum
_UNIFIED_MIN_DATA_CHARACTERS = 60
# Human-readable part -> checksum variant, for each kind of shielded address.
_SHIELDED: dict[Network, dict[str, int]] = {
    "testnet": {"utest": _BECH32M, "ztestsapling": _BECH32},
    "mainnet": {"u": _BECH32M, "zs": _BECH32},
}


def _polymod(values: Sequence[int]) -> int:
    generators = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for index, generator in enumerate(generators):
            if (top >> index) & 1:
                checksum ^= generator
    return checksum


def _expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def bech32_encode(hrp: str, payload: bytes, *, modern: bool) -> str:
    """Bech32 (or Bech32m when `modern`) text for a payload. Used to build fake addresses."""
    bits = "".join(f"{byte:08b}" for byte in payload)
    bits += "0" * (-len(bits) % 5)
    data = [int(bits[i : i + 5], 2) for i in range(0, len(bits), 5)]
    constant = _BECH32M if modern else _BECH32
    polymod = _polymod([*_expand(hrp), *data, 0, 0, 0, 0, 0, 0]) ^ constant
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in [*data, *checksum])


def validate_address(address: str, *, network: Network = "testnet") -> bool:
    """Whether `address` is a well-formed shielded address for the network.

    Accepts unified and Sapling addresses and verifies the checksum, so a typo
    is caught. Transparent addresses are refused: payouts must be shielded.
    It cannot tell whether a unified address holds a shielded receiver; the
    wallet refuses to build the transaction if it does not.
    """
    if not address.isascii() or address != address.lower():
        return False
    hrp, separator, encoded = address.rpartition("1")
    constant = _SHIELDED[network].get(hrp)
    if not separator or constant is None:
        return False
    if any(character not in _BECH32_CHARSET for character in encoded):
        return False
    if constant == _BECH32 and len(encoded) != _SAPLING_DATA_CHARACTERS:
        return False
    if constant == _BECH32M and len(encoded) < _UNIFIED_MIN_DATA_CHARACTERS:
        return False
    data = [_BECH32_CHARSET.index(character) for character in encoded]
    return _polymod([*_expand(hrp), *data]) == constant


def _send_problem(
    to_address: str, amount_zatoshi: int, memo_text: str, network: Network
) -> str | None:
    """Why a payment cannot be sent, checked before any wallet is touched."""
    if not validate_address(to_address, network=network):
        return f"not a shielded {network} address"
    if type(amount_zatoshi) is not int or amount_zatoshi <= 0:
        return "amount must be a positive integer number of zatoshi"
    try:
        encode_memo(memo_text)
    except ValueError as exc:
        return str(exc)
    return None


def _next_cursor(since: int, tip: int) -> str:
    """Everything mined at or below this height is settled and need not be read again."""
    return str(max(since, tip - SETTLED_CONFIRMATIONS + 1, 0))


def _parse_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isdecimal():
        raise ValueError(f"not a wallet cursor: {cursor!r}")
    return int(cursor)


# --- fake ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class OutgoingPayment:
    txid: str
    to_address: str
    amount_zatoshi: int
    memo_text: str
    memo_hex: str


@dataclass
class _FakeDeposit:
    txid: str
    amount_zatoshi: int
    memo_hex: str
    # The block it was mined in, or None while unmined.
    height: int | None


class FakeWallet:
    """An in-memory wallet on a pretend chain. Deterministic, and it never touches a network.

    simulate_deposit() makes a payment arrive; mine() adds blocks; `sent`
    lists what was paid out. Transaction IDs are a hash of a counter, so a
    test sees the same ones on every run.
    """

    def __init__(self, network: Network = "testnet") -> None:
        self.network: Network = network
        hrp = "utest" if network == "testnet" else "u"
        self.address = bech32_encode(hrp, hashlib.sha256(b"fake wallet").digest() * 2, modern=True)
        self.sent: list[OutgoingPayment] = []
        self._deposits: list[_FakeDeposit] = []
        self._height = 1_000
        self._transactions = 0
        self._send_failure: SendResult | None = None

    # --- test controls ---

    def simulate_deposit(
        self, amount_zatoshi: int, memo_text: str = "", confirmations: int = 1
    ) -> str:
        """Make a shielded payment arrive, already `confirmations` deep. Returns its txid."""
        if type(amount_zatoshi) is not int or amount_zatoshi <= 0:
            raise ValueError("amount must be a positive integer number of zatoshi")
        if confirmations < 0:
            raise ValueError("confirmations cannot be negative")
        height = self._height - confirmations + 1 if confirmations else None
        deposit = _FakeDeposit(self._txid(), amount_zatoshi, encode_memo(memo_text), height)
        self._deposits.append(deposit)
        return deposit.txid

    def mine(self, blocks: int = 1) -> None:
        """Add blocks. Unmined deposits are included in the first of them."""
        if blocks < 1:
            raise ValueError("mine at least one block")
        for deposit in self._deposits:
            if deposit.height is None:
                deposit.height = self._height + 1
        self._height += blocks

    def fail_next_send(self, error: str, *, ambiguous: bool = False) -> None:
        """Make the next send_payment report this error instead of sending."""
        self._send_failure = SendResult(error=error, ambiguous=ambiguous)

    # --- WalletBackend ---

    async def list_incoming(self, since_cursor: str | None) -> IncomingPage:
        since = _parse_cursor(since_cursor)
        payments = tuple(
            IncomingPayment(
                deposit.txid,
                deposit.amount_zatoshi,
                decode_memo(deposit.memo_hex),
                self._confirmations(deposit),
            )
            for deposit in sorted(self._deposits, key=lambda d: (d.height is None, d.height or 0))
            if deposit.height is None or deposit.height >= since
        )
        return IncomingPage(payments, _next_cursor(since, self._height))

    async def send_payment(
        self, to_address: str, amount_zatoshi: int, memo_text: str
    ) -> SendResult:
        problem = _send_problem(to_address, amount_zatoshi, memo_text, self.network)
        if problem is not None:
            return SendResult(error=problem)
        if self._send_failure is not None:
            failure, self._send_failure = self._send_failure, None
            return failure
        if amount_zatoshi > await self.get_balance():
            return SendResult(error="insufficient funds")
        payment = OutgoingPayment(
            self._txid(), to_address, amount_zatoshi, memo_text, encode_memo(memo_text)
        )
        self.sent.append(payment)
        return SendResult(txid=payment.txid)

    async def get_balance(self) -> int:
        received = sum(d.amount_zatoshi for d in self._deposits if self._confirmations(d) >= 1)
        return received - sum(payment.amount_zatoshi for payment in self.sent)

    # --- internals ---

    def _confirmations(self, deposit: _FakeDeposit) -> int:
        return 0 if deposit.height is None else self._height - deposit.height + 1

    def _txid(self) -> str:
        self._transactions += 1
        return hashlib.sha256(f"fake-transaction-{self._transactions}".encode()).hexdigest()


# --- zcash, through zipher-cli ------------------------------------------------------------


class _Envelope(BaseModel):
    ok: bool
    data: Any = None
    error: Any = None


class _SyncStatus(BaseModel):
    synced_height: int


class _Balance(BaseModel):
    sapling: int
    orchard: int


class _Transaction(BaseModel):
    txid: str
    # Net change to the wallet in zatoshi: positive is incoming.
    value: int
    memo: str | None = None
    # 0 or absent while unmined.
    height: int | None = None
    expired_unmined: bool = False


class _Sent(BaseModel):
    txid: str


class _Address(BaseModel):
    address: str


def _memo_text(memo: str | None) -> str | None:
    """zipher-cli reports a memo as text. If it is given as hex instead, decode it."""
    if not memo:
        return None
    text = memo.rstrip("\x00")
    if len(text) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in text):
        return decode_memo(text) or text
    return text or None


class ZcashWallet:
    """A Zcash light wallet, driven by the zipher-cli binary in JSON mode.

    Each call runs one short-lived process. The seed stays in zipher-cli's
    encrypted vault; what unlocks it (ZIPHER_VAULT_PASS or ZIPHER_SEED) is
    handed only to the `send confirm` process, the one step that signs.
    """

    def __init__(
        self,
        cli_path: str,
        data_dir: str,
        *,
        network: Network = "testnet",
        server: str | None = None,
        vault_pass: str | None = None,
        seed: str | None = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        self.network: Network = network
        self._command = [cli_path, "--data-dir", data_dir]
        if network == "testnet":
            self._command.append("--testnet")
        if server:
            self._command += ["--server", server]
        self._signing_env = {
            name: value
            for name, value in (("ZIPHER_VAULT_PASS", vault_pass), ("ZIPHER_SEED", seed))
            if value is not None
        }
        self._timeout = timeout_seconds
        # A proposal is one pending slot in the wallet, so sends must not interleave.
        self._sending = asyncio.Lock()

    async def _run(self, *arguments: str, signing: bool = False) -> Any:
        """Run one zipher-cli command and return the `data` of its JSON envelope."""
        # The child gets a minimal environment; in particular no secret it has no use for.
        env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        if signing:
            env.update(self._signing_env)
        # The subcommand only: arguments can hold an address or a memo.
        name = " ".join(arguments[: 2 if arguments[0] in ("send", "sync") else 1])
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise WalletError(f"zipher-cli could not be started: {exc}") from exc
        try:
            async with asyncio.timeout(self._timeout):
                stdout, stderr = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise WalletError(f"zipher-cli {name}: timed out after {self._timeout}s") from None
        output = stdout.decode(errors="replace")
        # Sync progress and engine log lines can come before the JSON envelope.
        start = 0 if output.startswith("{") else output.find("\n{") + 1
        try:
            parsed, _ = json.JSONDecoder().raw_decode(output, start)
            envelope = _Envelope.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError):
            detail = (stderr.decode(errors="replace").strip() or output.strip())[-300:]
            raise WalletError(f"zipher-cli {name}: no JSON result ({detail})") from None
        if not envelope.ok:
            raise WalletError(f"zipher-cli {name}: {envelope.error}")
        return envelope.data

    async def _read[T: BaseModel](self, model: type[T], *arguments: str) -> T:
        data = await self._run(*arguments)
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise WalletError(f"zipher-cli {arguments[0]}: unexpected result") from exc

    async def receive_address(self) -> str:
        """The wallet's own unified address. Not part of WalletBackend."""
        data = await self._run("address")
        try:
            return _Address.model_validate(data[0]).address
        except (ValidationError, LookupError, TypeError) as exc:
            raise WalletError("zipher-cli address: unexpected result") from exc

    async def list_incoming(self, since_cursor: str | None) -> IncomingPage:
        since = _parse_cursor(since_cursor)
        tip = (await self._read(_SyncStatus, "sync", "status")).synced_height
        data = await self._run("transactions", "--limit", "100000")
        try:
            transactions = [_Transaction.model_validate(item) for item in data]
        except (ValidationError, TypeError) as exc:
            raise WalletError("zipher-cli transactions: unexpected result") from exc
        payments = []
        for transaction in transactions:
            mined = bool(transaction.height) and not transaction.expired_unmined
            height = transaction.height or 0
            if transaction.value <= 0 or transaction.expired_unmined or (mined and height < since):
                continue
            payments.append(
                (
                    height if mined else tip + 1,
                    IncomingPayment(
                        transaction.txid,
                        transaction.value,
                        _memo_text(transaction.memo),
                        tip - height + 1 if mined else 0,
                    ),
                )
            )
        payments.sort(key=lambda item: item[0])
        return IncomingPage(tuple(payment for _, payment in payments), _next_cursor(since, tip))

    async def send_payment(
        self, to_address: str, amount_zatoshi: int, memo_text: str
    ) -> SendResult:
        problem = _send_problem(to_address, amount_zatoshi, memo_text, self.network)
        if problem is not None:
            return SendResult(error=problem)
        async with self._sending:
            # Step one builds the transaction and needs no seed. Nothing has left the
            # wallet if it fails.
            try:
                arguments = ["send", "propose", "--to", to_address, "--amount", str(amount_zatoshi)]
                if memo_text:
                    arguments += ["--memo", memo_text]
                await self._run(*arguments)
            except WalletError as exc:
                return SendResult(error=str(exc))
            # Step two signs and broadcasts. If it fails we cannot know how far it got.
            try:
                data = await self._run("send", "confirm", signing=True)
                return SendResult(txid=_Sent.model_validate(data).txid)
            except (WalletError, ValidationError) as exc:
                return SendResult(error=str(exc), ambiguous=True)

    async def get_balance(self) -> int:
        balance = await self._read(_Balance, "balance")
        return balance.sapling + balance.orchard


@dataclass(frozen=True)
class AddressRule:
    """What counts as an address a payment could be sent to, and what to call one."""

    kind: str
    accepts: Callable[[str], bool]


def as_rule(network: Network | AddressRule) -> AddressRule:
    """A rule from either a rule or, as the callers of old pass, a Zcash network."""
    if isinstance(network, AddressRule):
        return network
    return AddressRule(f"shielded {network}", lambda a: validate_address(a, network=network))


def address_rule(settings: Settings) -> AddressRule:
    return AddressRule(address_kind(settings), lambda a: valid_payment_address(settings, a))


def valid_payment_address(settings: Settings, address: str) -> bool:
    """Whether a reward or a refund could be sent to the address, on this deployment."""
    if settings.wallet_backend == "solana":
        from agentbounty.services.solana import valid_address

        return valid_address(address)
    return validate_address(address, network=settings.zcash_network)


def address_kind(settings: Settings) -> str:
    """What to call an address here, when telling someone theirs is not one."""
    if settings.wallet_backend == "solana":
        return "Solana"
    return f"shielded {settings.zcash_network}"


def escrow_address(settings: Settings) -> str:
    """The address sponsors deposit to. Raises ValueError if it is missing or malformed.

    Read from the environment, not from the wallet, so that the API process can
    tell a sponsor where to pay without ever holding a wallet.
    """
    if settings.escrow_address is None:
        if settings.wallet_backend == "fake":
            return FakeWallet(settings.zcash_network).address
        raise ValueError("ESCROW_ADDRESS is not set")
    if not valid_payment_address(settings, settings.escrow_address):
        raise ValueError(f"ESCROW_ADDRESS is not a {address_kind(settings)} address")
    return settings.escrow_address


def make_wallet(settings: Settings) -> WalletBackend:
    """The wallet WALLET_BACKEND selects."""
    if settings.wallet_backend == "fake":
        return FakeWallet(settings.zcash_network)
    if settings.wallet_backend == "solana":
        from agentbounty.services.solana import SolanaWallet

        if settings.solana_rpc_url is None or settings.solana_secret_key is None:
            raise ValueError("WALLET_BACKEND=solana needs SOLANA_RPC_URL and SOLANA_SECRET_KEY")
        wallet = SolanaWallet(
            settings.solana_rpc_url, settings.solana_secret_key.get_secret_value()
        )
        if settings.escrow_address and wallet.address != settings.escrow_address:
            raise ValueError("SOLANA_SECRET_KEY is not the key of ESCROW_ADDRESS")
        return wallet
    if settings.zipher_cli_path is None or settings.zipher_data_dir is None:
        raise ValueError("WALLET_BACKEND=zcash needs ZIPHER_CLI_PATH and ZIPHER_DATA_DIR")
    return ZcashWallet(
        settings.zipher_cli_path,
        settings.zipher_data_dir,
        network=settings.zcash_network,
        server=settings.zipher_server,
        vault_pass=settings.zipher_vault_pass.get_secret_value()
        if settings.zipher_vault_pass
        else None,
        seed=settings.zipher_seed.get_secret_value() if settings.zipher_seed else None,
    )
