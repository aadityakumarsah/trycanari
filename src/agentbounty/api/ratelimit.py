"""A per-client request limit for the endpoints anyone can call."""

from __future__ import annotations

import secrets
import time
from collections import deque

from fastapi import HTTPException, Request

WINDOW_SECONDS = 60.0
_MAX_CLIENTS = 50_000


class RateLimiter:
    """A sliding window per client address, held in this process's memory.

    With several API processes each keeps its own count, so the effective
    limit is the configured one times the number of processes. The caps that
    protect money and model spend (per payout address, per bounty, the daily
    run budget) are counted in the database instead and hold across processes.
    """

    def __init__(self, per_minute: int) -> None:
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = {}

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        if len(self._hits) > _MAX_CLIENTS:
            self._hits = {
                key: hits for key, hits in self._hits.items() if now - hits[-1] < WINDOW_SECONDS
            }
        hits = self._hits.setdefault(client, deque())
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


def client_address(request: Request) -> str:
    """Who is calling. A proxy that proves itself with the shared secret may say who it
    is calling for; anyone else's claim is ignored, or the limit could be dodged by
    inventing an address per request."""
    secret = request.app.state.settings.proxy_secret
    offered = request.headers.get("x-proxy-secret")
    claimed = request.headers.get("x-client-address", "").strip()
    if (
        secret is not None
        and offered is not None
        and claimed
        and secrets.compare_digest(offered.encode(), secret.get_secret_value().encode())
    ):
        return claimed[:64]
    return request.client.host if request.client else "unknown"


def limit_by_client(request: Request) -> None:
    """Dependency for public endpoints: one allowance per client address."""
    limiter: RateLimiter = request.app.state.rate_limiter
    client = client_address(request)
    if not limiter.allow(client):
        raise HTTPException(
            429, "too many requests; slow down", headers={"Retry-After": str(int(WINDOW_SECONDS))}
        )
