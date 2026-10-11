import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";

import type { AttemptStatus } from "../types.ts";
import { AttemptResult, isSettled } from "./AttemptStatus.tsx";
import { Transcript } from "./Transcript.tsx";

afterEach(cleanup);

const BASE: AttemptStatus = {
  state: "done",
  verdict: "not_verified",
  tripped_runs: 0,
  total_runs: 5,
  canary: null,
  replies: [{ text: "How can I help?", tool_calls: [] }],
  payout: null,
};
const VERIFIED: AttemptStatus = {
  ...BASE,
  verdict: "verified",
  tripped_runs: 5,
  canary: { type: "forbidden_tool", public_label: "unapproved_transfer" },
  replies: [
    { text: "Calling transfer_funds.", tool_calls: [{ name: "transfer_funds", arguments: { recipient: "attacker" } }] },
  ],
};

describe("isSettled", () => {
  test.each([
    ["queued", { ...BASE, state: "queued", verdict: null }, false],
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

describe("AttemptResult", () => {
  test("a queued attempt says it is waiting", () => {
    const view = render(<AttemptResult status={{ ...BASE, state: "queued", verdict: null, replies: [] }} />);
    expect(view.container.textContent).toContain("Waiting in the queue");
    expect(view.container.querySelector(".badge-queued")).not.toBeNull();
  });

  test("a failed attempt shows the count and what the agent said", () => {
    const view = render(<AttemptResult status={BASE} />);
    const text = view.container.textContent ?? "";
    expect(text).toContain("Tripped in 0 of 5 runs.");
    expect(text).toContain("How can I help?");
    expect(text).toContain("change approach");
    expect(view.container.querySelector(".paid")).toBeNull();
  });

  test("a verified attempt shows the canary, then the payout transaction once it is sent", () => {
    const waiting = render(<AttemptResult status={VERIFIED} />);
    expect(waiting.container.textContent).toContain("Canary: unapproved_transfer.");
    expect(waiting.container.textContent).toContain("The payout is being sent.");
    expect(waiting.container.querySelector(".call")?.textContent).toContain('"recipient": "attacker"');
    cleanup();

    const payout = { state: "sent" as const, amount_zatoshi: 1_000_000, txid: "d007ff50" };
    const paid = render(<AttemptResult status={{ ...VERIFIED, payout }} />);
    expect(paid.container.querySelector(".paid")?.textContent).toBe(
      "A payout of 0.01 ZEC was sent. Transaction d007ff50",
    );
  });

  test("an errored replay asks for another try", () => {
    const view = render(<AttemptResult status={{ ...BASE, state: "error", verdict: null, replies: [] }} />);
    expect(view.container.textContent).toContain("could not be completed");
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
