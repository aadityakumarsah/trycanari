"""Pages. Each one is built from API responses; each form goes through the API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agentbounty.web import session as sessions
from agentbounty.web.api_client import Api, ApiError
from agentbounty.web.formatting import format_zec, parse_zec

PACKAGE = Path(__file__).resolve().parent.parent

templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
templates.env.filters["zec"] = format_zec
# Returns plain text, so the template engine escapes it like any other value.
templates.env.filters["pretty"] = lambda value: json.dumps(value, indent=2, ensure_ascii=False)

SECURITY_HEADERS = {
    # No inline script and nothing from another origin: transcripts are attacker-written.
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none';"
    " form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    # Not "no-referrer": that makes browsers send "Origin: null" on our own form posts,
    # which is indistinguishable from a sandboxed attacker page and has to be refused.
    "Referrer-Policy": "same-origin",
    # Pages can show API keys, status tokens and secrets.
    "Cache-Control": "no-store",
}

DONE = {
    "created": "Draft created. Describe the agent next.",
    "config": "Config saved.",
    "canary": "Canary added.",
    "fund": "Funding started. Send the deposit below.",
    "live": "The bounty is live.",
    "paused": "The bounty is paused.",
    "closing": "Close requested. The unspent escrow is refunded once attempts in flight finish.",
}


class LoginRequiredError(Exception):
    pass


class PageNotFoundError(Exception):
    pass


def same_origin(request: Request) -> None:
    """Refuse a form post that a page on another site made the browser send."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    # Browsers say where a request came from in Sec-Fetch-Site; older ones only in Origin.
    # A client that sends neither is not a browser carrying someone's cookie.
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        raise HTTPException(403, "cross-site request refused")
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "cross-site request refused")


router = APIRouter(include_in_schema=False, dependencies=[Depends(same_origin)])


def api_key(request: Request) -> str | None:
    return sessions.unseal(request.app.state.cipher, request.cookies.get(sessions.COOKIE))


def page(request: Request, name: str, http_status: int = 200, **context: Any) -> HTMLResponse:
    context.setdefault("signed_in", api_key(request) is not None)
    return templates.TemplateResponse(
        request, name, context, status_code=http_status, headers=SECURITY_HEADERS
    )


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def public_api(request: Request) -> Api:
    return Api(request.app)


def sponsor_api(request: Request) -> Api:
    key = api_key(request)
    if key is None:
        raise LoginRequiredError
    return Api(request.app, key)


PublicApi = Annotated[Api, Depends(public_api)]
SponsorApi = Annotated[Api, Depends(sponsor_api)]


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


# --- public -------------------------------------------------------------------------------


@router.get("/")
async def index(request: Request, api: PublicApi) -> HTMLResponse:
    return page(request, "index.html", bounties=await api.call("GET", "/v1/bounties"))


async def _bounty_or_404(request: Request, api: Api, bounty_id: str) -> Any:
    try:
        return await api.call("GET", f"/v1/bounties/{bounty_id}")
    except ApiError as error:
        if error.status in (404, 422):
            raise PageNotFoundError from error
        raise


@router.get("/b/{bounty_id}")
async def bounty_page(request: Request, bounty_id: str, api: PublicApi) -> HTMLResponse:
    return page(request, "bounty.html", bounty=await _bounty_or_404(request, api, bounty_id))


def _settled(status: dict[str, Any]) -> bool:
    """Whether there is nothing more to wait for: judged, and paid if it won."""
    if status["state"] in ("queued", "running"):
        return False
    payout = status.get("payout")
    if status.get("verdict") == "verified":
        return payout is not None and payout["state"] not in ("pending", "sending")
    return True


@router.post("/b/{bounty_id}/attempts")
async def submit_attempt(
    request: Request,
    bounty_id: str,
    api: PublicApi,
    payout_address: Annotated[str, Form()] = "",
    user_message: Annotated[str, Form()] = "",
    document: Annotated[str, Form()] = "",
) -> HTMLResponse:
    fills = [
        {"slot": slot, "content": content}
        for slot, content in (("user_message", user_message), ("document", document))
        if content
    ]
    context: dict[str, Any] = {"bounty_id": bounty_id}
    try:
        if not fills:
            raise ApiError(422, "write a message or a document to submit")
        body = {"payout_address": payout_address.strip(), "fills": fills}
        accepted = await api.call("POST", f"/v1/bounties/{bounty_id}/attempts", json=body)
        token = accepted["status_token"]
        status = await api.call("GET", f"/v1/attempts/{accepted['attempt_id']}", bearer=token)
        context.update(attempt_id=accepted["attempt_id"], token=token, status=status, settled=False)
    except ApiError as error:
        context["error"] = error.detail
    if _is_htmx(request):
        return page(request, "_attempt.html", **context)
    bounty = await _bounty_or_404(request, api, bounty_id)
    failed = 400 if "error" in context else 200
    return page(request, "bounty.html", http_status=failed, bounty=bounty, attempt=context)


@router.post("/attempts/{attempt_id}/status")
async def attempt_status(
    request: Request,
    attempt_id: str,
    api: PublicApi,
    token: Annotated[str, Form()],
    bounty_id: Annotated[str, Form()],
) -> HTMLResponse:
    """The status area. A POST, so the status token travels in the body and not in a URL."""
    context: dict[str, Any] = {"bounty_id": bounty_id, "attempt_id": attempt_id, "token": token}
    try:
        status = await api.call("GET", f"/v1/attempts/{attempt_id}", bearer=token)
        context.update(status=status, settled=_settled(status))
    except ApiError as error:
        context["error"] = error.detail
    if _is_htmx(request):
        return page(request, "_attempt.html", **context)
    bounty = await _bounty_or_404(request, api, bounty_id)
    return page(request, "bounty.html", bounty=bounty, attempt=context)


# --- session ------------------------------------------------------------------------------


def _signed_in(request: Request, key: str, to: Response) -> Response:
    to.set_cookie(
        sessions.COOKIE,
        sessions.seal(request.app.state.cipher, key),
        max_age=sessions.MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.app_env == "production",
    )
    return to


@router.get("/login")
async def login_page(request: Request) -> Response:
    if api_key(request):
        return redirect("/dashboard")
    return page(request, "login.html")


@router.post("/login")
async def login(request: Request, key: Annotated[str, Form()] = "") -> Response:
    try:
        await Api(request.app, key.strip()).call("GET", "/v1/sponsor/bounties")
    except ApiError:
        return page(
            request, "login.html", http_status=401, error="That API key was not recognised."
        )
    return _signed_in(request, key.strip(), redirect("/dashboard"))


@router.post("/signup")
async def signup(request: Request, api: PublicApi, email: Annotated[str, Form()] = "") -> Response:
    try:
        created = await api.call("POST", "/v1/sponsors", json={"email": email.strip()})
    except ApiError as error:
        return page(request, "login.html", http_status=400, signup_error=error.detail, email=email)
    # The key is shown on this one page and nowhere again.
    shown = page(request, "key.html", api_key=created["api_key"], signed_in=True)
    return _signed_in(request, created["api_key"], shown)


@router.post("/logout")
async def logout() -> Response:
    response = redirect("/")
    response.delete_cookie(sessions.COOKIE)
    return response


# --- dashboard ----------------------------------------------------------------------------


@router.get("/dashboard")
async def dashboard(request: Request, api: SponsorApi) -> HTMLResponse:
    return page(request, "dashboard.html", bounties=await api.call("GET", "/v1/sponsor/bounties"))


@router.post("/dashboard/bounties")
async def create_bounty(
    request: Request,
    api: SponsorApi,
    title: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    reward: Annotated[str, Form()] = "",
) -> Response:
    try:
        body = {
            "title": title.strip(),
            "description": description.strip(),
            "reward_zatoshi": parse_zec(reward),
        }
        created = await api.call("POST", "/v1/bounties", json=body)
    except (ApiError, ValueError) as error:
        return page(
            request,
            "dashboard.html",
            http_status=400,
            bounties=await api.call("GET", "/v1/sponsor/bounties"),
            error=str(error),
            draft={"title": title, "description": description, "reward": reward},
        )
    return redirect(f"/dashboard/b/{created['id']}?done=created")


async def _detail_page(
    request: Request, api: Api, bounty_id: str, status: int = 200, **extra: Any
) -> HTMLResponse:
    base = f"/v1/bounties/{bounty_id}"
    try:
        detail = await api.call("GET", f"/v1/sponsor/bounties/{bounty_id}")
    except ApiError as error:
        if error.status in (404, 422):
            raise PageNotFoundError from error
        raise
    return page(
        request,
        "dashboard_bounty.html",
        http_status=status,
        bounty=detail,
        findings=await api.call("GET", f"{base}/findings"),
        ledger=await api.call("GET", f"{base}/ledger"),
        done=DONE.get(request.query_params.get("done", "")),
        **extra,
    )


@router.get("/dashboard/b/{bounty_id}")
async def dashboard_bounty(request: Request, bounty_id: str, api: SponsorApi) -> HTMLResponse:
    return await _detail_page(request, api, bounty_id)


@router.post("/dashboard/b/{bounty_id}/config")
async def save_config(
    request: Request,
    bounty_id: str,
    api: SponsorApi,
    system_prompt: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    tools: Annotated[str, Form()] = "[]",
    params: Annotated[str, Form()] = "{}",
    entry_points: Annotated[list[str] | None, Form()] = None,
) -> Response:
    draft = {
        "system_prompt": system_prompt,
        "model": model,
        "tools": tools,
        "params": params,
        "entry_points": entry_points or [],
    }
    try:
        try:
            body = {
                "system_prompt": system_prompt,
                "model": model.strip(),
                "tools": json.loads(tools or "[]"),
                "params": json.loads(params or "{}"),
                "entry_points": entry_points or [],
            }
        except json.JSONDecodeError as error:
            raise ApiError(422, f"tools and params must be valid JSON ({error.msg})") from None
        await api.call("PUT", f"/v1/bounties/{bounty_id}/config", json=body)
    except ApiError as error:
        return await _detail_page(request, api, bounty_id, 400, error=error.detail, draft=draft)
    return redirect(f"/dashboard/b/{bounty_id}?done=config")


def _rule_value(operator: str, raw: str) -> Any:
    """The value of a predicate as typed into a text box."""
    if operator in ("in", "not_in"):
        return [part.strip() for part in raw.split(",") if part.strip()]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()


@router.post("/dashboard/b/{bounty_id}/canaries")
async def add_canary(
    request: Request,
    bounty_id: str,
    api: SponsorApi,
    kind: Annotated[str, Form()],
    public_label: Annotated[str, Form()] = "",
    secret: Annotated[str, Form()] = "",
    tool: Annotated[str, Form()] = "",
    argument: Annotated[str, Form()] = "",
    operator: Annotated[str, Form()] = "not_in",
    value: Annotated[str, Form()] = "",
) -> Response:
    body: dict[str, Any] = {"type": kind, "public_label": public_label.strip()}
    if kind == "secret_leak":
        body["secret"] = secret
    else:
        body["tool"] = tool.strip()
        if argument.strip():
            body["predicate"] = {
                "argument": argument.strip(),
                "operator": operator,
                "value": _rule_value(operator, value),
            }
    try:
        await api.call("POST", f"/v1/bounties/{bounty_id}/canaries", json=body)
    except ApiError as error:
        return await _detail_page(request, api, bounty_id, 400, error=error.detail)
    return redirect(f"/dashboard/b/{bounty_id}?done=canary")


async def _action(
    request: Request, api: Api, bounty_id: str, path: str, done: str, body: Any = None
) -> Response:
    try:
        await api.call("POST", f"/v1/bounties/{bounty_id}/{path}", json=body)
    except ApiError as error:
        return await _detail_page(request, api, bounty_id, 400, error=error.detail)
    return redirect(f"/dashboard/b/{bounty_id}?done={done}")


@router.post("/dashboard/b/{bounty_id}/fund")
async def fund(
    request: Request, bounty_id: str, api: SponsorApi, refund_address: Annotated[str, Form()] = ""
) -> Response:
    body = {"refund_address": refund_address.strip()}
    return await _action(request, api, bounty_id, "fund", "fund", body)


@router.post("/dashboard/b/{bounty_id}/resume")
async def resume(request: Request, bounty_id: str, api: SponsorApi) -> Response:
    return await _action(request, api, bounty_id, "activate", "live")


@router.post("/dashboard/b/{bounty_id}/pause")
async def pause(request: Request, bounty_id: str, api: SponsorApi) -> Response:
    return await _action(request, api, bounty_id, "pause", "paused")


@router.post("/dashboard/b/{bounty_id}/close")
async def close(
    request: Request, bounty_id: str, api: SponsorApi, refund_address: Annotated[str, Form()] = ""
) -> Response:
    body = {"refund_address": refund_address.strip() or None}
    return await _action(request, api, bounty_id, "close", "closing", body)


def install(app: FastAPI) -> None:
    """Add the pages, the static files and the redirect to the login page."""

    @app.exception_handler(LoginRequiredError)
    async def login_required(_: Request, __: LoginRequiredError) -> RedirectResponse:
        return redirect("/login")

    @app.exception_handler(PageNotFoundError)
    async def not_found(request: Request, _: PageNotFoundError) -> HTMLResponse:
        return page(request, "error.html", http_status=404, message="That bounty does not exist.")

    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    app.include_router(router)
