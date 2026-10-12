"""Application settings, read only from environment variables."""

from __future__ import annotations

import base64
import binascii
from decimal import Decimal
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentbounty.money import SOL, ZEC, Currency

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

    # "fake" is the in-memory FakeWallet; "zcash" drives the zipher-cli light wallet;
    # "solana" pays in native SOL through a Solana RPC node.
    wallet_backend: Literal["fake", "zcash", "solana"] = "fake"
    # WALLET_BACKEND=solana: the RPC node to use, which cluster it belongs to (for the
    # links and labels people are shown), and the escrow wallet's secret key, as a base58
    # string or the list of numbers in a keypair file.
    solana_rpc_url: str | None = None
    solana_cluster: Literal["devnet", "testnet", "mainnet-beta"] = "devnet"
    solana_secret_key: SecretStr | None = None
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

    # Sign in with Google, for sponsors. Off unless both are set. The client is created
    # in the Google Cloud console, with PUBLIC_URL + /v1/auth/google/callback as its
    # authorised redirect URI.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Where browsers reach this service, such as https://example.com. No path.
    public_url: str | None = None

    # Shared with a proxy in front of the API (the Cloudflare Pages worker). A request that
    # carries it in X-Proxy-Secret is believed about who the real client is. Unset means
    # no proxy is believed.
    proxy_secret: SecretStr | None = None

    # Invite-only mode. Anyone may make an account, but an attack must come from an account
    # that has redeemed a referral code, and it spends credits from a rolling 24-hour
    # allowance. This is what stops a stranger running up the model bill. Off, anyone may
    # attack without an account.
    invite_only: bool = False
    # Where uploaded profile pictures are kept: cloudinary://<api_key>:<api_secret>@<cloud>.
    # Unset, people keep the picture Google or GitHub gives them and cannot upload one.
    cloudinary_url: SecretStr | None = None

    # The owners: email addresses, separated by commas, whose accounts are always admins.
    # It only counts for an account that signs in with Google, which is what proves the
    # address is theirs; signing up with someone's email proves nothing.
    admin_emails: str = ""

    # Credits an account may spend in any 24 hours: at first, and once it has a verified
    # break to its name.
    starter_credits: int = Field(default=8, ge=1)
    proven_credits: int = Field(default=40, ge=1)
    # Extra tokens the first attack on a bounty costs an account, on top of its length
    # price. Attacks on a bounty the account has already engaged cost only the length
    # price, so spreading thin across many bounties is dear and focusing is cheap.
    new_bounty_premium: int = Field(default=2, ge=0)

    # How long the public listing, a bounty's public page and the activity feed may be
    # served from memory. They are the same for everyone, so a few seconds saves most of
    # the database work a busy page would cause. 0 switches the cache off.
    public_cache_seconds: float = Field(default=5.0, ge=0)

    # Model tokens all replays together may use in a rolling 24 hours: the ceiling on the
    # whole platform's model bill, whatever the number of bounties and accounts. Once
    # spent, no bounty takes attempts until some of that spend ages out.
    platform_daily_token_budget: int = Field(default=20_000_000, ge=1)
    # A sponsor may point a bounty at a model endpoint of their own. Those URLs are reached
    # only on the public internet, unless this is set: it exists for trying an endpoint on
    # this machine during development and must stay off anywhere real.
    allow_private_model_endpoints: bool = False

    # Email about breaks and payouts. "none" sends nothing; "fake" logs what it would
    # send; "cloudflare" sends through Cloudflare Email Service, which needs the account,
    # a token allowed to send email for it, and an address on a domain onboarded there.
    # Notifications appear on the site whatever this is.
    email_backend: Literal["none", "fake", "cloudflare"] = "none"
    cloudflare_account_id: str | None = None
    cloudflare_email_token: SecretStr | None = None
    email_from: str | None = None
    email_from_name: str = "trycanari"
    email_poll_seconds: float = Field(default=10.0, gt=0)

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
        "solana_rpc_url",
        "solana_secret_key",
        "direct_url",
        "escrow_address",
        "google_client_id",
        "google_client_secret",
        "public_url",
        "proxy_secret",
        "cloudinary_url",
        "cloudflare_account_id",
        "cloudflare_email_token",
        "email_from",
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
        if self.wallet_backend == "solana" and not (self.solana_rpc_url and self.escrow_address):
            # The secret key is needed only by the payment process, which checks for it
            # itself: the API serves sponsors with the address alone.
            raise ValueError("WALLET_BACKEND=solana needs SOLANA_RPC_URL and ESCROW_ADDRESS")
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

    @model_validator(mode="after")
    def _google_sign_in_is_usable(self) -> Self:
        if (self.google_client_id is None) != (self.google_client_secret is None):
            raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set together")
        if self.public_url is not None:
            parts = urlsplit(self.public_url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("PUBLIC_URL must be an http or https URL")
            if parts.path.strip("/") or parts.query or parts.fragment:
                raise ValueError("PUBLIC_URL must not have a path")
        if self.google_client_id is not None:
            if self.public_url is None:
                raise ValueError("GOOGLE_CLIENT_ID needs PUBLIC_URL")
            # The session cookie is marked Secure in production, so it needs HTTPS.
            if self.app_env == "production" and not self.public_url.startswith("https://"):
                raise ValueError("PUBLIC_URL must be https when APP_ENV=production")
        return self

    @model_validator(mode="after")
    def _email_backend_is_usable(self) -> Self:
        if self.email_backend == "cloudflare":
            if not (self.cloudflare_account_id and self.cloudflare_email_token):
                raise ValueError(
                    "EMAIL_BACKEND=cloudflare needs CLOUDFLARE_ACCOUNT_ID and"
                    " CLOUDFLARE_EMAIL_TOKEN"
                )
            if not self.email_from or "@" not in self.email_from:
                raise ValueError("EMAIL_BACKEND=cloudflare needs EMAIL_FROM, an email address")
            # The emails link back to the site.
            if self.public_url is None:
                raise ValueError("EMAIL_BACKEND=cloudflare needs PUBLIC_URL")
        return self

    @property
    def currency(self) -> Currency:
        """What rewards are paid in, which follows from the wallet."""
        return SOL if self.wallet_backend == "solana" else ZEC

    @property
    def owners(self) -> frozenset[str]:
        return frozenset(
            email.strip().lower() for email in self.admin_emails.split(",") if email.strip()
        )

    @field_validator("cloudinary_url")
    @classmethod
    def _is_a_cloudinary_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            parts = urlsplit(value.get_secret_value())
            if parts.scheme != "cloudinary" or not (
                parts.username and parts.password and parts.hostname
            ):
                raise ValueError("must look like cloudinary://<api_key>:<api_secret>@<cloud_name>")
        return value

    @property
    def secure_cookies(self) -> bool:
        """Whether cookies are marked Secure, so that they are never sent over plain HTTP.
        Always in production, and anywhere the site is known to be served over HTTPS."""
        return self.app_env == "production" or (self.public_url or "").startswith("https://")

    @property
    def google_sign_in(self) -> bool:
        return self.google_client_id is not None

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
