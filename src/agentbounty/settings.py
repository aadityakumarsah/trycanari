"""Application settings, read only from environment variables."""

from __future__ import annotations

import base64
import binascii
from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ASYNC_DRIVER_PREFIX = "postgresql+asyncpg://"
ENCRYPTION_KEY_BYTES = 32


class SettingsError(RuntimeError):
    """The environment does not hold a usable configuration."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    app_env: Literal["development", "test", "production"]
    # Holds the database password, so it is kept out of repr() and logs.
    database_url: str = Field(repr=False)
    # 32 random bytes, base64. Encrypts system prompts, tool definitions and canary secrets.
    encryption_key: SecretStr

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

    # The platform's fee, as a fraction of the reward.
    fee_rate: Decimal = Field(default=Decimal("0.1"), ge=0, le=1)

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
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("database_url")
    @classmethod
    def _requires_async_driver(cls, value: str) -> str:
        if not value.startswith(ASYNC_DRIVER_PREFIX):
            raise ValueError(f"must start with {ASYNC_DRIVER_PREFIX}")
        return value

    @field_validator("encryption_key")
    @classmethod
    def _requires_a_256_bit_key(cls, value: SecretStr) -> SecretStr:
        try:
            key = base64.b64decode(value.get_secret_value(), validate=True)
        except binascii.Error:
            key = b""
        if len(key) != ENCRYPTION_KEY_BYTES:
            raise ValueError(f"must be {ENCRYPTION_KEY_BYTES} bytes, base64-encoded")
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
        # A fake wallet in production would record payouts that were never sent.
        if self.wallet_backend == "fake" and self.app_env == "production":
            raise ValueError("WALLET_BACKEND=fake is not allowed when APP_ENV=production")
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
