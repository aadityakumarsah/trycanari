import { describe, expect, test } from "bun:test";

import type { SponsorBountyDetail } from "../types.ts";
import { isMoving, ruleValue } from "./DashboardBounty.tsx";

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
