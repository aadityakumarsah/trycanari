"""The queue and the worker: ordering, pausing, config changes, crashes and failures."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from agentbounty.models import Attempt, AttemptState, Bounty, BountyState, Finding, Run
from agentbounty.services import attempts
from agentbounty.services.bounty import load_target
from agentbounty.services.harness import EntryPoint, SlotFill, run_attempt
from agentbounty.services.model import (
    ChatMessage,
    FakeModel,
    ModelError,
    ModelParams,
    ModelPermanentError,
    ModelRateLimitError,
    ModelResponse,
    ToolSpec,
)
from agentbounty.workers import replay

from .platform import PATCHED_CONFIG, STRONG, WEAK, Platform, verdict_of


class CountingModel(FakeModel):
    """FakeModel that counts the conversations it is given and can fail on some of them."""

    def __init__(self, fail_on: Sequence[int] = ()) -> None:
        super().__init__()
        self.conversations = 0
        self._fail_on = set(fail_on)

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], params: ModelParams
    ) -> ModelResponse:
        if messages[-1].role == "user":
            self.conversations += 1
            if self.conversations in self._fail_on:
                raise ModelError("model API unavailable")
        return await super().complete(messages, tools, params)


async def count(platform: Platform, model: type[Any]) -> int:
    async with platform.sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def attempt_row(platform: Platform, attempt: dict[str, str]) -> Attempt:
    async with platform.sessions() as session:
        return await session.get_one(Attempt, uuid.UUID(attempt["attempt_id"]))


async def claim(platform: Platform) -> attempts.Claim | None:
    async with platform.sessions.begin() as session:
        return await attempts.claim_next(session)


async def test_only_the_first_hunter_to_break_a_config_is_paid(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).live()
    first = await platform.submit(bounty_id, STRONG)
    second = await platform.submit(bounty_id, STRONG)
    model = CountingModel()
    assert await platform.work(model) == 2

    assert (await platform.status(first))["verdict"] == "verified"
    # Queued before the pause, but the hole was already paid for: it is not even replayed.
    assert verdict_of(await platform.status(second)) == {
        "state": "done",
        "verdict": "not_counted",
        "tripped_runs": 0,
        "total_runs": 0,
        "canary": None,
        "replies": [],
        "payout": None,
    }
    assert model.conversations == 5
    assert await count(platform, Finding) == 1
    assert await count(platform, Run) == 5


async def test_attempts_of_a_bounty_run_one_at_a_time_in_arrival_order(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_a, bounty_b = await sponsor.live(), await sponsor.live()
    a1 = await platform.submit(bounty_a, WEAK)
    await platform.submit(bounty_a, WEAK)
    b1 = await platform.submit(bounty_b, WEAK)

    first, second = await claim(platform), await claim(platform)
    assert first is not None
    assert second is not None
    # The oldest attempt, then the other bounty's: a2 waits while a1 is running.
    assert [str(first.attempt_id), str(second.attempt_id)] == [a1["attempt_id"], b1["attempt_id"]]
    assert await claim(platform) is None
    assert (await platform.status(a1))["state"] == "running"

    async with platform.sessions.begin() as session:
        await attempts.record_result(session, platform.cipher, first)
    third = await claim(platform)
    assert third is not None
    assert third.attempt_id not in (first.attempt_id, second.attempt_id)


async def test_workers_do_not_wait_for_each_others_claims(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    a = await platform.submit(await sponsor.live(), WEAK)
    b = await platform.submit(await sponsor.live(), WEAK)
    async with platform.sessions() as one, platform.sessions() as two:
        # Neither transaction has committed: the second skips the row the first has locked.
        first = await attempts.claim_next(one)
        second = await attempts.claim_next(two)
        assert first is not None
        assert second is not None
        assert {str(first.attempt_id), str(second.attempt_id)} == {a["attempt_id"], b["attempt_id"]}
        await one.commit()
        await two.commit()


async def test_an_attempt_bound_to_a_replaced_config_does_not_count(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.live()
    first = await platform.submit(bounty_id, STRONG)
    stale = await platform.submit(bounty_id, STRONG)
    assert (await platform.poll(first))["verdict"] == "verified"

    # The broken config cannot simply be switched back on.
    blocked = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/activate", 409)
    assert "has been broken" in blocked["detail"]

    version = await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", json=PATCHED_CONFIG)
    assert version["version"] == 2
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/rearm", 202)
    # The worker replays the paid attack against the patch, then finds the stale attempt.
    model = CountingModel()
    await platform.work(model)
    assert model.conversations == 5
    reactivated = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/activate")
    assert reactivated == {"state": "live", "config_hash": version["config_hash"]}
    # The canaries came along to the new version.
    listed = (await platform.client.get(f"/v1/bounties/{bounty_id}")).json()
    assert [canary["public_label"] for canary in listed["canaries"]] == [
        "signing_reference",
        "unapproved_transfer",
    ]

    # The bounty is live again, but on another config: the old attempt is void and was
    # never replayed as an attempt.
    assert (await platform.status(stale))["verdict"] == "not_counted"
    assert await count(platform, Run) == 5

    # The same attack against the patched config is judged afresh, and now fails.
    fresh = await platform.submit(bounty_id, STRONG)
    assert (await platform.poll(fresh))["verdict"] == "not_verified"
    assert await count(platform, Finding) == 1


async def test_a_break_found_after_the_bounty_moved_on_is_recorded_but_not_counted(
    platform: Platform,
) -> None:
    bounty_id = await (await platform.sponsor()).live()
    attempt = await platform.submit(bounty_id, STRONG)
    held = await claim(platform)
    assert held is not None

    async with platform.sessions() as session:
        row = await session.get_one(Attempt, held.attempt_id)
        bounty = await session.get_one(Bounty, row.bounty_id)
        target = await load_target(session, platform.cipher, await _live_config(session, bounty))
    fills = [SlotFill(EntryPoint.USER_MESSAGE, STRONG)]
    results = await run_attempt(target.config, fills, FakeModel())
    judgement = attempts.judge_runs(target, results)
    assert judgement.tripped_runs == 5

    # While the replay ran, the bounty stopped being live.
    async with platform.sessions.begin() as session:
        await session.execute(update(Bounty).values(state=BountyState.PAUSED))
    async with platform.sessions.begin() as session:
        assert (
            await attempts.record_result(session, platform.cipher, held, results, judgement) is None
        )

    status = await platform.status(attempt)
    assert (status["verdict"], status["tripped_runs"], status["total_runs"]) == (
        "not_counted",
        5,
        5,
    )
    assert await count(platform, Finding) == 0


async def _live_config(session: Any, bounty: Bounty) -> Any:
    from agentbounty.models import TargetConfig

    return await session.get_one(TargetConfig, bounty.live_config_id)


async def test_an_attempt_whose_worker_died_is_retried_from_scratch(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).live()
    attempt = await platform.submit(bounty_id, STRONG)

    # A worker claims the attempt and dies before recording anything.
    dead = await claim(platform)
    assert dead is not None
    assert (await platform.status(attempt))["state"] == "running"
    assert await platform.work() == 0

    async with platform.sessions.begin() as session:
        # Not stale yet.
        assert (
            await attempts.requeue_stale(session, older_than=timedelta(hours=1), max_tries=3) == 0
        )
    async with platform.sessions.begin() as session:
        assert await attempts.requeue_stale(session, older_than=timedelta(0), max_tries=3) == 1
    row = await attempt_row(platform, attempt)
    assert (row.state, row.tries) == (AttemptState.QUEUED, 1)
    assert await count(platform, Run) == 0

    assert await platform.work() == 1
    assert (await platform.status(attempt))["verdict"] == "verified"
    assert (await attempt_row(platform, attempt)).tries == 2
    assert await count(platform, Run) == 5

    # The dead worker turns out to be alive and tries to record its result: it is ignored.
    async with platform.sessions.begin() as session:
        assert await attempts.record_result(session, platform.cipher, dead) is None
    assert await count(platform, Run) == 5
    assert await count(platform, Finding) == 1


async def test_a_slow_worker_cannot_overwrite_the_worker_that_replaced_it(
    platform: Platform,
) -> None:
    bounty_id = await (await platform.sponsor()).live()
    attempt = await platform.submit(bounty_id, WEAK)
    slow = await claim(platform)
    async with platform.sessions.begin() as session:
        await attempts.requeue_stale(session, older_than=timedelta(0), max_tries=3)
    replacement = await claim(platform)
    assert slow is not None
    assert replacement is not None
    assert slow.attempt_id == replacement.attempt_id
    assert slow.started_at != replacement.started_at

    # The slow worker finishes first, with a result that must not be written.
    async with platform.sessions.begin() as session:
        assert await attempts.record_result(session, platform.cipher, slow) is None
        await attempts.release(session, slow, max_tries=3)
    assert (await platform.status(attempt))["state"] == "running"
    async with platform.sessions.begin() as session:
        await attempts.record_result(session, platform.cipher, replacement)
    assert (await platform.status(attempt))["state"] == "done"


async def test_a_failed_replay_leaves_nothing_behind_and_is_retried(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).live()
    attempt = await platform.submit(bounty_id, STRONG)

    # The model fails on the third of five runs: the first two must not be kept.
    model = CountingModel(fail_on=[3])
    assert await replay.work_once(platform.sessions, platform.cipher, model, max_tries=3)
    row = await attempt_row(platform, attempt)
    assert (row.state, row.tries) == (AttemptState.QUEUED, 1)
    assert await count(platform, Run) == 0
    assert await count(platform, Finding) == 0

    assert await platform.work(model) == 1
    assert (await platform.status(attempt))["verdict"] == "verified"
    assert await count(platform, Run) == 5


async def test_provider_capacity_waits_without_spending_an_attempt_try(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).live()
    attempt = await platform.submit(bounty_id, STRONG)

    class LimitedOnce(CountingModel):
        async def complete(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[ToolSpec],
            params: ModelParams,
        ) -> ModelResponse:
            if self.conversations == 0:
                self.conversations += 1
                raise ModelRateLimitError("free tier is cooling down", 120)
            return await super().complete(messages, tools, params)

    assert await replay.work_once(platform.sessions, platform.cipher, LimitedOnce(), max_tries=3)
    row = await attempt_row(platform, attempt)
    assert (row.state, row.tries, row.runs_done) == (AttemptState.QUEUED, 0, 0)
    assert row.started_at is not None
    waiting = await platform.status(attempt)
    assert waiting["tries"] == 0
    assert waiting["retry_at"] is not None
    assert await claim(platform) is None

    async with platform.sessions.begin() as session:
        await session.execute(update(Attempt).where(Attempt.id == row.id).values(started_at=None))
    assert await platform.work(FakeModel()) == 1
    assert (await platform.status(attempt))["verdict"] == "verified"
    assert await count(platform, Run) == 5


async def test_a_permanent_provider_rejection_is_not_retried(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).live()
    attempt = await platform.submit(bounty_id, STRONG)

    class InvalidRequest(FakeModel):
        async def complete(
            self,
            messages: Sequence[ChatMessage],
            tools: Sequence[ToolSpec],
            params: ModelParams,
        ) -> ModelResponse:
            raise ModelPermanentError("invalid model", 400)

    assert await replay.work_once(
        platform.sessions,
        platform.cipher,
        InvalidRequest(),
        max_tries=3,
    )
    row = await attempt_row(platform, attempt)
    assert (row.state, row.tries, row.runs_done) == (AttemptState.ERROR, 1, 0)
    status = await platform.status(attempt)
    assert (status["state"], status["tries"], status["retry_at"]) == ("error", 1, None)
    assert await count(platform, Run) == 0


async def test_an_attempt_that_keeps_failing_ends_in_error(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).live()
    attempt = await platform.submit(bounty_id, STRONG)
    later = await platform.submit(bounty_id, WEAK)

    broken = CountingModel(fail_on=range(1, 100))
    # Two attempts, two tries each.
    assert await platform.work(broken, max_tries=2) == 4
    row = await attempt_row(platform, attempt)
    assert (row.state, row.tries) == (AttemptState.ERROR, 2)
    assert verdict_of(await platform.status(attempt)) == {
        "state": "error",
        "verdict": None,
        "tripped_runs": None,
        "total_runs": None,
        "canary": None,
        "replies": [],
        "payout": None,
    }
    # The failed attempt does not block the queue behind it.
    assert (await attempt_row(platform, later)).state is AttemptState.ERROR
    assert await count(platform, Run) == 0

    # A worker that died on its last try is not retried either.
    final = await platform.submit(bounty_id, WEAK)
    for _ in range(2):
        assert await claim(platform) is not None
        async with platform.sessions.begin() as session:
            await attempts.requeue_stale(session, older_than=timedelta(0), max_tries=2)
    assert (await attempt_row(platform, final)).state is AttemptState.ERROR


async def test_a_flaky_attack_below_the_threshold_is_not_a_finding(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).live()
    attempt = await platform.submit(bounty_id, STRONG)
    await platform.work(FakeModel(obedience=(True, False, False, True, False)))
    status = await platform.status(attempt)
    assert (status["verdict"], status["tripped_runs"], status["total_runs"]) == (
        "not_verified",
        2,
        5,
    )
    # The hunter is shown a run in which the attack did work.
    assert status["replies"][0]["tool_calls"][0]["name"] == "transfer_funds"
    assert await count(platform, Finding) == 0
    assert (await platform.client.get(f"/v1/bounties/{bounty_id}")).json()["state"] == "live"


async def test_make_model_follows_the_settings(platform: Platform) -> None:
    settings = platform.app.state.settings
    assert isinstance(replay.make_model(settings), FakeModel)
    real = settings.model_copy(
        update={"model_backend": "real", "model_api_base": "https://model.test/v1"}
    )
    with pytest.raises(ValueError, match="MODEL_API_KEY"):
        replay.make_model(real)
