"""Sign in with Google, for sponsors.

The authorisation code flow, run entirely on the server: the browser is sent to
Google, Google sends it back with a one-time code, and this service exchanges
the code for an ID token that says who signed in. The browser never sees a
token; it ends up with the same session cookie as any other sponsor.

Three things tie the answer to the browser that asked: a random `state`, a
`nonce` that must come back inside the ID token, and a PKCE verifier. All three
are kept in a short-lived encrypted cookie between the two requests.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.session import seal_sponsor, set_session_cookie
from agentbounty.db import get_transaction
from agentbounty.services import bounty as bounty_service
from agentbounty.services import credits
from agentbounty.services.crypto import Cipher, DecryptionError
from agentbounty.services.errors import ServiceError
from agentbounty.settings import Settings

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an address, not a secret
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
CALLBACK_PATH = "/v1/auth/google/callback"
START_PATH = "/v1/auth/google/start"

STATE_COOKIE = "ab_oauth"
STATE_MAX_AGE_SECONDS = 10 * 60
_PURPOSE = "web.oauth.google"

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["session"])
Transaction = Annotated[AsyncSession, Depends(get_transaction)]


class Providers(BaseModel):
    google: bool
    # Whether attacking needs an account that has redeemed a referral code.
    invite_only: bool


class SignInError(Exception):
    """Signing in failed. `code` is shown to the browser; `reason` goes to the log."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code


def redirect_uri(settings: Settings) -> str:
    return f"{(settings.public_url or '').rstrip('/')}{CALLBACK_PATH}"


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    if not settings.google_sign_in:
        raise HTTPException(404, "not found")
    return settings


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _read_state(cipher: Cipher, cookie: str | None) -> dict[str, Any]:
    try:
        payload = cipher.decrypt_json(base64.urlsafe_b64decode(cookie or ""), purpose=_PURPOSE)
        if time.time() - int(payload["issued"]) > STATE_MAX_AGE_SECONDS:
            raise SignInError("google_state", "the sign-in took too long")
        kept = {key: str(payload[key]) for key in ("state", "nonce", "verifier")}
        return {**kept, "referral": str(payload.get("referral") or "")}
    except (DecryptionError, binascii.Error, ValueError, KeyError, TypeError):
        raise SignInError("google_state", "no usable state cookie") from None


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    name: str | None
    picture_url: str | None


def _picture(value: object) -> str | None:
    """A profile picture's address, if it is one of Google's. Anything else is dropped,
    because the page will only load images from there."""
    if not isinstance(value, str) or len(value) > 1_000:
        return None
    parts = urlsplit(value)
    host = parts.hostname or ""
    return value if parts.scheme == "https" and host.endswith(".googleusercontent.com") else None


def read_id_token(token: object, *, client_id: str, nonce: str) -> GoogleIdentity:
    """The Google account an ID token vouches for: its identifier and verified email,
    with the person's name and picture when Google gives them.

    The token is taken straight from Google's token endpoint over TLS, which is
    what authenticates it: OpenID Connect allows the signature check to be
    skipped in exactly that case. Its claims are still checked in full.
    """
    try:
        _, body, _ = str(token).split(".")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if not isinstance(claims, dict):
            raise ValueError
    except (ValueError, binascii.Error):
        raise SignInError("google_failed", "the ID token could not be read") from None
    if claims.get("iss") not in ISSUERS:
        raise SignInError("google_failed", "the ID token has the wrong issuer")
    if claims.get("aud") != client_id:
        raise SignInError("google_failed", "the ID token is for another client")
    expires = claims.get("exp")
    if not isinstance(expires, int | float) or expires <= time.time():
        raise SignInError("google_failed", "the ID token has expired")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SignInError("google_failed", "the ID token answers a different request")
    subject, email = claims.get("sub"), claims.get("email")
    if not isinstance(subject, str) or not subject:
        raise SignInError("google_failed", "the ID token names no account")
    if not isinstance(email, str) or not email or claims.get("email_verified") is not True:
        raise SignInError("google_unverified", "the account has no verified email")
    name = claims.get("name")
    return GoogleIdentity(
        subject=subject,
        email=email.strip().lower(),
        name=name.strip()[:120] if isinstance(name, str) and name.strip() else None,
        picture_url=_picture(claims.get("picture")),
    )


async def exchange_code(
    settings: Settings, code: str, verifier: str, transport: httpx.AsyncBaseTransport | None
) -> object:
    """Trade the one-time code for an ID token."""
    secret = settings.google_client_secret
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.google_client_id or "",
        "client_secret": secret.get_secret_value() if secret else "",
        "redirect_uri": redirect_uri(settings),
        "code_verifier": verifier,
    }
    try:
        async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
            answer = await client.post(TOKEN_URL, data=form)
        if answer.status_code != 200:
            raise SignInError("google_failed", f"the token endpoint answered {answer.status_code}")
        return answer.json().get("id_token")
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise SignInError(
            "google_failed", f"the token endpoint failed: {type(exc).__name__}"
        ) from None


@router.get("/providers")
async def providers(request: Request) -> Providers:
    """Which ways of signing in this deployment offers, besides an API key."""
    settings: Settings = request.app.state.settings
    return Providers(google=settings.google_sign_in, invite_only=settings.invite_only)


@router.get("/google/start", dependencies=[Depends(limit_by_client)], include_in_schema=False)
async def start(request: Request, code: str | None = None) -> RedirectResponse:
    """Send the browser to Google. `code` is a referral code someone arrived with."""
    settings = _settings(request)
    public = urlsplit(settings.public_url or "")
    referral = (code or "")[:40]
    # Cookies belong to a host. Google answers to PUBLIC_URL, so begin there too.
    if request.headers.get("host") != public.netloc:
        onward = f"{(settings.public_url or '').rstrip('/')}{START_PATH}"
        return RedirectResponse(
            f"{onward}?{urlencode({'code': referral})}" if referral else onward, 307
        )

    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    query = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(settings),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    response = RedirectResponse(f"{AUTHORIZE_URL}?{urlencode(query)}", 302)
    cipher: Cipher = request.app.state.cipher
    sealed = cipher.encrypt_json(
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "referral": referral,
            "issued": int(time.time()),
        },
        purpose=_PURPOSE,
    )
    response.set_cookie(
        STATE_COOKIE,
        base64.urlsafe_b64encode(sealed).decode(),
        max_age=STATE_MAX_AGE_SECONDS,
        httponly=True,
        # Lax, not Strict: the browser must send it when Google navigates back here.
        samesite="lax",
        secure=settings.app_env == "production",
        path="/v1/auth/google",
    )
    return response


@router.get("/google/callback", dependencies=[Depends(limit_by_client)], include_in_schema=False)
async def callback(
    request: Request,
    session: Transaction,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Google sends the browser back here. Sign the sponsor in, or say why not."""
    settings = _settings(request)
    home = (settings.public_url or "").rstrip("/")
    try:
        if error is not None:
            raise SignInError("google_denied", f"Google answered {error[:60]!r}")
        expected = _read_state(request.app.state.cipher, request.cookies.get(STATE_COOKIE))
        if not state or not secrets.compare_digest(state, expected["state"]):
            raise SignInError("google_state", "the state does not match")
        if not code:
            raise SignInError("google_failed", "no code in the answer")
        token = await exchange_code(
            settings, code, expected["verifier"], request.app.state.google_transport
        )
        who = read_id_token(
            token, client_id=settings.google_client_id or "", nonce=expected["nonce"]
        )

        async def admit() -> str | None:
            # Someone new. Anyone may join; a referral code they arrived with is redeemed
            # for them, and one that does not work is simply left for them to enter later.
            if not (settings.invite_only and expected["referral"]):
                return None
            try:
                return await credits.redeem(session, expected["referral"])
            except ServiceError:
                return None

        try:
            sponsor = await bounty_service.sponsor_for_google(
                session, who.subject, who.email, admit
            )
        except ServiceError:
            raise SignInError("google_email_taken", "the email belongs to a key account") from None
        # Kept in step with Google on every sign-in. A name the person chose here stays.
        sponsor.name = sponsor.name or who.name
        sponsor.picture_url = who.picture_url
    except SignInError as failure:
        logger.warning("google sign-in refused: %s", failure)
        response = RedirectResponse(f"{home}/login?error={failure.code}", 303)
    else:
        logger.info("google sign-in", extra={"sponsor_id": str(sponsor.id)})
        response = RedirectResponse(f"{home}/dashboard", 303)
        set_session_cookie(request, response, seal_sponsor(request.app.state.cipher, sponsor.id))
    response.delete_cookie(STATE_COOKIE, path="/v1/auth/google")
    return response
