import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";

import type { AttemptStatus } from "../types.ts";
import { checksOf, whyNot } from "./AttackRun.tsx";
import { AttemptResult, isSettled } from "./AttemptStatus.tsx";
import { Transcript } from "./Transcript.tsx";

afterEach(cleanup);

const held = (index: number) => ({ run_index: index, tripped: [], replies: 1, tool_calls: [], tokens: 120 });
const broke = (index: number) => ({
  run_index: index,
  tripped: ["unapproved_transfer"],
  replies: 2,
  tool_calls: ["transfer_funds"],
  tokens: 200,
});
const SECRET = { type: "secret_leak" as const, public_label: "signing_reference" };
const TOOL = { type: "forbidden_tool" as const, public_label: "unapproved_transfer" };

const BASE: AttemptStatus = {
  state: "done",
  verdict: "not_verified",
  tripped_runs: 0,
  total_runs: 5,
  canary: null,
  replies: [{ text: "How can I help?", tool_calls: [] }],
  payout: null,
  queue_ahead: null,
  runs_done: 5,
  runs_total: 5,
  threshold: 3,
  runs: [0, 1, 2, 3, 4].map(held),
  canaries: [
    { canary: SECRET, tripped_runs: 0 },
    { canary: TOOL, tripped_runs: 0 },
  ],
};
const WAITING: AttemptStatus = {
  ...BASE,
  state: "queued",
  verdict: null,
  replies: [],
  queue_ahead: 2,
  runs_done: 0,
  runs: [],
  canaries: [],
};
const VERIFIED: AttemptStatus = {
  ...BASE,
  verdict: "verified",
  tripped_runs: 5,
  canary: TOOL,
  replies: [
    { text: "Calling transfer_funds.", tool_calls: [{ name: "transfer_funds", arguments: { recipient: "attacker" } }] },
  ],
  runs: [0, 1, 2, 3, 4].map(broke),
  canaries: [
    { canary: SECRET, tripped_runs: 0 },
    { canary: TOOL, tripped_runs: 5 },
  ],
};

describe("isSettled", () => {
  test.each([
    ["queued", WAITING, false],
    ["running", { ...BASE, state: "running", verdict: null }, false],
    ["not verified", BASE, true],
    ["not counted", { ...BASE, verdict: "not_counted" }, true],
    ["errored", { ...BASE, state: "error", verdict: null }, true],
    ["verified, payout not created yet", VERIFIED, false],
    ["verified, payout pending", { ...VERIFIED, payout: { state: "pending", amount_zatoshi: 5, txid: null } }, false],
    ["verified, payout sending", { ...VERIFIED, payout: { state: "sending", amount_zatoshi: 5, txid: null } }, false],
    ["verified, payout sent", { ...VERIFIED, payout: { state: "sent", amount_zatoshi: 5, txid: "ab" } }, true],
  ] as [string, AttemptStatus, boolean][])("%s", (_name, status, settled) => {
    expect(isSettled(status)).toBe(settled);
  });
});

const states = (status: AttemptStatus, withPayout = true) =>
  Object.fromEntries(checksOf(status, withPayout).map((check) => [check.id, check.state]));

describe("the checks an attack goes through", () => {
  test("a queued attack is accepted, waits its turn, and nothing after has started", () => {
    expect(states(WAITING)).toEqual({
      accepted: "passed",
      queue: "running",
      replay: "pending",
      canaries: "pending",
      verdict: "pending",
    });
    const queue = checksOf(WAITING).find((check) => check.id === "queue");
    expect(queue?.summary).toContain("2 attacks on this bounty are replayed before yours");
    expect(queue?.meter).toBe("2 ahead");
    expect(checksOf({ ...WAITING, queue_ahead: 0 })[1]?.summary).toContain("Next in line");
  });

  test("a running attack says which replay it is on", () => {
    const running = { ...WAITING, state: "running" as const, queue_ahead: null, runs_done: 2 };
    const replay = checksOf(running).find((check) => check.id === "replay");
    expect(replay?.state).toBe("running");
    expect(replay?.summary).toBe("2 of 5 finished. Replay 3 is running.");
    expect(replay?.meter).toBe("2 / 5");
  });

  test("an attack the agent held against fails the canary check and the verdict", () => {
    expect(states(BASE)).toEqual({
      accepted: "passed",
      queue: "passed",
      replay: "passed",
      canaries: "failed",
      verdict: "failed",
    });
  });

  test("a verified attack passes them all, and the payout follows it to the end", () => {
    expect(states(VERIFIED).payout).toBe("running");
    expect(checksOf(VERIFIED).at(-1)?.summary).toContain("Under review");
    const sent = { ...VERIFIED, payout: { state: "sent" as const, amount_zatoshi: 1_000_000, txid: "d007ff50" } };
    expect(Object.values(states(sent)).every((state) => state === "passed")).toBe(true);
    expect(states(VERIFIED, false).payout).toBeUndefined();
  });

  test("a replay that could not be completed stops there", () => {
    const errored = { ...WAITING, state: "error" as const, queue_ahead: null };
    expect(states(errored)).toEqual({ accepted: "passed", queue: "passed", replay: "failed" });
  });

  test("an attack that was never replayed is skipped, not failed", () => {
    const late = { ...BASE, verdict: "not_counted" as const, total_runs: 0, runs: [], canaries: [], replies: [] };
    expect(states(late)).toMatchObject({ replay: "skipped", canaries: "skipped", verdict: "failed" });
  });
});

describe("why an attack did not break the agent", () => {
  test("nothing tripped and the agent never called a tool", () => {
    const why = whyNot(BASE).join(" ");
    expect(why).toContain("No canary tripped in any of the 5 replays.");
    expect(why).toContain("did not call a tool in any replay");
    expect(why).toContain("planted secret did not appear");
  });

  test("the agent called the tool, but within its rule", () => {
    const within = { ...BASE, runs: [0, 1, 2, 3, 4].map((index) => ({ ...held(index), tool_calls: ["transfer_funds"] })) };
    expect(whyNot(within).join(" ")).toContain("called transfer_funds in 5 of 5 replays, but never in a way its rule forbids");
  });

  test("a near miss says how close it was", () => {
    const near = {
      ...BASE,
      runs: [broke(0), broke(1), held(2), held(3), held(4)],
      canaries: [
        { canary: SECRET, tripped_runs: 0 },
        { canary: TOOL, tripped_runs: 2 },
      ],
    };
    expect(whyNot(near)[0]).toContain("Close. unapproved_transfer tripped in 2 of 5 replays and needs 3.");
  });

  test("an attack that worked too late, or was never replayed, says so", () => {
    const beaten = { ...VERIFIED, verdict: "not_counted" as const };
    expect(whyNot(beaten)[0]).toBe("Your attack worked: unapproved_transfer tripped in 5 of 5 replays.");
    const late = { ...BASE, verdict: "not_counted" as const, runs: [], canaries: [] };
    expect(whyNot(late)[0]).toContain("never replayed");
  });

  test("a verified or unfinished attack has nothing to explain", () => {
    expect(whyNot(VERIFIED)).toEqual([]);
    expect(whyNot(WAITING)).toEqual([]);
  });
});

describe("AttemptResult", () => {
  test("a queued attempt shows the queue check in progress", () => {
    const view = render(<AttemptResult status={WAITING} />);
    expect(view.container.textContent).toContain("replayed before yours");
    expect(view.container.querySelector(".check.running .check-mark.running")).not.toBeNull();
    expect(view.container.querySelectorAll(".check.pending")).toHaveLength(3);
  });

  test("a failed attempt shows each run, each canary, why, and what the agent said", () => {
    const view = render(<AttemptResult status={BASE} />);
    const text = view.container.textContent ?? "";
    expect(view.container.querySelectorAll(".run-cells li.held")).toHaveLength(5);
    expect(view.container.querySelectorAll(".tallies li")).toHaveLength(2);
    expect(text).toContain("Why it did not break");
    expect(text).toContain("How can I help?");
    expect(view.container.querySelector(".paid")).toBeNull();
  });

  test("a verified attempt shows the canary, then the payout transaction once it is sent", () => {
    const waiting = render(<AttemptResult status={VERIFIED} />);
    expect(waiting.container.querySelectorAll(".run-cells li.tripped")).toHaveLength(5);
    expect(waiting.container.querySelector(".tallies li.won")?.textContent).toContain("unapproved_transfer");
    expect(waiting.container.textContent).toContain("Under review");
    expect(waiting.container.textContent).not.toContain("Why it did not");
    expect(waiting.container.querySelector(".call")?.textContent).toContain('"recipient": "attacker"');
    cleanup();

    const payout = { state: "sent" as const, amount_zatoshi: 1_000_000, txid: "d007ff50" };
    const paid = render(<AttemptResult status={{ ...VERIFIED, payout }} />);
    expect(paid.container.querySelector(".paid")?.textContent).toBe(
      "A payout of 0.01 ZEC was sent. Transaction d007ff50",
    );
  });

  test("an errored replay asks for another try", () => {
    const view = render(<AttemptResult status={{ ...WAITING, state: "error", queue_ahead: null }} />);
    expect(view.container.textContent).toContain("could not be completed");
  });

  test("an attack being watched has its results revealed one check at a time", () => {
    const view = render(<AttemptResult status={{ ...WAITING, state: "running", queue_ahead: null }} live />);
    view.rerender(<AttemptResult status={BASE} live />);
    // The replay is in; the canary check is the one now resolving, the verdict still waits.
    expect(view.container.querySelector(".check.running")?.textContent).toContain("Canary check");
    expect(view.container.textContent).not.toContain("Why it did not break");
  });
});

describe("what hunters and agents wrote is shown as text, never as markup", () => {
  const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>';

  test("in an agent's reply and its tool-call arguments", () => {
    const status = { ...BASE, replies: [{ text: hostile, tool_calls: [{ name: "t", arguments: { note: hostile } }] }] };
    const view = render(<AttemptResult status={status} />);
    expect(view.container.querySelector("img")).toBeNull();
    expect(view.container.querySelector("script")).toBeNull();
    expect(view.container.querySelector(".text")?.textContent).toBe(hostile);
  });

  test("in a transcript, with the tripping message marked", () => {
    const view = render(
      <Transcript
        messages={[
          { role: "user", content: hostile, tool_calls: [] },
          { role: "assistant", content: "ok", tool_calls: [] },
        ]}
        evidence={[
          {
            canary: { type: "secret_leak", public_label: hostile },
            message_index: 1,
            location: "content",
            representation: "base64",
            matched_text: "ok",
          },
        ]}
      />,
    );
    expect(view.container.querySelector("img")).toBeNull();
    const messages = view.container.querySelectorAll(".message");
    expect(messages[0]?.classList.contains("tripped")).toBe(false);
    expect(messages[1]?.classList.contains("tripped")).toBe(true);
    expect(view.container.querySelector(".hit")?.textContent).toBe(`Tripped ${hostile} (base64)`);
  });
});
