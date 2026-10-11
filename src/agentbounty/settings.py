"""Application settings, read only from environment variables."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ASYNC_DRIVER_PREFIX = "postgresql+asyncpg://"


class SettingsError(RuntimeError):
    """The environment does not hold a usable configuration."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    app_env: Literal["development", "test", "production"]
    # Holds the database password, so it is kept out of repr() and logs.
    database_url: str = Field(repr=False)

    @field_validator("database_url")
    @classmethod
    def _requires_async_driver(cls, value: str) -> str:
        if not value.startswith(ASYNC_DRIVER_PREFIX):
            raise ValueError(f"must start with {ASYNC_DRIVER_PREFIX}")
        return value


def load_settings() -> Settings:
    """Read settings from the environment, or fail naming every variable at fault."""
    try:
        return Settings()
    except ValidationError as exc:
        missing: list[str] = []
        invalid: list[str] = []
        for error in exc.errors(include_input=False, include_url=False):
            name = str(error["loc"][0]).upper()
            if error["type"] == "missing":
                missing.append(name)
            else:
                invalid.append(f"{name}: {error['msg']}")
        problems = []
        if missing:
            problems.append(f"missing required environment variable(s): {', '.join(missing)}")
        if invalid:
            problems.append(f"invalid environment variable(s): {'; '.join(invalid)}")
        raise SettingsError("Configuration error: " + ". ".join(problems)) from None
