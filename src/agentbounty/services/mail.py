"""Email: what a message says, and the provider that carries it.

Email sits behind `EmailSender`, like the wallet and the model, so the whole system
runs without a provider: `FakeEmail` keeps what it was given, `CloudflareEmail` hands
it to Cloudflare Email Service over its REST API.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from agentbounty.money import Currency, format_amount
from agentbounty.settings import Settings

logger = logging.getLogger(__name__)

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str


class EmailError(Exception):
    """An email that was not accepted. `permanent`: trying again would not help."""

    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


class EmailSender(Protocol):
    async def send(self, email: Email) -> None: ...

    async def aclose(self) -> None: ...


@dataclass
class FakeEmail:
    """Keeps every email it is given, and logs who it was for. Sends nothing."""

    sent: list[Email] = field(default_factory=list)
    # Set by a test to make the next sends fail the way a provider would.
    fail_with: EmailError | None = None

    async def send(self, email: Email) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(email)
        logger.info("email (not sent, fake backend): %s", email.subject, extra={"to": email.to})

    async def aclose(self) -> None:
        return None


class CloudflareEmail:
    """Cloudflare Email Service. The sending domain must be onboarded in the dashboard,
    and the token needs permission to send email for the account."""

    def __init__(
        self,
        account_id: str,
        token: str,
        sender: str,
        sender_name: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{CLOUDFLARE_API}/accounts/{account_id}/email/sending/send"
        self._token = token
        self._from = {"address": sender, "name": sender_name}
        self._client = client or httpx.AsyncClient(timeout=20)

    async def send(self, email: Email) -> None:
        body = {
            "to": email.to,
            "from": self._from,
            "subject": email.subject,
            "text": email.text,
            "html": email.html,
        }
        try:
            response = await self._client.post(
                self._url, json=body, headers={"Authorization": f"Bearer {self._token}"}
            )
        except httpx.HTTPError as exc:
            raise EmailError(f"Cloudflare could not be reached: {exc}", permanent=False) from exc
        status = response.status_code
        try:
            answer: Any = response.json()
        except ValueError:
            answer = None
        if status == 429 or status >= 500:
            raise EmailError(f"Cloudflare answered {status}", permanent=False)
        ok = isinstance(answer, dict) and answer.get("success") is True
        if status >= 400 or not ok:
            errors = answer.get("errors") if isinstance(answer, dict) else None
            raise EmailError(f"Cloudflare refused the email ({status}): {errors}", permanent=True)
        result = answer.get("result") or {}
        if email.to in (result.get("permanent_bounces") or []):
            raise EmailError("the address bounced", permanent=True)

    async def aclose(self) -> None:
        await self._client.aclose()


def make_sender(settings: Settings) -> EmailSender | None:
    """The configured sender, or None when this deployment sends no email."""
    if settings.email_backend == "fake":
        return FakeEmail()
    if settings.email_backend == "cloudflare":
        # The settings refuse to load without these three.
        assert settings.cloudflare_account_id is not None  # noqa: S101
        assert settings.cloudflare_email_token is not None  # noqa: S101
        assert settings.email_from is not None  # noqa: S101
        return CloudflareEmail(
            settings.cloudflare_account_id,
            settings.cloudflare_email_token.get_secret_value(),
            settings.email_from,
            settings.email_from_name,
        )
    return None


# --- what the emails say ------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """Everything an email about a notification is written from."""

    kind: str
    recipient_name: str | None
    bounty_id: str
    bounty_title: str
    # The hunter, for an email to the sponsor. None when they have no name or account.
    actor_name: str | None
    amount: int | None
    txid: str | None
    # What broke: the canary's public label, and in how many of how many replays.
    canary: str | None = None
    tripped_runs: int | None = None
    total_runs: int = 5
    # The hunter's GitHub name, for the sponsor. And where the reward goes, for the hunter.
    actor_github: str | None = None
    payout_address: str | None = None
    # The attack as the hunter sent it, for the sponsor it now belongs to: (slot, text).
    attack: tuple[tuple[str, str], ...] = ()
    # The house hunter found it: nothing is paid for it, and the escrow is untouched.
    house: bool = False
    # How many probes of a scan tripped a canary, on a scan_found email.
    tripped_probes: int | None = None


@dataclass(frozen=True)
class _Copy:
    subject: str
    headline: str
    status: str
    body: str
    link: str
    link_label: str


def _copy(event: Event, currency: Currency) -> _Copy:
    # A title is one line wherever it is shown, whatever was typed into it.
    title = " ".join(event.bounty_title.split())
    amount = (
        f"{format_amount(event.amount, currency)} {currency.symbol}"
        if event.amount is not None
        else "the reward"
    )
    hunter = event.actor_name or "A hunter"
    to_hunter = event.actor_name or "a hunter"
    sponsor_page = f"/dashboard/b/{event.bounty_id}"
    if event.kind == "bounty_broken" and event.house:
        return _Copy(
            subject=f"The house hunter broke your agent: {title}",
            headline=f"The house hunter broke {title}",
            status="Verified break, nothing to pay",
            body=(
                "trycanari's own hunter attacks every agent that is listed. One of its"
                " attacks tripped a canary in at least 3 of 5 replays. The house is not"
                " paid: your escrow is untouched and the reward is still there for the"
                " hunters. The bounty is paused so that nobody is paid for a break you"
                " now know of. The attack, every transcript and the evidence are on the"
                " bounty's page. Patch the agent, run the re-arm check, and reopen it."
            ),
            link=f"{sponsor_page}?tab=findings",
            link_label="See every transcript and the evidence",
        )
    if event.kind == "bounty_broken":
        return _Copy(
            subject=f"{hunter} broke your agent: {title}",
            headline=f"{hunter} broke {title}",
            status="Verified break",
            body=(
                f"{hunter} submitted an attack that tripped a canary in at least 3 of 5"
                f" replays. The bounty is paused and takes no more attempts, and {amount}"
                " is being paid to them from its escrow. The attack, every transcript and"
                " the evidence are on the bounty's page. Patch the agent, run the re-arm"
                " check, and reopen it."
            ),
            link=f"{sponsor_page}?tab=findings",
            link_label="See every transcript and the evidence",
        )
    if event.kind == "scan_found":
        probes = event.tripped_probes or 0
        many = "probes" if probes != 1 else "probe"
        return _Copy(
            subject=f"A scan tripped a canary on {title}",
            headline=f"{probes} {many} tripped a canary on {title}",
            status="Found by a scan",
            body=(
                f"The daily scan of {title} ran the baseline playbook and the attacks this"
                f" bounty has already paid for. {probes} of them tripped a canary. A probe"
                " is run once, so this is a lead and not a verified break: nothing is paid,"
                " the bounty is not paused and your escrow is untouched. What each probe"
                " sent and what the agent said are on the bounty's Scan tab."
            ),
            link=f"{sponsor_page}?tab=scan",
            link_label="See what the scan found",
        )
    if event.kind == "reward_paid":
        return _Copy(
            subject=f"Reward paid for {title}",
            headline=f"{amount} was paid to {to_hunter}",
            status="Paid",
            body=(
                f"The reward for the verified break of {title} has left the bounty's escrow."
                " The payment and the platform fee are on the bounty's ledger."
            ),
            link=sponsor_page,
            link_label="Open the ledger",
        )
    if event.kind == "break_verified":
        return _Copy(
            subject=f"Verified: you broke {title}",
            headline=f"You broke {title}",
            status="Verified, payout under review",
            body=(
                "Your attack tripped a canary in at least 3 of 5 replays, so the break is"
                f" verified. The payout of {amount} is now checked against the bounty's"
                " escrow and the platform's spending limits, and then sent to the payout"
                " address you gave. You will get another email when it has been sent."
            ),
            link="/attempts",
            link_label="See your attempt",
        )
    if event.kind == "payout_held":
        return _Copy(
            subject=f"Your payout for {title} is on hold",
            headline="Your payout is on hold",
            status="Payout on hold",
            body=(
                f"Your break of {title} is verified and the reward of {amount} is yours."
                " The payment could not be sent automatically, so a person is looking at"
                " it. Nothing is needed from you, and you will get an email when it has"
                " been sent."
            ),
            link="/attempts",
            link_label="See your attempt",
        )
    return _Copy(
        subject=f"You were paid {amount} for {title}",
        headline=f"{amount} is on its way to you",
        status="Paid",
        body=(
            f"The reward for your verified break of {title} has been sent to your payout address."
        ),
        link="/attempts",
        link_label="See your payout",
    )


_HTML = """\
<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f5f7;\
font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#14171c">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" \
style="max-width:520px;background:#ffffff;border:1px solid #e3e6ea;border-radius:12px">
<tr><td style="padding:28px 28px 8px">
<p style="margin:0 0 18px;font-size:13px;font-weight:700;letter-spacing:.02em">trycanari</p>
<p style="margin:0 0 12px"><span style="display:inline-block;padding:3px 10px;\
border-radius:999px;background:#e6f6ee;color:#0b6b3f;font-size:12px;font-weight:600">\
{status}</span></p>
<h1 style="margin:0 0 12px;font-size:20px;line-height:1.3">{headline}</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.55;color:#3a4350">{greeting}{body}</p>
{facts}
<p style="margin:20px 0 24px"><a href="{link}" style="display:inline-block;padding:10px 18px;\
border-radius:8px;background:#14171c;color:#ffffff;text-decoration:none;font-size:14px;\
font-weight:600">{link_label}</a></p>
</td></tr>
<tr><td style="padding:14px 28px 20px;border-top:1px solid #e3e6ea;font-size:12px;\
line-height:1.5;color:#77808c">You are getting this because of activity on a bounty you \
sponsor or attacked on trycanari.</td></tr>
</table></td></tr></table></body></html>
"""


SLOT_NAMES = {"user_message": "User message", "document": "Document"}
# An email shows the start of a long attack. All of it is on the bounty's page.
ATTACK_SHOWN = 1_500


def _excerpt(text: str) -> str:
    if len(text) <= ATTACK_SHOWN:
        return text
    more = len(text) - ATTACK_SHOWN
    return f"{text[:ATTACK_SHOWN]}\n[... {more:,} more characters on the bounty's page]"


def compose(event: Event, to: str, currency: Currency, public_url: str | None) -> Email:
    """The email for one notification. Everything a person typed is escaped in the HTML."""
    copy = _copy(event, currency)
    link = f"{(public_url or '').rstrip('/')}{copy.link}"
    greeting = f"Hi {event.recipient_name.split()[0]}, " if event.recipient_name else ""
    for_sponsor = event.kind in ("bounty_broken", "reward_paid")
    facts = [("Bounty", event.bounty_title)]
    if for_sponsor:
        who = event.actor_name or "A hunter with no account"
        if event.actor_github:
            who += f" (github.com/{event.actor_github})"
        facts.append(("Broken by", who))
    if event.canary:
        facts.append(("Canary tripped", event.canary))
    if event.tripped_runs is not None:
        facts.append(("Replays tripped", f"{event.tripped_runs} of {event.total_runs}"))
    if event.tripped_probes is not None:
        facts.append(("Probes tripped", str(event.tripped_probes)))
    if event.amount is not None:
        facts.append(("Amount", f"{format_amount(event.amount, currency)} {currency.symbol}"))
    if event.payout_address and not for_sponsor:
        facts.append(("Paid to", event.payout_address))
    if event.txid:
        facts.append(("Transaction", event.txid))
    lines = [f"{greeting}{copy.body}", ""]
    lines += [f"{name}: {value}" for name, value in facts]
    # How it was done is the sponsor's to read, and only in the email about the break.
    attack = event.attack if event.kind == "bounty_broken" else ()
    shown = [(SLOT_NAMES.get(slot, slot), _excerpt(text)) for slot, text in attack]
    for slot, text in shown:
        lines += ["", f"The attack, {slot.lower()}:", text]
    lines += ["", f"{copy.link_label}: {link}"]
    attack_html = "".join(
        '<p style="margin:16px 0 6px;font-size:12px;font-weight:600;letter-spacing:.04em;'
        f'text-transform:uppercase;color:#77808c">The attack: {html.escape(slot)}</p>'
        '<pre style="margin:0;padding:12px;border:1px solid #e3e6ea;border-radius:8px;'
        "background:#f7f8fa;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;"
        f'white-space:pre-wrap;word-break:break-word;color:#14171c">{html.escape(text)}</pre>'
        for slot, text in shown
    )
    rows = "".join(
        '<tr><td style="padding:6px 12px 6px 0;font-size:13px;color:#77808c;'
        f'vertical-align:top">{html.escape(name)}</td>'
        '<td style="padding:6px 0;font-size:13px;word-break:break-all">'
        f"{html.escape(value)}</td></tr>"
        for name, value in facts
    )
    return Email(
        to=to,
        subject=copy.subject,
        text="\n".join(lines),
        html=_HTML.format(
            status=html.escape(copy.status),
            headline=html.escape(copy.headline),
            greeting=html.escape(greeting),
            body=html.escape(copy.body),
            facts=f'<table role="presentation" cellpadding="0" cellspacing="0">{rows}</table>'
            + attack_html,
            link=html.escape(link, quote=True),
            link_label=html.escape(copy.link_label),
        ),
    )
