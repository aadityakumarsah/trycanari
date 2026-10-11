from __future__ import annotations

import pytest

from agentbounty.settings import SettingsError, load_settings


def test_missing_variables_are_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(SettingsError, match="missing required") as raised:
        load_settings()
    assert "APP_ENV" in str(raised.value)
    assert "DATABASE_URL" in str(raised.value)


def test_invalid_values_are_reported_without_echoing_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:hunter2@localhost/db")
    with pytest.raises(SettingsError, match="invalid environment") as raised:
        load_settings()
    message = str(raised.value)
    assert "APP_ENV" in message
    assert "DATABASE_URL: Value error, must start with postgresql+asyncpg://" in message
    assert "hunter2" not in message
