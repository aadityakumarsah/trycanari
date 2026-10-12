import { describe, expect, test } from "bun:test";

import type { BountyPublic } from "../types.ts";
import { outcomeOf } from "./Home.tsx";

const BOUNTY: BountyPublic = {
  id: "b1",
  title: "Treasury agent",
  description: "",
  state: "closed",
  accepting_attempts: false,
  reward_zatoshi: 1_000_000,
  entry_points: ["user_message"],
  canaries: [],
  attempts: 4,
  breaks: 0,
  broken_by: [],
  created_at: "2026-10-01T00:00:00Z",
};

describe("a completed bounty", () => {
  test("held when nobody broke it, however often it was attacked", () => {
    expect(outcomeOf(BOUNTY)).toBe("held");
    expect(outcomeOf({ ...BOUNTY, attempts: 0 })).toBe("held");
  });

  test("broken once any attack was verified", () => {
    expect(outcomeOf({ ...BOUNTY, state: "paused", breaks: 1 })).toBe("broken");
    expect(outcomeOf({ ...BOUNTY, breaks: 3 })).toBe("broken");
  });
});
