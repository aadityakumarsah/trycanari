"""A per-client request limit for the endpoints anyone can call."""

from __future__ import annotations

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


def limit_by_client(request: Request) -> None:
    """Dependency for public endpoints. Behind a proxy, run uvicorn with --proxy-headers so
    the client address is the caller's and not the proxy's."""
    limiter: RateLimiter = request.app.state.rate_limiter
    client = request.client.host if request.client else "unknown"
    if not limiter.allow(client):
        raise HTTPException(
            429, "too many requests; slow down", headers={"Retry-After": str(int(WINDOW_SECONDS))}
        )
