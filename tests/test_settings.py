from __future__ import annotations

import pytest

from agentbounty.db import engine_options
from agentbounty.services.crypto import generate_key
from agentbounty.settings import SettingsError, load_settings

REQUIRED = ("APP_ENV", "DATABASE_URL", "SECRET_ENCRYPTION_KEY", "MODEL_BACKEND")


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A complete, valid environment that a test can then break."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:hunter2@localhost/db")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", generate_key())
    monkeypatch.setenv("MODEL_BACKEND", "fake")
    monkeypatch.setenv("WALLET_BACKEND", "fake")
    for optional in ("MODEL_API_BASE", "MODEL_API_KEY", "FEE_RATE", "ZCASH_NETWORK", "DIRECT_URL"):
        monkeypatch.delenv(optional, raising=False)
    for optional in ("ESCROW_ADDRESS", "PAYOUT_MAX_ZATOSHI", "PAYOUT_DAILY_MAX_ZATOSHI"):
        monkeypatch.delenv(optional, raising=False)
    for optional in ("ZIPHER_CLI_PATH", "ZIPHER_DATA_DIR", "ZIPHER_SERVER", "ZIPHER_SEED"):
        monkeypatch.delenv(optional, raising=False)
    return monkeypatch


def test_a_complete_environment_loads(environment: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert (settings.app_env, settings.model_backend, str(settings.fee_rate)) == (
        "test",
        "fake",
        "0.1",
    )
    assert settings.allow_unfunded_activation
    # Secrets stay out of the repr.
    assert "hunter2" not in repr(settings)
    assert settings.secret_encryption_key.get_secret_value() not in repr(settings)


def test_missing_variables_are_named(environment: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        environment.delenv(name)
    with pytest.raises(SettingsError, match="missing required") as raised:
        load_settings()
    assert all(name in str(raised.value) for name in REQUIRED)


def test_invalid_values_are_reported_without_echoing_them(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("APP_ENV", "staging")
    environment.setenv("DATABASE_URL", "postgresql://user:hunter2@localhost/db")
    environment.setenv("SECRET_ENCRYPTION_KEY", "c2hvcnQtc2VjcmV0")
    with pytest.raises(SettingsError, match="invalid environment") as raised:
        load_settings()
    message = str(raised.value)
    assert "APP_ENV" in message
    assert "DATABASE_URL: Value error, must start with postgresql+asyncpg://" in message
    assert "SECRET_ENCRYPTION_KEY: Value error, must be 32 bytes, base64-encoded" in message
    assert "hunter2" not in message
    assert "c2hvcnQtc2VjcmV0" not in message


def test_the_real_model_needs_an_endpoint_and_a_key(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("MODEL_BACKEND", "real")
    with pytest.raises(
        SettingsError, match="MODEL_BACKEND=real needs MODEL_API_BASE and MODEL_API_KEY"
    ):
        load_settings()
    environment.setenv("MODEL_API_BASE", "https://model.test/v1")
    environment.setenv("MODEL_API_KEY", "key-123")
    settings = load_settings()
    assert settings.model_backend == "real"
    assert "key-123" not in repr(settings)


def test_the_fake_model_is_refused_in_production(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("APP_ENV", "production")
    with pytest.raises(SettingsError, match="MODEL_BACKEND=fake is not allowed"):
        load_settings()
    environment.setenv("MODEL_BACKEND", "real")
    environment.setenv("MODEL_API_BASE", "https://model.test/v1")
    environment.setenv("MODEL_API_KEY", "key-123")
    with pytest.raises(SettingsError, match="WALLET_BACKEND=fake is not allowed"):
        load_settings()
    environment.setenv("WALLET_BACKEND", "zcash")
    environment.setenv("ZIPHER_CLI_PATH", "/usr/local/bin/zipher-cli")
    environment.setenv("ZIPHER_DATA_DIR", "/wallets/escrow")
    environment.setenv("ESCROW_ADDRESS", "utest1escrow")
    # Production must cap what the payment process can spend.
    with pytest.raises(SettingsError, match="PAYOUT_MAX_ZATOSHI and PAYOUT_DAILY_MAX_ZATOSHI"):
        load_settings()
    environment.setenv("PAYOUT_MAX_ZATOSHI", "500000000")
    environment.setenv("PAYOUT_DAILY_MAX_ZATOSHI", "2000000000")
    settings = load_settings()
    assert not settings.allow_unfunded_activation
    assert (settings.payout_max_zatoshi, settings.payout_daily_max_zatoshi) == (
        500_000_000,
        2_000_000_000,
    )


def test_the_wallet_defaults_to_fake_and_zcash_needs_a_binary_and_a_wallet(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.delenv("WALLET_BACKEND")
    settings = load_settings()
    assert (settings.wallet_backend, settings.zcash_network) == ("fake", "testnet")

    environment.setenv("WALLET_BACKEND", "zcash")
    with pytest.raises(SettingsError, match="needs ZIPHER_CLI_PATH and ZIPHER_DATA_DIR"):
        load_settings()
    environment.setenv("ZIPHER_CLI_PATH", "/usr/local/bin/zipher-cli")
    environment.setenv("ZIPHER_DATA_DIR", "/wallets/escrow")
    with pytest.raises(SettingsError, match="needs ESCROW_ADDRESS"):
        load_settings()
    environment.setenv("ESCROW_ADDRESS", "utest1escrow")
    environment.setenv("ZIPHER_VAULT_PASS", "passphrase")
    environment.setenv("ZIPHER_SEED", "abandon ability able")
    settings = load_settings()
    assert settings.wallet_backend == "zcash"
    assert "passphrase" not in repr(settings)
    assert "abandon" not in repr(settings)


def test_a_direct_url_is_optional_and_checked_like_the_main_one(
    environment: pytest.MonkeyPatch,
) -> None:
    assert load_settings().direct_url is None
    environment.setenv("DIRECT_URL", "postgresql://user:hunter2@db.example.com:5432/postgres")
    with pytest.raises(SettingsError, match="DIRECT_URL: Value error, must start with") as raised:
        load_settings()
    assert "hunter2" not in str(raised.value)
    environment.setenv(
        "DIRECT_URL", "postgresql+asyncpg://user:hunter2@db.example.com:5432/postgres"
    )
    settings = load_settings()
    assert settings.direct_url is not None
    assert "hunter2" not in repr(settings)


def test_a_pooled_url_switches_off_prepared_statement_caching() -> None:
    plain, no_arguments = engine_options("postgresql+asyncpg://u:p@localhost:5432/app?ssl=require")
    assert no_arguments == {}
    assert dict(plain.query) == {"ssl": "require"}

    pooled = "postgresql+asyncpg://u:p@pooler.example.com:6543/postgres?pgbouncer=true&ssl=require"
    url, arguments = engine_options(pooled)
    # The marker is ours; the driver must never see it.
    assert dict(url.query) == {"ssl": "require"}
    assert (url.host, url.port) == ("pooler.example.com", 6543)
    assert arguments["statement_cache_size"] == 0
    assert arguments["prepared_statement_cache_size"] == 0
    names = {arguments["prepared_statement_name_func"]() for _ in range(50)}
    assert len(names) == 50
