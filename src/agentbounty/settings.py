"""Application settings, read only from environment variables."""

from __future__ import annotations

import base64
import binascii
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ASYNC_DRIVER_PREFIX = "postgresql+asyncpg://"
SECRET_ENCRYPTION_KEY_BYTES = 32


class SettingsError(RuntimeError):
    """The environment does not hold a usable configuration."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    app_env: Literal["development", "test", "production"]
    # Holds the database password, so it is kept out of repr() and logs.
    database_url: str = Field(repr=False)
    # A connection that supports sessions, for migrations, when DATABASE_URL goes through
    # a transaction-mode pooler. Unset means migrations use DATABASE_URL.
    direct_url: str | None = Field(default=None, repr=False)
    # 32 random bytes, base64. Encrypts system prompts, tool definitions and canary secrets.
    secret_encryption_key: SecretStr

    # "fake" is the deterministic FakeModel; "real" calls the API below.
    model_backend: Literal["fake", "real"]
    # An OpenAI-compatible chat completions endpoint and its key.
    model_api_base: str | None = None
    model_api_key: SecretStr | None = None

    # "fake" is the in-memory FakeWallet; "zcash" drives the zipher-cli light wallet.
    wallet_backend: Literal["fake", "zcash"] = "fake"
    zcash_network: Literal["testnet", "mainnet"] = "testnet"
    # The zipher-cli binary, its wallet directory, and the lightwalletd server it talks to.
    zipher_cli_path: str | None = None
    zipher_data_dir: str | None = None
    zipher_server: str | None = None
    # What unlocks the seed for signing: the vault passphrase, or the seed phrase itself.
    zipher_vault_pass: SecretStr | None = None
    zipher_seed: SecretStr | None = None

    # The shielded address sponsors deposit to: the wallet's own address. Required with
    # WALLET_BACKEND=zcash; the fake wallet has a fixed address of its own.
    escrow_address: str | None = None
    # Confirmations a deposit needs before it is credited.
    deposit_confirmations: int = Field(default=3, ge=1)
    # Spending caps on payouts to hunters, in zatoshi: per payment, and per rolling
    # 24 hours. Unset means no cap; both are required in production.
    payout_max_zatoshi: int | None = Field(default=None, gt=0)
    payout_daily_max_zatoshi: int | None = Field(default=None, gt=0)
    payment_poll_seconds: float = Field(default=30.0, gt=0)
    # With the fake wallet, the payment process makes each awaited deposit arrive by
    # itself, so the whole flow runs locally without a chain.
    fake_wallet_autofund: bool = True

    # The platform's fee, as a fraction of the reward.
    fee_rate: Decimal = Field(default=Decimal("0.1"), ge=0, le=1)

    # Requests a minute one client address may make to the public endpoints.
    rate_limit_per_minute: int = Field(default=120, ge=1)
    # Attempts an hour: per payout address, and per bounty across all hunters.
    attempts_per_address_per_hour: int = Field(default=20, ge=1)
    attempts_per_bounty_per_hour: int = Field(default=60, ge=1)
    # Model tokens a bounty's replays may use in a rolling 24 hours. Once spent, the
    # bounty takes no more attempts until some of that spend ages out.
    bounty_daily_token_budget: int = Field(default=2_000_000, ge=1)

    worker_poll_seconds: float = Field(default=2.0, gt=0)
    # A running attempt older than this is taken to belong to a dead worker and is retried.
    attempt_timeout_seconds: float = Field(default=900.0, gt=0)
    attempt_max_tries: int = Field(default=3, ge=1)

    @field_validator(
        "model_api_base",
        "model_api_key",
        "zipher_cli_path",
        "zipher_data_dir",
        "zipher_server",
        "zipher_seed",
        "direct_url",
        "escrow_address",
        "payout_max_zatoshi",
        "payout_daily_max_zatoshi",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("database_url", "direct_url")
    @classmethod
    def _requires_async_driver(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(ASYNC_DRIVER_PREFIX):
            raise ValueError(f"must start with {ASYNC_DRIVER_PREFIX}")
        return value

    @field_validator("secret_encryption_key")
    @classmethod
    def _requires_a_256_bit_key(cls, value: SecretStr) -> SecretStr:
        try:
            key = base64.b64decode(value.get_secret_value(), validate=True)
        except binascii.Error:
            key = b""
        if len(key) != SECRET_ENCRYPTION_KEY_BYTES:
            raise ValueError(f"must be {SECRET_ENCRYPTION_KEY_BYTES} bytes, base64-encoded")
        return value

    @model_validator(mode="after")
    def _model_backend_is_usable(self) -> Self:
        if self.model_backend == "real" and not (self.model_api_base and self.model_api_key):
            raise ValueError("MODEL_BACKEND=real needs MODEL_API_BASE and MODEL_API_KEY")
        # FakeModel does whatever an attacker's message says, so every attack would be paid.
        if self.model_backend == "fake" and self.app_env == "production":
            raise ValueError("MODEL_BACKEND=fake is not allowed when APP_ENV=production")
        return self

    @model_validator(mode="after")
    def _wallet_backend_is_usable(self) -> Self:
        if self.wallet_backend == "zcash" and not (self.zipher_cli_path and self.zipher_data_dir):
            raise ValueError("WALLET_BACKEND=zcash needs ZIPHER_CLI_PATH and ZIPHER_DATA_DIR")
        if self.wallet_backend == "zcash" and not self.escrow_address:
            raise ValueError("WALLET_BACKEND=zcash needs ESCROW_ADDRESS")
        # A fake wallet in production would record payouts that were never sent.
        if self.wallet_backend == "fake" and self.app_env == "production":
            raise ValueError("WALLET_BACKEND=fake is not allowed when APP_ENV=production")
        if self.app_env == "production" and None in (
            self.payout_max_zatoshi,
            self.payout_daily_max_zatoshi,
        ):
            raise ValueError(
                "APP_ENV=production needs PAYOUT_MAX_ZATOSHI and PAYOUT_DAILY_MAX_ZATOSHI"
            )
        return self

    @property
    def allow_unfunded_activation(self) -> bool:
        """Outside production a bounty may go live without a deposit."""
        return self.app_env != "production"


def load_settings() -> Settings:
    """Read settings from the environment, or fail naming every variable at fault."""
    try:
        return Settings()
    except ValidationError as exc:
        missing: list[str] = []
        invalid: list[str] = []
        for error in exc.errors(include_input=False, include_url=False):
            if error["type"] == "missing":
                missing.append(str(error["loc"][0]).upper())
            elif error["loc"]:
                invalid.append(f"{str(error['loc'][0]).upper()}: {error['msg']}")
            else:
                invalid.append(error["msg"])
        problems = []
        if missing:
            problems.append(f"missing required environment variable(s): {', '.join(missing)}")
        if invalid:
            problems.append(f"invalid environment variable(s): {'; '.join(invalid)}")
        raise SettingsError("Configuration error: " + ". ".join(problems)) from None
