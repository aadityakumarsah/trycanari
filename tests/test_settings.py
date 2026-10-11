from __future__ import annotations

import pytest

from agentbounty.services.crypto import generate_key
from agentbounty.settings import SettingsError, load_settings

REQUIRED = ("APP_ENV", "DATABASE_URL", "ENCRYPTION_KEY", "MODEL_BACKEND")


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A complete, valid environment that a test can then break."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:hunter2@localhost/db")
    monkeypatch.setenv("ENCRYPTION_KEY", generate_key())
    monkeypatch.setenv("MODEL_BACKEND", "fake")
    for optional in ("MODEL_API_BASE", "MODEL_API_KEY", "FEE_RATE"):
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
    assert settings.encryption_key.get_secret_value() not in repr(settings)


def test_missing_variables_are_named(environment: pytest.MonkeyPatch) -> None:
    for name in REQUIRED:
        environment.delenv(name)
    with pytest.raises(SettingsError, match="missing required") as raised:
        load_settings()
    assert all(name in str(raised.value) for name in REQUIRED)


def test_invalid_values_are_reported_without_echoing_them(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("APP_ENV", "staging")
    environment.setenv("DATABASE_URL", "postgresql://user:hunter2@localhost/db")
    environment.setenv("ENCRYPTION_KEY", "c2hvcnQtc2VjcmV0")
    with pytest.raises(SettingsError, match="invalid environment") as raised:
        load_settings()
    message = str(raised.value)
    assert "APP_ENV" in message
    assert "DATABASE_URL: Value error, must start with postgresql+asyncpg://" in message
    assert "ENCRYPTION_KEY: Value error, must be 32 bytes, base64-encoded" in message
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
    assert not load_settings().allow_unfunded_activation
