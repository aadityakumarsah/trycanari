import { describe, expect, test } from "bun:test";

import type { SponsorBountyDetail } from "../types.ts";
import { houseLine, isMoving, ruleValue, tabOf } from "./DashboardBounty.tsx";

describe("the house hunter's go at a config", () => {
  test("is said in a sentence, or not at all when it has not been", () => {
    expect(houseLine(null)).toBeNull();
    expect(houseLine(undefined)).toBeNull();
    expect(houseLine({ state: "running", outcome: null, tries: 1, detail: null })).toBe(
      "The house hunter is attacking this config now: 1 attack so far.",
    );
    expect(houseLine({ state: "done", outcome: "held", tries: 5, detail: null })).toBe(
      "The house hunter made 5 attacks on this config and none got through.",
    );
    expect(houseLine({ state: "done", outcome: "broke", tries: 2, detail: null })).toContain("Nothing is paid for it");
    expect(houseLine({ state: "done", outcome: "stopped", tries: 2, detail: "the bounty is paused" })).toBe(
      "The house hunter stopped after 2 attacks: the bounty is paused.",
    );
    expect(houseLine({ state: "done", outcome: "stopped", tries: 0, detail: "this config was live before the house hunter was switched on" })).toBe(
      "The house hunter has not attacked this config: this config was live before the house hunter was switched on.",
    );
  });
});

describe("ruleValue", () => {
  test.each([
    ["not_in", "payroll-ops, vendor-acme", ["payroll-ops", "vendor-acme"]],
    ["in", " attacker ,, ", ["attacker"]],
    ["gt", "1000", 1000],
    ["eq", "prod", "prod"],
    ["eq", "true", true],
    ["ne", '"quoted"', "quoted"],
  ] as [string, string, unknown][])("%s %p", (operator, raw, value) => {
    expect(ruleValue(operator, raw)).toEqual(value);
  });
});

describe("isMoving", () => {
  const bounty = { state: "paused", closing: false, rearm: null } as SponsorBountyDetail;
  const check = { config_hash: "h", tripped_runs: 0 };

  test("a settled bounty is left alone", () => {
    expect(isMoving(bounty)).toBe(false);
    expect(isMoving({ ...bounty, state: "draft" })).toBe(false);
    expect(isMoving({ ...bounty, state: "closed" })).toBe(false);
    expect(isMoving({ ...bounty, rearm: { ...check, state: "passed" } })).toBe(false);
  });

  test("anything the server may change by itself is watched", () => {
    expect(isMoving({ ...bounty, state: "awaiting_deposit" })).toBe(true);
    expect(isMoving({ ...bounty, state: "live" })).toBe(true);
    expect(isMoving({ ...bounty, closing: true })).toBe(true);
    expect(isMoving({ ...bounty, rearm: { ...check, state: "queued" } })).toBe(true);
    expect(isMoving({ ...bounty, rearm: { ...check, state: "running" } })).toBe(true);
  });
});

describe("tabOf", () => {
  test("reads a known tab and falls back to the overview", () => {
    expect(tabOf("ledger")).toBe("ledger");
    expect(tabOf("findings")).toBe("findings");
    expect(tabOf(null)).toBe("overview");
    expect(tabOf("nope")).toBe("overview");
  });
});
