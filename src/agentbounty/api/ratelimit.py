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

    A strict window makes the response predictable: once the configured number has
    been accepted, the next request gets 429 and a Retry-After header the client can
    obey. Database-backed spend caps remain the cross-process source of truth.
    """

    def __init__(self, per_minute: int) -> None:
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = {}

    def _recent(self, client: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(client, deque())
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        return hits

    def allow(self, client: str, burst: bool = True) -> bool:
        del burst  # Kept for callers that deliberately disable bursts; windows have none.
        now = time.monotonic()
        if len(self._hits) > _MAX_CLIENTS:
            self._cleanup(now)
        hits = self._recent(client, now)
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True

    def _cleanup(self, now: float) -> None:
        """Remove stale entries to prevent memory bloat."""
        self._hits = {
            key: hits
            for key, hits in self._hits.items()
            if hits and now - hits[-1] < WINDOW_SECONDS
        }

    def get_remaining(self, client: str) -> int:
        return max(0, self._limit - len(self._recent(client, time.monotonic())))


def from_our_proxy(request: Request) -> bool:
    """Whether the request came through the site's own proxy, proved by the shared secret."""
    secret = request.app.state.settings.proxy_secret
    offered = request.headers.get("x-proxy-secret")
    return (
        secret is not None
        and offered is not None
        and secrets.compare_digest(offered.encode(), secret.get_secret_value().encode())
    )


def client_address(request: Request) -> str:
    """Who is calling. A proxy that proves itself with the shared secret may say who it
    is calling for; anyone else's claim is ignored, or the limit could be dodged by
    inventing an address per request."""
    claimed = request.headers.get("x-client-address", "").strip()
    if claimed and from_our_proxy(request):
        return claimed[:64]
    return request.client.host if request.client else "unknown"


def visited_host(request: Request) -> str:
    """The host the browser asked for. Behind the site's proxy that is the host the
    proxy was reached at, which it passes on; a stranger's claim to one is ignored."""
    forwarded = request.headers.get("x-forwarded-host", "").strip()
    if forwarded and from_our_proxy(request):
        return forwarded
    return request.headers.get("host", "")


def limit_by_client(request: Request) -> None:
    """Dependency for public endpoints: one allowance per client address."""
    limiter: RateLimiter = request.app.state.rate_limiter
    client = client_address(request)
    if not limiter.allow(client):
        remaining = limiter.get_remaining(client)
        raise HTTPException(
            429,
            "too many requests; slow down",
            headers={
                "Retry-After": str(int(WINDOW_SECONDS)),
                "X-RateLimit-Limit": str(request.app.state.settings.rate_limit_per_minute),
                "X-RateLimit-Remaining": str(max(0, remaining)),
                "X-RateLimit-Reset": str(int(time.time() + WINDOW_SECONDS)),
            },
        )


def limit_by_identity(request: Request, identity: str) -> None:
    """Apply the configured allowance to a stable, already-authenticated identity."""
    limiter: RateLimiter = request.app.state.rate_limiter
    if not limiter.allow(identity, burst=False):
        raise HTTPException(
            429,
            "too many requests for this API key; slow down",
            headers={
                "Retry-After": str(int(WINDOW_SECONDS)),
                "X-RateLimit-Limit": str(request.app.state.settings.rate_limit_per_minute),
                "X-RateLimit-Remaining": str(max(0, limiter.get_remaining(identity))),
                "X-RateLimit-Reset": str(int(time.time() + WINDOW_SECONDS)),
            },
        )
