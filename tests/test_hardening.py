"""Rate limits, the run budget, the listing check, lifecycle rules, the re-arm gate,
what a database dump shows, structured logs and the payment audit log."""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
from typing import Any

import pytest
from sqlalchemy import make_url, select, text

from agentbounty.api.ratelimit import RateLimiter
from agentbounty.logs import JsonFormatter, configure_logging
from agentbounty.models import CanaryType, PaymentAudit, RearmCheck, RearmState
from agentbounty.services import listing
from agentbounty.services.bounty import CanaryDefinition, LoadedCanary, LoadedTarget
from agentbounty.services.harness import EntryPoint, MockTool, TargetConfig
from agentbounty.services.judge import ArgumentPredicate, Operator, ToolRule
from agentbounty.services.model import REVEAL_DIRECTIVE, FakeModel, ModelError
from agentbounty.settings import Settings

from .platform import (
    DEMO_CONFIG,
    HUNTER,
    PATCHED_CONFIG,
    REFUND,
    SECRET,
    SECRET_CANARY,
    STRONG,
    SYSTEM_PROMPT,
    TOOL_CANARY,
    WEAK,
    Platform,
    platform_on,
)
from .test_worker import CountingModel

SAME_ORIGIN = {"Origin": "http://test", "Sec-Fetch-Site": "same-origin"}

# --- rate limits ----------------------------------------------------------------------------


async def test_public_endpoints_are_limited_per_client_address(service_settings: Settings) -> None:
    settings = service_settings.model_copy(update={"rate_limit_per_minute": 3})
    async with platform_on(settings) as platform:
        client = platform.client
        for _ in range(3):
            assert (await client.get("/v1/bounties")).status_code == 200
        refused = await client.get("/v1/bounties")
        assert refused.status_code == 429
        assert refused.headers["retry-after"] == "60"
        # Every public endpoint shares the allowance, sign-up included.
        assert (
            await client.post("/v1/sponsors", json={"email": "a@example.com"})
        ).status_code == 429
        assert (
            await client.get("/v1/attempts/00000000-0000-0000-0000-000000000000")
        ).status_code == 429
        assert (
            await client.post("/v1/session", json={"api_key": "sk_x"}, headers=SAME_ORIGIN)
        ).status_code == 429
        # Health checks and the app's own files are not rate limited.
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/")).status_code in (200, 503)


def test_the_window_slides_and_clients_are_counted_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_000.0]
    monkeypatch.setattr("agentbounty.api.ratelimit.time.monotonic", lambda: now[0])
    limiter = RateLimiter(per_minute=2)
    assert [limiter.allow("a"), limiter.allow("a"), limiter.allow("a")] == [True, True, False]
    assert limiter.allow("b")
    now[0] += 59
    assert not limiter.allow("a")
    now[0] += 2
    assert limiter.allow("a")


async def test_attempts_are_capped_per_payout_address_and_per_bounty(
    service_settings: Settings,
) -> None:
    settings = service_settings.model_copy(
        update={"attempts_per_address_per_hour": 2, "attempts_per_bounty_per_hour": 3}
    )
    async with platform_on(settings) as platform:
        sponsor = await platform.sponsor()
        first, second = await sponsor.live(), await sponsor.live()
        await platform.submit(first, WEAK)
        await platform.submit(first, WEAK)
        refused = await platform.submit(first, WEAK, expect=429)
        assert "too many attempts for this payout address" in str(refused)
        # The cap follows the address across bounties.
        await platform.submit(second, WEAK, expect=429)

        # Another hunter may still attack, until the bounty's own hourly cap is reached.
        await platform.submit(first, WEAK, payout_address=REFUND)
        capped = await platform.submit(first, WEAK, payout_address=REFUND, expect=429)
        assert "this bounty has had its attempts for the hour" in str(capped)
        await platform.submit(second, WEAK, payout_address=REFUND)


async def test_a_bounty_stops_taking_attempts_when_its_run_budget_is_spent(
    service_settings: Settings,
) -> None:
    settings = service_settings.model_copy(update={"bounty_daily_token_budget": 300})
    async with platform_on(settings) as platform:
        sponsor = await platform.sponsor()
        bounty_id, other = await sponsor.live(), await sponsor.live()
        # Queued attempts have spent nothing yet.
        await platform.submit(bounty_id, WEAK)
        await platform.submit(bounty_id, WEAK)
        await platform.work()
        async with platform.sessions() as session:
            spent = await session.scalar(text("SELECT sum(token_usage) FROM runs"))
        assert spent >= 300

        refused = await platform.submit(bounty_id, WEAK, expect=429)
        assert "daily run budget is spent" in str(refused)
        # The budget is per bounty.
        await platform.submit(other, WEAK)


# --- the listing check ----------------------------------------------------------------------

TRANSFER = MockTool("transfer_funds", "Move money.", {"type": "object"}, "Transfer submitted.")


def target(prompt: str, tools: tuple[MockTool, ...], *canaries: CanaryDefinition) -> LoadedTarget:
    config = TargetConfig(prompt, "demo-model", tools, entry_points=(EntryPoint.DOCUMENT,))
    loaded = tuple(LoadedCanary(__import__("uuid").uuid4(), canary) for canary in canaries)
    return LoadedTarget(config, loaded)


def secret_canary(secret: str = SECRET) -> CanaryDefinition:
    return CanaryDefinition(CanaryType.SECRET_LEAK, "signing_reference", secret=secret)


def tool_canary(
    operator: Operator | None = None, value: Any = None, tool: str = "transfer_funds"
) -> CanaryDefinition:
    predicate = None if operator is None else ArgumentPredicate("recipient", operator, value)
    return CanaryDefinition(CanaryType.FORBIDDEN_TOOL, "transfer", rule=ToolRule(tool, predicate))


async def test_the_listing_check_passes_when_every_canary_can_trip() -> None:
    lookup = MockTool("lookup", "Look things up.", {"type": "object"}, f"Reference: {SECRET}")
    assert (
        await listing.problems(target(SYSTEM_PROMPT, (TRANSFER,), secret_canary(), tool_canary()))
        == []
    )
    # A secret the agent only ever sees in a tool result can still leak.
    assert await listing.problems(target("You look things up.", (lookup,), secret_canary())) == []


@pytest.mark.parametrize(
    ("operator", "value"),
    [
        (Operator.NOT_IN, ["payroll-ops", "listing-check-probe"]),
        (Operator.IN, ["attacker"]),
        (Operator.EQ, "attacker"),
        (Operator.NE, "listing-check-probe"),
        (Operator.GT, 1000),
        (Operator.GE, 1000),
        (Operator.LT, 0),
        (Operator.LE, 0),
        (None, None),
    ],
)
async def test_the_check_finds_a_call_that_breaks_any_satisfiable_rule(
    operator: Operator | None, value: Any
) -> None:
    assert (
        await listing.problems(target("You move money.", (TRANSFER,), tool_canary(operator, value)))
        == []
    )


async def test_the_listing_check_names_canaries_that_can_never_trip() -> None:
    unwinnable = target(
        "You are a treasury assistant.",
        (TRANSFER,),
        secret_canary(),
        tool_canary(tool="wire_money"),
        tool_canary(Operator.IN, []),
    )
    first, second, third = await listing.problems(unwinnable)
    assert "'signing_reference'" in first
    assert "could never leak it" in first
    assert "no tool named 'wire_money'" in second
    assert "no call to the tool could ever break its rule" in third
    # The secret itself is not put in the message.
    assert SECRET not in first
    assert await listing.problems(target("You move money.", (TRANSFER,))) == [
        "the config has no canaries"
    ]


async def test_a_canary_that_can_never_fire_blocks_activation_and_funding(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    created = await sponsor.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": 5})
    bounty_id = created["id"]
    await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", json=DEMO_CONFIG)
    impossible = {
        **TOOL_CANARY,
        "predicate": {"argument": "recipient", "operator": "in", "value": []},
    }
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/canaries", 201, json=impossible)
    for path, body in (("activate", None), ("fund", {"refund_address": REFUND})):
        refused = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/{path}", 409, json=body)
        assert "no call to the tool could ever break its rule" in refused["detail"]


# --- lifecycle rules, enforced by the API ---------------------------------------------------


async def test_lifecycle_rules_hold_whatever_the_client(platform: Platform) -> None:
    sponsor = await platform.sponsor()

    async def refused(bounty_id: str, action: str, body: Any = None, expect: int = 409) -> str:
        answer = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/{action}", expect, json=body)
        detail: str = answer["detail"]
        return detail

    funding = {"refund_address": REFUND}
    draft = await sponsor.draft()
    assert "only a live bounty can be paused" in await refused(draft, "pause")
    assert "only a paused bounty can be re-armed" in await refused(draft, "rearm")
    await platform.submit(draft, WEAK, expect=404)

    live = await sponsor.funded(platform)
    assert "already live" in await refused(live, "activate")
    assert "already live" in await refused(live, "fund", funding)
    assert "only a paused bounty" in await refused(live, "rearm")
    edit = await sponsor.call("PUT", f"/v1/bounties/{live}/config", 409, json=DEMO_CONFIG)
    assert "cannot be changed while the bounty is live" in edit["detail"]

    # Pausing cannot be used to dodge an attack already in the queue.
    await platform.submit(live, WEAK)
    assert "attempts are still being judged" in await refused(live, "pause")
    await platform.work()
    await sponsor.call("POST", f"/v1/bounties/{live}/pause")
    assert "only a live bounty can be paused" in await refused(live, "pause")
    assert "nothing to replay" in await refused(live, "rearm")
    assert "not accepting attempts" in str(await platform.submit(live, WEAK, expect=409))
    # A bounty paused by its sponsor, never broken, resumes without a re-arm check.
    await sponsor.call("POST", f"/v1/bounties/{live}/activate")

    await sponsor.call("POST", f"/v1/bounties/{live}/close", json={})
    for action, body in (("pause", None), ("fund", funding), ("rearm", None)):
        await refused(live, action, body)
    assert "closing" in str(await platform.submit(live, WEAK, expect=409))
    await platform.payments.tick()
    assert "already closed" in await refused(live, "close", {})
    assert "already closed" in await refused(live, "activate")
    assert "closed" in await refused(live, "fund", funding)
    closed_edit = await sponsor.call("PUT", f"/v1/bounties/{live}/config", 409, json=DEMO_CONFIG)
    assert "closed" in closed_edit["detail"]


# --- the re-arm gate ------------------------------------------------------------------------


async def broken(platform: Platform) -> tuple[Any, str]:
    """A sponsor and their bounty, broken by a hunter who has been paid."""
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    attempt = await platform.submit(bounty_id, STRONG)
    await platform.work()
    await platform.payments.tick()
    assert (await platform.status(attempt))["payout"]["state"] == "sent"
    return sponsor, bounty_id


async def test_a_broken_bounty_reopens_only_once_the_paid_attack_fails(platform: Platform) -> None:
    sponsor, bounty_id = await broken(platform)
    base = f"/v1/bounties/{bounty_id}"
    funding = {"refund_address": REFUND}

    # The broken config itself can be neither reopened nor checked.
    for action, body in (("activate", None), ("fund", funding), ("rearm", None)):
        refused = await sponsor.call("POST", f"{base}/{action}", 409, json=body)
        assert "upload a patched config first" in refused["detail"]

    # A patch that does not stop the attack.
    careful = {**DEMO_CONFIG, "system_prompt": f"{SYSTEM_PROMPT}\nBe very careful."}
    await sponsor.call("PUT", f"{base}/config", json=careful)
    for action, body in (("activate", None), ("fund", funding)):
        refused = await sponsor.call("POST", f"{base}/{action}", 409, json=body)
        assert "replay the paid attack against the new config first" in refused["detail"]
    queued = await sponsor.call("POST", f"{base}/rearm", 202)
    assert (queued["state"], queued["tripped_runs"]) == ("queued", 0)
    waiting = await sponsor.call("POST", f"{base}/activate", 409)
    assert "still being replayed" in waiting["detail"]

    model = CountingModel()
    assert await platform.work(model) == 1
    assert model.conversations == 5
    detail = await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty_id}")
    assert detail["rearm"] == {
        "state": "failed",
        "config_hash": queued["config_hash"],
        "tripped_runs": 5,
    }
    for action, body in (("activate", None), ("fund", funding)):
        refused = await sponsor.call("POST", f"{base}/{action}", 409, json=body)
        assert "the paid attack still breaks this config" in refused["detail"]
    # Asking again does not replay a config that already failed.
    again = await sponsor.call("POST", f"{base}/rearm", 202)
    assert again["state"] == "failed"
    assert await platform.work(model) == 0

    # A patch that does stop it.
    patched = await sponsor.call("PUT", f"{base}/config", json=PATCHED_CONFIG)
    assert patched["config_hash"] != queued["config_hash"]
    await sponsor.call("POST", f"{base}/activate", 409)
    await sponsor.call("POST", f"{base}/rearm", 202)
    assert await platform.work(model) == 1
    detail = await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty_id}")
    assert detail["rearm"] == {
        "state": "passed",
        "config_hash": patched["config_hash"],
        "tripped_runs": 0,
    }

    # Now it can be funded again, and goes live on the patched config.
    funded = await sponsor.call("POST", f"{base}/fund", json=funding)
    assert funded["state"] == "awaiting_deposit"
    platform.wallet.simulate_deposit(
        funded["amount_due_zatoshi"], funded["deposit_code"], confirmations=3
    )
    await platform.payments.tick()
    public = (await platform.client.get(base)).json()
    assert (public["state"], public["accepting_attempts"]) == ("live", True)

    async with platform.sessions() as session:
        checks = (await session.scalars(select(RearmCheck).order_by(RearmCheck.created_at))).all()
    assert [check.state for check in checks] == [RearmState.FAILED, RearmState.PASSED]


async def test_a_passed_check_does_not_cover_a_config_edited_afterwards(platform: Platform) -> None:
    sponsor, bounty_id = await broken(platform)
    base = f"/v1/bounties/{bounty_id}"
    await sponsor.call("PUT", f"{base}/config", json=PATCHED_CONFIG)
    await sponsor.call("POST", f"{base}/rearm", 202)
    await platform.work()

    # The check passed, then the sponsor swaps the guard back out.
    await sponsor.call("PUT", f"{base}/config", json={**DEMO_CONFIG, "model": "another-model"})
    refused = await sponsor.call("POST", f"{base}/activate", 409)
    assert "replay the paid attack against the new config first" in refused["detail"]
    detail = await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty_id}")
    assert detail["rearm"] is None


async def test_a_re_arm_replay_that_fails_to_run_is_retried_not_passed(platform: Platform) -> None:
    sponsor, bounty_id = await broken(platform)
    base = f"/v1/bounties/{bounty_id}"
    await sponsor.call("PUT", f"{base}/config", json=PATCHED_CONFIG)
    await sponsor.call("POST", f"{base}/rearm", 202)

    class Down(FakeModel):
        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise ModelError("model API unavailable")

    from agentbounty.workers import replay

    assert await replay.work_once(platform.sessions, platform.cipher, Down(), max_tries=3)
    detail = await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty_id}")
    assert detail["rearm"]["state"] == "queued"
    await sponsor.call("POST", f"{base}/activate", 409)
    await platform.work()
    assert (await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty_id}"))["rearm"][
        "state"
    ] == "passed"


# --- nothing secret in a database dump ------------------------------------------------------


async def test_a_database_dump_holds_no_secret_in_plaintext(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    # A leak, so the secret is in a transcript, and a forbidden call, so it is in evidence.
    leak = await platform.submit(bounty_id, REVEAL_DIRECTIVE)
    await platform.work()
    await platform.payments.tick()
    status = await platform.status(leak)
    assert status["verdict"] == "verified"
    # The hunter, who extracted it, does see it.
    assert SECRET in json.dumps(status)

    async with platform.sessions() as session:
        tables = (
            await session.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        ).all()
        rows: list[str] = []
        for table in tables:
            dumped = await session.scalars(text(f'SELECT row_to_json(t)::text FROM "{table}" t'))  # noqa: S608
            rows.extend(dumped.all())
    dumps = {"sql": "\n".join(rows)}
    assert len(rows) > 20

    pg_dump = shutil.which("pg_dump")
    if pg_dump:
        url = make_url(platform.app.state.settings.database_url)
        command = [
            pg_dump,
            "--data-only",
            "--inserts",
            "-h",
            url.host or "localhost",
            "-p",
            str(url.port or 5432),
        ]
        command += ["-U", url.username] if url.username else []
        result = subprocess.run(  # noqa: S603, ASYNC221 - a fixed command, run once in a test
            [*command, url.database or ""],
            capture_output=True,
            text=True,
            env={"PGPASSWORD": url.password or "", "PATH": "/usr/bin:/bin"},
            check=False,
        )
        if result.returncode == 0:
            dumps["pg_dump"] = result.stdout

    api_key = sponsor.headers["Authorization"].removeprefix("Bearer ")
    secrets_ = {
        "the canary secret": SECRET,
        "the system prompt": "treasury assistant for Northwind",
        "a tool's canned result": "Transfer submitted.",
        "the sponsor's API key": api_key,
        "a hunter's status token": leak["status_token"],
    }
    # A forbidden-tool rule is not on this list. It is hidden from hunters by the API, but
    # stored as plain JSON: the brief encrypts prompts, tool definitions and canary secrets.
    for name, dump in dumps.items():
        assert "ledger_entries" in dump or "deposit" in dump
        for what, plaintext in secrets_.items():
            for form in (plaintext, plaintext.encode().hex(), plaintext.lower()):
                assert form not in dump, f"{what} is readable in the {name} dump"


# --- structured logs and the audit log ------------------------------------------------------


def test_logs_are_one_json_object_per_line() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("agentbounty.test.json")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("payout %s sent", "p-1", extra={"audit": True, "amount_zatoshi": 5, "txid": "ab"})
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("pass failed\nwith a newline")

    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert first["message"] == "payout p-1 sent"
    assert (first["level"], first["logger"]) == ("info", "agentbounty.test.json")
    assert (first["audit"], first["amount_zatoshi"], first["txid"]) == (True, 5, "ab")
    assert first["time"].endswith("+00:00")
    assert second["level"] == "error"
    assert "ValueError: boom" in second["exception"]
    assert second["message"] == "pass failed\nwith a newline"


def test_configuring_logging_twice_adds_one_handler() -> None:
    root = logging.getLogger()
    kept = list(root.handlers)
    level = root.level
    root.handlers[:] = [h for h in kept if not isinstance(h.formatter, JsonFormatter)]
    try:
        before = list(root.handlers)
        configure_logging()
        configure_logging()
        added = [handler for handler in root.handlers if handler not in before]
        assert len(added) == 1
        assert isinstance(added[0].formatter, JsonFormatter)
        assert logging.getLogger("uvicorn.access").propagate
    finally:
        root.handlers[:] = kept
        root.setLevel(level)


async def audit_trail(platform: Platform) -> list[tuple[str, int | None, bool]]:
    async with platform.sessions() as session:
        entries = await session.scalars(select(PaymentAudit).order_by(PaymentAudit.created_at))
        return [(e.action.value, e.amount_zatoshi, e.txid is not None) for e in entries]


async def test_every_payment_action_is_in_the_audit_log(
    platform: Platform, caplog: pytest.LogCaptureFixture
) -> None:
    sponsor = await platform.sponsor()
    with caplog.at_level("INFO", logger="agentbounty.services.payment"):
        paid, refunded = await sponsor.funded(platform), await sponsor.funded(platform)
        platform.wallet.simulate_deposit(700, "dep-unknown", confirmations=5)
        await platform.payments.tick()
        await platform.submit(paid, STRONG)
        await platform.work()
        platform.wallet.fail_next_send("lightwalletd unreachable")
        await platform.payments.tick()
        await platform.payments.tick()
        await sponsor.call("POST", f"/v1/bounties/{refunded}/close", json={})
        await platform.payments.tick()

    assert await audit_trail(platform) == [
        ("deposit_matched", 1_100_000, True),
        ("deposit_credited", 1_100_000, True),
        ("deposit_matched", 1_100_000, True),
        ("deposit_credited", 1_100_000, True),
        ("deposit_unmatched", 700, True),
        ("payout_created", 1_000_000, False),
        ("payout_retry", 1_000_000, False),
        ("payout_sent", 1_000_000, True),
        ("refund_created", 1_100_000, False),
        ("refund_sent", 1_100_000, True),
    ]
    # Each action is also a structured log record.
    logged = [record for record in caplog.records if getattr(record, "audit", False)]
    actions = [getattr(record, "action", None) for record in logged]
    assert actions == [action for action, _, _ in await audit_trail(platform)]
    sent = logged[actions.index("payout_sent")]
    assert json.loads(JsonFormatter().format(sent))["txid"] == platform.wallet.sent[0].txid

    # The log is append-only.
    async with platform.sessions() as session:
        for statement in (
            "UPDATE payment_audit SET amount_zatoshi = 0",
            "DELETE FROM payment_audit",
            "TRUNCATE payment_audit",
        ):
            with pytest.raises(Exception, match="payment_audit is append-only"):
                await session.execute(text(statement))
            await session.rollback()
    assert len(await audit_trail(platform)) == 10


async def test_a_send_that_cannot_be_resolved_is_audited_as_such(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).funded(platform)
    await platform.submit(bounty_id, STRONG)
    await platform.work()
    platform.wallet.fail_next_send("broadcast timed out", ambiguous=True)
    await platform.payments.tick()
    await platform.payments.tick()
    trail = await audit_trail(platform)
    assert trail[-2:] == [
        ("payout_created", 1_000_000, False),
        ("payout_unresolved", 1_000_000, False),
    ]
    async with platform.sessions() as session:
        detail = await session.scalar(
            select(PaymentAudit.detail).where(PaymentAudit.action == "payout_unresolved")
        )
    assert detail == "broadcast timed out"
    assert platform.wallet.sent == []
    assert HUNTER not in str(trail)
    assert SECRET_CANARY["secret"] == SECRET
