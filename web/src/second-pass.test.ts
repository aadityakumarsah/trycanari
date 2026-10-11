import { afterEach, describe, expect, test } from "bun:test";

import {
  forgetAllAttempts,
  forgetAttempt,
  saveAttempt,
  savedAttempts,
  savedPayoutAddress,
  savePayoutAddress,
  settleAttempt,
} from "./attempts.ts";
import { ago } from "./components/ui.tsx";
import { funded } from "./pages/Dashboard.tsx";
import { arrange } from "./pages/Home.tsx";
import type { BountyPublic } from "./types.ts";

function bounty(over: Partial<BountyPublic>): BountyPublic {
  return {
    id: "b",
    title: "Agent",
    description: "",
    state: "live",
    accepting_attempts: true,
    reward_zatoshi: 1,
    entry_points: ["user_message"],
    canaries: [],
    attempts: 0,
    breaks: 0,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

describe("arrange", () => {
  const treasury = bounty({ id: "t", title: "Treasury agent", reward_zatoshi: 5, attempts: 9, breaks: 1 });
  const support = bounty({
    id: "s",
    title: "Support desk",
    reward_zatoshi: 9,
    attempts: 2,
    created_at: "2026-03-01T00:00:00Z",
    canaries: [{ type: "secret_leak", public_label: "refund_code" }],
  });
  const all = [treasury, support];
  const ids = (list: BountyPublic[]) => list.map((item) => item.id);

  test("orders by each choice", () => {
    expect(ids(arrange(all, "", "reward"))).toEqual(["s", "t"]);
    expect(ids(arrange(all, "", "attacked"))).toEqual(["t", "s"]);
    expect(ids(arrange(all, "", "newest"))).toEqual(["s", "t"]);
    expect(ids(arrange(all, "", "unbroken"))).toEqual(["s", "t"]);
  });

  test("searches titles, descriptions and canary labels, ignoring case", () => {
    expect(ids(arrange(all, "  TREASURY ", "reward"))).toEqual(["t"]);
    expect(ids(arrange(all, "refund", "reward"))).toEqual(["s"]);
    expect(arrange(all, "nothing like it", "reward")).toEqual([]);
  });

  test("leaves the list it was given alone", () => {
    arrange(all, "", "attacked");
    expect(ids(all)).toEqual(["t", "s"]);
  });
});

describe("ago", () => {
  const now = Date.parse("2026-06-01T12:00:00Z");
  test("picks a unit a person would", () => {
    expect(ago("2026-06-01T11:59:30Z", now)).toBe("30 seconds ago");
    expect(ago("2026-06-01T11:15:00Z", now)).toBe("45 minutes ago");
    expect(ago("2026-06-01T07:00:00Z", now)).toBe("5 hours ago");
    expect(ago("2026-05-31T12:00:00Z", now)).toBe("yesterday");
    expect(ago("2026-05-12T12:00:00Z", now)).toBe("3 weeks ago");
  });
});

describe("funded", () => {
  test("is a whole percentage that never leaves 0 to 100", () => {
    expect(funded({ balance_zatoshi: 0, required_zatoshi: 110 })).toBe(0);
    expect(funded({ balance_zatoshi: 55, required_zatoshi: 110 })).toBe(50);
    expect(funded({ balance_zatoshi: 109, required_zatoshi: 110 })).toBe(99);
    expect(funded({ balance_zatoshi: 500, required_zatoshi: 110 })).toBe(100);
    expect(funded({ balance_zatoshi: 5, required_zatoshi: 0 })).toBe(0);
  });
});

describe("remembered attempts", () => {
  afterEach(() => localStorage.clear());
  const saved = (id: string, bountyId = "b1") => ({
    attempt_id: id,
    status_token: `token-${id}`,
    bounty_id: bountyId,
    bounty_title: "Agent",
    submitted_at: "2026-06-01T00:00:00Z",
  });

  test("are kept newest first, per bounty, without duplicates", () => {
    saveAttempt(saved("a"));
    saveAttempt(saved("b", "b2"));
    saveAttempt(saved("a"));
    expect(savedAttempts().map((attempt) => attempt.attempt_id)).toEqual(["a", "b"]);
    expect(savedAttempts("b2").map((attempt) => attempt.attempt_id)).toEqual(["b"]);
  });

  test("record an outcome, and can be forgotten", () => {
    saveAttempt(saved("a"));
    saveAttempt(saved("b"));
    settleAttempt("a", "verified");
    settleAttempt("missing", "error");
    expect(savedAttempts().find((attempt) => attempt.attempt_id === "a")?.outcome).toBe("verified");
    forgetAttempt("a");
    expect(savedAttempts().map((attempt) => attempt.attempt_id)).toEqual(["b"]);
    forgetAllAttempts();
    expect(savedAttempts()).toEqual([]);
  });

  test("survive storage holding something else", () => {
    localStorage.setItem("trycanari.attempts", "{not json");
    expect(savedAttempts()).toEqual([]);
    localStorage.setItem("trycanari.attempts", JSON.stringify([{ nope: 1 }, saved("a")]));
    expect(savedAttempts().map((attempt) => attempt.attempt_id)).toEqual(["a"]);
  });

  test("the payout address is remembered only when asked", () => {
    expect(savedPayoutAddress()).toBe("");
    savePayoutAddress("utest1abc");
    expect(savedPayoutAddress()).toBe("utest1abc");
    savePayoutAddress("");
    expect(savedPayoutAddress()).toBe("");
  });
});
