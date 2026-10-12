"""Browser sessions for sponsors: who is signed in, sealed in an HttpOnly cookie.

The single-page app cannot be trusted to hold an API key where script can read
it, so signing in exchanges the key for a cookie the browser keeps and script
never sees. There is no session table: the cookie holds the key encrypted
under the service's key, so it is opaque, useless on another deployment, and
expires by itself. A sponsor who signed in with Google has no key in hand, so
their cookie names the sponsor instead.

A cookie is sent by the browser on every request, whoever caused it, so a
request authenticated by cookie must also prove it came from this site.
"""

from __future__ import annotations

import base64
import binascii
import time
import uuid
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.api.ratelimit import limit_by_client
from agentbounty.db import get_transaction
from agentbounty.models import Sponsor
from agentbounty.services import bounty as bounty_service
from agentbounty.services.crypto import Cipher, DecryptionError

COOKIE = "ab_session"
# A session lasts thirty days from when it was last renewed, and it is renewed whenever
# the app is opened more than a day after that. So someone who keeps coming back stays
# signed in, and a browser left alone for a month is signed out.
MAX_AGE_SECONDS = 30 * 24 * 60 * 60
RENEW_AFTER_SECONDS = 24 * 60 * 60
_PURPOSE = "web.session"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def seal(cipher: Cipher, api_key: str) -> str:
    payload = {"key": api_key, "issued": int(time.time())}
    return base64.urlsafe_b64encode(cipher.encrypt_json(payload, purpose=_PURPOSE)).decode()


def seal_sponsor(cipher: Cipher, sponsor_id: uuid.UUID) -> str:
    payload = {"sponsor": str(sponsor_id), "issued": int(time.time())}
    return base64.urlsafe_b64encode(cipher.encrypt_json(payload, purpose=_PURPOSE)).decode()


def _open(cipher: Cipher, cookie: str | None) -> dict[str, Any] | None:
    """What the cookie holds, or None if it is missing, forged or expired."""
    if not cookie:
        return None
    try:
        payload = cipher.decrypt_json(base64.urlsafe_b64decode(cookie), purpose=_PURPOSE)
        if not isinstance(payload, dict) or time.time() - int(payload["issued"]) > MAX_AGE_SECONDS:
            return None
        return payload
    except (DecryptionError, binascii.Error, ValueError, KeyError, TypeError):
        return None


def renewed(cipher: Cipher, cookie: str | None) -> str | None:
    """The same session with its clock restarted, if it is old enough to be worth it."""
    payload = _open(cipher, cookie)
    if payload is None or time.time() - int(payload["issued"]) < RENEW_AFTER_SECONDS:
        return None
    fresh = {**payload, "issued": int(time.time())}
    return base64.urlsafe_b64encode(cipher.encrypt_json(fresh, purpose=_PURPOSE)).decode()


def unseal(cipher: Cipher, cookie: str | None) -> str | None:
    """The API key in the cookie, or None if it holds none or is forged or expired."""
    payload = _open(cipher, cookie)
    key = payload.get("key") if payload else None
    return key if isinstance(key, str) else None


@dataclass(frozen=True)
class Identity:
    """Who a session cookie says the browser is: exactly one of the two is set."""

    api_key: str | None = None
    sponsor_id: uuid.UUID | None = None


def identify(cipher: Cipher, cookie: str | None) -> Identity | None:
    payload = _open(cipher, cookie)
    if payload is None:
        return None
    if isinstance(payload.get("key"), str):
        return Identity(api_key=payload["key"])
    try:
        return Identity(sponsor_id=uuid.UUID(str(payload["sponsor"])))
    except (KeyError, ValueError):
        return None


async def sponsor_of(session: AsyncSession, identity: Identity | None) -> Sponsor | None:
    if identity is None:
        return None
    if identity.api_key is not None:
        return await bounty_service.sponsor_for_key(session, identity.api_key)
    return await session.get(Sponsor, identity.sponsor_id)


def set_session_cookie(request: Request, response: Response, value: str) -> None:
    response.set_cookie(
        COOKIE,
        value,
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.secure_cookies,
        path="/",
    )


def require_same_origin(request: Request) -> None:
    """Refuse a state-changing request that another site made the browser send.

    Browsers say where a request came from in Sec-Fetch-Site, and older ones in
    Origin. A request that carries neither cannot be shown to be ours, so it
    is refused too: scripts authenticate with a bearer key, not a cookie.
    """
    if request.method in _SAFE_METHODS:
        return
    site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    if site is None and origin is None:
        raise HTTPException(403, "cross-site request refused")
    if site is not None and site != "same-origin":
        raise HTTPException(403, "cross-site request refused")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "cross-site request refused")


def session_identity(request: Request) -> Identity | None:
    """Who the signed-in browser is, for a request that is provably from this site."""
    identity = identify(request.app.state.cipher, request.cookies.get(COOKIE))
    if identity is not None:
        require_same_origin(request)
    return identity


router = APIRouter(prefix="/v1", tags=["session"], dependencies=[Depends(require_same_origin)])
Transaction = Annotated[AsyncSession, Depends(get_transaction)]


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=200)


class SessionOut(BaseModel):
    signed_in: bool
    email: str | None = None


@router.get("/session")
async def read_session(request: Request, response: Response, session: Transaction) -> SessionOut:
    """Whether this browser is signed in. The page cannot read the cookie itself.

    The app asks this every time it is opened, which is when a session is renewed.
    """
    cipher: Cipher = request.app.state.cipher
    cookie = request.cookies.get(COOKIE)
    sponsor = await sponsor_of(session, identify(cipher, cookie))
    fresh = renewed(cipher, cookie) if sponsor is not None else None
    if fresh is not None:
        set_session_cookie(request, response, fresh)
    return SessionOut(signed_in=sponsor is not None, email=sponsor.email if sponsor else None)


@router.post("/session", dependencies=[Depends(limit_by_client)])
async def sign_in(
    body: SessionIn, request: Request, response: Response, session: Transaction
) -> SessionOut:
    """Exchange an API key for a session cookie."""
    key = body.api_key.strip()
    sponsor = await bounty_service.sponsor_for_key(session, key)
    if sponsor is None:
        raise HTTPException(401, "that API key was not recognised")
    set_session_cookie(request, response, seal(request.app.state.cipher, key))
    return SessionOut(signed_in=True, email=sponsor.email)


@router.delete("/session")
async def sign_out(response: Response) -> SessionOut:
    response.delete_cookie(COOKIE, path="/")
    return SessionOut(signed_in=False)
