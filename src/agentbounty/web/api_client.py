"""Calls the JSON API in-process, as the web app's only way to read or change anything."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI


class ApiError(Exception):
    """The API refused a call. `detail` is safe to show to the person who made it."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return response.reason_phrase
    if isinstance(detail, list):
        # Request validation errors: one message per field.
        parts = []
        for item in detail:
            field = ".".join(str(part) for part in item.get("loc", [])[1:])
            parts.append(f"{field}: {item.get('msg', 'invalid')}" if field else item.get("msg", ""))
        return "; ".join(parts)
    return str(detail or response.reason_phrase)


class Api:
    def __init__(self, app: FastAPI, bearer: str | None = None) -> None:
        self._app = app
        self._bearer = bearer

    async def call(
        self, method: str, path: str, *, json: Any = None, bearer: str | None = None
    ) -> Any:
        """Make one API call and return its JSON body. Raises ApiError on a 4xx or 5xx."""
        token = bearer or self._bearer
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api.internal") as client:
            response = await client.request(method, path, json=json, headers=headers)
        if response.is_error:
            raise ApiError(response.status_code, _detail(response))
        return response.json()
