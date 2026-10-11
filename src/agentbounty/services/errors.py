"""The error every service raises for a request it will not carry out."""

from __future__ import annotations


class ServiceError(Exception):
    """Carries the HTTP status the API should answer with and a message safe to show."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
