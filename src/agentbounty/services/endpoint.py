"""A sponsor's own model endpoint: where it may point, and how it is reached.

A sponsor can have their agent replayed on a model they host, by giving an
OpenAI-compatible base URL and a key. That makes this service fetch a URL a
stranger chose, so the address is checked twice: when it is saved, for a clear
error, and again at the moment of every connection, against the address that
connection will really use. A name that resolves to a public address when it is
saved and to an internal one later is refused at the second check.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from agentbounty.services.model import ChatMessage, ModelClient, ModelError, ModelParams, RealModel
from agentbounty.settings import Settings

MAX_URL_CHARACTERS = 300
# One replay is five runs of several turns; a slow endpoint must not hold a worker for long.
TIMEOUT_SECONDS = 60.0

Connector = Callable[[str, str], RealModel]


class EndpointError(ValueError):
    """The endpoint cannot be used. The message is written for the sponsor."""


def normalize(base: str, *, allow_private: bool = False) -> str:
    """The base URL in the one form it is stored and hashed in."""
    base = base.strip()
    if len(base) > MAX_URL_CHARACTERS:
        raise EndpointError(f"the endpoint URL is longer than {MAX_URL_CHARACTERS} characters")
    try:
        parts = urlsplit(base)
        port = parts.port
    except ValueError as exc:
        raise EndpointError("the endpoint URL is not a valid URL") from exc
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise EndpointError("the endpoint URL must start with https://")
    if not parts.hostname:
        raise EndpointError("the endpoint URL has no host")
    if parts.username or parts.password:
        raise EndpointError("put the key in the key field, not in the URL")
    if parts.query or parts.fragment:
        raise EndpointError("the endpoint URL must not have a query string or a fragment")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    where = host if port is None else f"{host}:{port}"
    return f"{parts.scheme}://{where}{parts.path.rstrip('/')}"


def is_public(address: str) -> bool:
    """Whether an IP address is one on the public internet."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def _resolve(host: str, port: int) -> list[str]:
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    addresses: list[str] = []
    for info in found:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    return addresses


async def check_reachable_host(base: str, *, allow_private: bool = False) -> None:
    """Refuse a URL whose host does not resolve, or resolves anywhere but the public internet."""
    parts = urlsplit(base)
    host = parts.hostname or ""
    addresses = await _resolve(host, parts.port or (443 if parts.scheme == "https" else 80))
    if not addresses:
        raise EndpointError(f"the host {host} could not be found")
    if not allow_private and not all(is_public(address) for address in addresses):
        raise EndpointError("the endpoint must be on the public internet")


class _PublicOnly(httpcore.AsyncNetworkBackend):
    """Opens connections to public addresses only, and to the address it checked."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await _resolve(host, port)
        if not addresses or not all(is_public(address) for address in addresses):
            raise httpcore.ConnectError("the endpoint is not on the public internet")
        # Connect to the address that was checked, not to the name: a second lookup
        # could answer differently.
        return await self._inner.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix sockets are not reachable from here")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def guarded_client(*, allow_private: bool = False) -> httpx.AsyncClient:
    """An HTTP client for URLs a sponsor chose: no redirects, no proxy from the
    environment, and no connection to an internal address."""
    transport = httpx.AsyncHTTPTransport()
    if not allow_private:
        transport._pool = httpcore.AsyncConnectionPool(  # httpx has no public hook for this
            ssl_context=httpx.create_ssl_context(), network_backend=_PublicOnly()
        )
    return httpx.AsyncClient(
        transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False, trust_env=False
    )


def connector(settings: Settings) -> Connector:
    """How this deployment opens a sponsor's endpoint."""
    allow_private = settings.allow_private_model_endpoints

    def connect(base: str, key: str) -> RealModel:
        return RealModel(base, key, client=guarded_client(allow_private=allow_private))

    return connect


def _hint(error: str) -> str:
    """The likeliest cause of a failed test, in words for the sponsor."""
    if "answered 401" in error or "answered 403" in error:
        return "The endpoint refused the key."
    if "answered 404" in error:
        return "The endpoint does not know this model, or the URL is not the API's base."
    if "answered 429" in error:
        return "The endpoint says it is being asked too often."
    if "request failed" in error:
        return "The endpoint could not be reached."
    if "unexpected body" in error:
        return "The endpoint answered, but not the way an OpenAI-compatible API does."
    return "The endpoint returned an error."


@dataclass(frozen=True)
class TestResult:
    ok: bool
    detail: str
    milliseconds: int


async def test(model: ModelClient, name: str) -> TestResult:
    """Ask the model for one word, to learn whether the URL, key and model name work."""
    started = time.monotonic()
    try:
        response = await model.complete(
            # Room for a model that thinks before it speaks.
            [ChatMessage("user", "Reply with the single word OK.")],
            [],
            ModelParams(name, 256),
        )
    except ModelError as exc:
        detail = f"{_hint(str(exc))} ({exc})"
        return TestResult(False, detail, int((time.monotonic() - started) * 1000))
    said = response.text.strip()[:80]
    detail = f"The model answered: {said}" if said else "The model answered."
    return TestResult(True, detail, int((time.monotonic() - started) * 1000))
