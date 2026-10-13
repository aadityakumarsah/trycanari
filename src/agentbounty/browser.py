"""A browser on the sponsor's own site, for the coding agent in their editor.

Playwright drives a real Chromium page: the agent opens a page, photographs it, reads
it, clicks, fills a box and scrapes repeated rows. It is how an agent sees what a person
sees, which the JSON API cannot show it: that a page renders, what it says, where a
number ends up on the screen.

It never leaves the site the server was pointed at. This is a tool for looking at your
own deployment, not a scraper for the web, and the one page it keeps is the only one.

Playwright is an extra, because it brings a browser with it:

    uv sync --extra browser && uv run playwright install chromium
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlsplit

if TYPE_CHECKING:  # pragma: no cover - for types only; the real import is lazy.
    from playwright.async_api import Browser, BrowserContext, Locator, Page, Playwright

# What the agent is shown of a page it opened or changed.
TEXT_SHOWN = 4_000
# A page that has not settled by now is reported as it stands.
READY_MS = 15_000
# What the page is photographed at. A screenshot's cost to a reader is its size.
WIDE, TALL = 1280, 800
# How far down a full-page photograph goes before it is cut.
TALLEST = 6_000
# How many of the things a word matched are named back when it matched too many.
MANY = 8
# What counts as something a person presses. A tab and an entry in a menu are buttons to
# a reader, and are not buttons to the page, so a word has to be looked for in all of them.
PRESSABLE: tuple[Any, ...] = ("link", "button", "tab", "menuitem", "radio", "checkbox")

# Controls that cannot be taken back, which a click will not reach by accident. The
# text of a page is written by hunters and by other sponsors, so an agent reading it
# can be told to press something; this is the line it cannot be talked across.
IRREVERSIBLE = ("close", "refund", "delete", "remove", "revoke", "ban", "sign out")


class BrowserError(Exception):
    """Something the caller can put right: no browser, a page elsewhere, no such element."""


NO_PLAYWRIGHT = (
    "This needs Playwright, which is an extra because it brings a browser with it."
    " Install it where this server runs:  uv sync --extra browser"
    "  &&  uv run playwright install chromium"
)


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def tidy(text: str) -> str:
    """One line of whatever a control's words came back as."""
    return re.sub(r"\s+", " ", text).strip()


def irreversible(label: str) -> str | None:
    """The word that makes a control one there is no going back from, if it is one."""
    said = label.casefold()
    return next((word for word in IRREVERSIBLE if word in said), None)


@dataclass
class Shown:
    """What a tool reports of the page it left behind."""

    url: str
    title: str
    text: str

    def __str__(self) -> str:
        return f"{self.title}\n{self.url}\n\n{self.text}"


class Site:
    """One browser on one site. Started when it is first wanted, and kept."""

    def __init__(self, base_url: str, *, headless: bool = True) -> None:
        self._base = base_url if base_url.endswith("/") else base_url + "/"
        self._origin = origin_of(self._base)
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def origin(self) -> str:
        return self._origin

    def resolve(self, path: str) -> str:
        """The address a path names on this site. Anywhere else is refused."""
        url = urljoin(self._base, path)
        if origin_of(url) != self._origin:
            raise BrowserError(
                f"{url} is not on {self._origin}. This browser only opens the site it was"
                " given, so that it is a way to look at your own deployment and not at the web."
            )
        return url

    async def page(self) -> Page:
        """The page, with the browser started if this is the first call."""
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as missing:  # pragma: no cover - depends on the install
            raise BrowserError(NO_PLAYWRIGHT) from missing
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=self._headless)
        except Exception as failed:  # pragma: no cover - depends on the install
            await self.aclose()
            raise BrowserError(
                f"Chromium could not be started: {failed}."
                "  Run:  uv run playwright install chromium"
            ) from failed
        self._context = await self._browser.new_context(viewport={"width": WIDE, "height": TALL})
        self._context.set_default_timeout(READY_MS)
        self._page = await self._context.new_page()
        return self._page

    async def sign_in(self, api_key: str) -> str:
        """Give the browser the site's own session cookie, so it sees a sponsor's pages.

        The site refuses a request that cannot be shown to come from itself, which is
        what keeps another site from making a browser act for you. So the key is
        exchanged from the site's own page, by the same call its sign-in page makes.
        The browser is left on the home page; it is never typed into a form."""
        page = await self.page()
        await page.goto(self.resolve("/"), wait_until="domcontentloaded")
        status = await page.evaluate(
            """async ([url, key]) => {
                const answer = await fetch(url, {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({api_key: key}),
                });
                return answer.status;
            }""",
            [self.resolve("v1/session"), api_key],
        )
        if int(status) != 200:
            raise BrowserError(
                f"The site did not accept the API key ({status}). Check TRYCANARI_API_KEY."
            )
        await page.reload(wait_until="domcontentloaded")
        return self._origin

    async def open(self, path: str) -> Shown:
        page = await self.page()
        await page.goto(self.resolve(path), wait_until="domcontentloaded")
        return await self.shown()

    async def shown(self, selector: str | None = None) -> Shown:
        """What the page says now, settled as far as it is going to."""
        page = await self.page()
        await self._settle(page)
        if selector is None:
            text = await page.evaluate("() => (document.body && document.body.innerText) || ''")
        else:
            found = page.locator(selector)
            if await found.count() == 0:
                raise BrowserError(f"nothing on the page matches {selector!r}")
            text = "\n\n".join(await found.all_inner_texts())
        tidied = re.sub(r"\n{3,}", "\n\n", str(text)).strip()
        if len(tidied) > TEXT_SHOWN:
            tidied = f"{tidied[:TEXT_SHOWN]}\n[... {len(tidied) - TEXT_SHOWN} more characters]"
        return Shown(page.url, await page.title(), tidied)

    async def _settle(self, page: Page) -> None:
        """Wait for the app to stop fetching, and carry on if it never does."""
        # A page that keeps polling never goes idle; what it says by then will do.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=READY_MS)

    def _named(self, page: Page, text: str, *, exact: bool) -> Locator:
        """Everything on the page called `text` that a person could press, by the name a
        reader would use for it."""
        first, *rest = PRESSABLE
        found = page.get_by_role(first, name=text, exact=exact)
        for role in rest:
            found = found.or_(page.get_by_role(role, name=text, exact=exact))
        return found

    async def click(self, *, text: str | None, selector: str | None, allow: bool) -> Shown:
        """Press something: a control named by its words, or one a selector picks out.

        Words are matched against what a control is called: the whole of it first, then
        the start of it, then anywhere in it. So 'Breaks' presses the tab called
        'Breaks 1' rather than a card that happens to say 'breaks' lower down. Where that
        is still not enough to tell two things apart, they are named back rather than one
        of them being guessed at."""
        page = await self.page()
        if (text is None) == (selector is None):
            raise BrowserError("give either `text` or `selector`, not both and not neither")
        if selector is not None:
            found = page.locator(selector)
            if await found.count() == 0:
                raise BrowserError(f"nothing to click matches selector {selector!r} on {page.url}")
        else:
            found = self._named(page, text or "", exact=True)
            if await found.count() == 0:
                found = self._named(page, text or "", exact=False)
            how_many = await found.count()
            if how_many == 0:
                raise BrowserError(f"nothing to click matches text {text!r} on {page.url}")
            if how_many > 1:
                names = [tidy(name) for name in await found.all_inner_texts()]
                wanted = tidy(text or "").casefold()
                opens = [at for at, name in enumerate(names) if name.casefold().startswith(wanted)]
                if len(opens) != 1:
                    listed = "; ".join(repr(name) for name in names[:MANY])
                    raise BrowserError(
                        f"{how_many} things on the page are called something like {text!r}"
                        f" ({listed}). Ask for one of them word for word, or give a selector."
                    )
                found = found.nth(opens[0])
        one = found.first
        label = ((await one.inner_text()) if text is None else text).strip()
        word = irreversible(label)
        if word and not allow:
            raise BrowserError(
                f"'{label}' looks like it cannot be taken back (it says {word!r}), so it was"
                " not pressed. Money and what hunters see are a person's to change. If you"
                " are sure, call it again with allow_irreversible set."
            )
        await one.click()
        return await self.shown()

    async def fill(self, selector: str, value: str) -> Shown:
        page = await self.page()
        found = page.locator(selector)
        if await found.count() == 0:
            raise BrowserError(f"no box on the page matches {selector!r}")
        await found.first.fill(value)
        return await self.shown()

    async def screenshot(self, *, full_page: bool) -> bytes:
        page = await self.page()
        await self._settle(page)
        if not full_page:
            return bytes(await page.screenshot(type="png"))
        height = await page.evaluate("() => document.documentElement.scrollHeight")
        if int(height) <= TALLEST:
            return bytes(await page.screenshot(type="png", full_page=True))
        # A very long page is cut rather than handed over whole: what comes back has to
        # be something a reader can take in.
        return bytes(
            await page.screenshot(
                type="png",
                full_page=True,
                clip={"x": 0, "y": 0, "width": float(WIDE), "height": float(TALLEST)},
            )
        )

    async def scrape(self, selector: str, fields: dict[str, str]) -> list[dict[str, Any]]:
        """Every element the selector picks out, as a row.

        A field's value is a selector inside the row, and `@href` or `@title` on the end
        of it takes an attribute instead of the text. With no fields, each row is its
        text."""
        page = await self.page()
        await self._settle(page)
        rows: Any = await page.evaluate(
            """([selector, fields]) => [...document.querySelectorAll(selector)].map((row) => {
                const names = Object.keys(fields);
                if (names.length === 0) return (row.innerText || '').trim();
                const out = {};
                for (const name of names) {
                    const [where, attribute] = String(fields[name]).split('@');
                    const found = where.trim() ? row.querySelector(where.trim()) : row;
                    if (!found) { out[name] = null; continue; }
                    out[name] = attribute
                        ? found.getAttribute(attribute)
                        : (found.innerText || '').trim();
                }
                return out;
            })""",
            [selector, fields],
        )
        if not rows:
            raise BrowserError(f"nothing on the page matches {selector!r}")
        return list(rows)

    async def aclose(self) -> None:
        for shutting in (self._context, self._browser, self._playwright):
            if shutting is None:
                continue
            # Nothing useful follows a failed shutdown: the process is going anyway.
            with contextlib.suppress(Exception):
                closer = getattr(shutting, "close", None) or getattr(shutting, "stop", None)
                if closer is not None:
                    await closer()
        self._playwright = self._browser = self._context = self._page = None


def as_json(rows: list[dict[str, Any]] | list[str]) -> str:
    return json.dumps(rows, indent=2, ensure_ascii=False)
