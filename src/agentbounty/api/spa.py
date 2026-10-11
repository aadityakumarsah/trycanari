"""Serves the built single-page app.

The app is static files produced by `bun run build` in web/. Every path that
is not an API route or a file gets index.html, and the router in the browser
takes it from there.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

PACKAGE = Path(__file__).resolve().parent.parent
APP = PACKAGE / "static" / "app"

# Paths the browser router never owns. A miss under these is a real 404.
_RESERVED = ("v1/", "v1", "health", "api-docs", "openapi.json", "app/")

SECURITY_HEADERS = {
    # Script and style only from this origin, and none inline: transcripts are attacker-written.
    # Images may also come from Google, for the profile picture of someone who signed in with it.
    "Content-Security-Policy": "default-src 'self'; img-src 'self' https://*.googleusercontent.com;"
    " frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    # The shell is tiny and names hashed assets, so it must never be served stale.
    "Cache-Control": "no-cache",
}

NOT_BUILT = (
    "The web app has not been built. Run `make web-build` (it needs Bun), or use the"
    " JSON API directly: see /api-docs.\n"
)


class _Assets(StaticFiles):
    """Hashed build output: safe to cache for good."""

    async def get_response(self, path: str, scope: object) -> Response:
        response = await super().get_response(path, scope)  # type: ignore[arg-type]
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def install(app: FastAPI) -> None:
    if APP.is_dir():
        app.mount("/app", _Assets(directory=APP), name="app")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        if path.startswith(_RESERVED):
            raise HTTPException(404, "not found")
        index = APP / "index.html"
        if not index.is_file():
            return PlainTextResponse(NOT_BUILT, status_code=503, headers=SECURITY_HEADERS)
        return FileResponse(index, media_type="text/html", headers=SECURITY_HEADERS)
