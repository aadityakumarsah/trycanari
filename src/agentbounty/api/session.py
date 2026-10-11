"""Browser sessions for sponsors: the API key, sealed in an HttpOnly cookie.

The single-page app cannot be trusted to hold an API key where script can read
it, so signing in exchanges the key for a cookie the browser keeps and script
never sees. There is no session table: the cookie holds the key encrypted
under the service's key, so it is opaque, useless on another deployment, and
expires by itself.

A cookie is sent by the browser on every request, whoever caused it, so a
request authenticated by cookie must also prove it came from this site.
"""

from __future__ import annotations

import base64
import binascii
import time
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.api.ratelimit import limit_by_client
from agentbounty.db import get_transaction
from agentbounty.services import bounty as bounty_service
from agentbounty.services.crypto import Cipher, DecryptionError

COOKIE = "ab_session"
MAX_AGE_SECONDS = 12 * 60 * 60
_PURPOSE = "web.session"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def seal(cipher: Cipher, api_key: str) -> str:
    payload = {"key": api_key, "issued": int(time.time())}
    return base64.urlsafe_b64encode(cipher.encrypt_json(payload, purpose=_PURPOSE)).decode()


def unseal(cipher: Cipher, cookie: str | None) -> str | None:
    """The API key in the cookie, or None if it is missing, forged or expired."""
    if not cookie:
        return None
    try:
        payload = cipher.decrypt_json(base64.urlsafe_b64decode(cookie), purpose=_PURPOSE)
        if time.time() - int(payload["issued"]) > MAX_AGE_SECONDS:
            return None
        return str(payload["key"])
    except (DecryptionError, binascii.Error, ValueError, KeyError, TypeError):
        return None


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


def session_key(request: Request) -> str | None:
    """The API key of the signed-in browser, for a request that is provably from this site."""
    key = unseal(request.app.state.cipher, request.cookies.get(COOKIE))
    if key is not None:
        require_same_origin(request)
    return key


router = APIRouter(prefix="/v1", tags=["session"], dependencies=[Depends(require_same_origin)])
Transaction = Annotated[AsyncSession, Depends(get_transaction)]


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=200)


class SessionOut(BaseModel):
    signed_in: bool
    email: str | None = None


@router.get("/session")
async def read_session(request: Request, session: Transaction) -> SessionOut:
    """Whether this browser is signed in. The page cannot read the cookie itself."""
    key = unseal(request.app.state.cipher, request.cookies.get(COOKIE))
    sponsor = await bounty_service.sponsor_for_key(session, key) if key else None
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
    response.set_cookie(
        COOKIE,
        seal(request.app.state.cipher, key),
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",
        secure=request.app.state.settings.app_env == "production",
        path="/",
    )
    return SessionOut(signed_in=True, email=sponsor.email)


@router.delete("/session")
async def sign_out(response: Response) -> SessionOut:
    response.delete_cookie(COOKIE, path="/")
    return SessionOut(signed_in=False)
