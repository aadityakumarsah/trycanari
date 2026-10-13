import { afterAll, beforeAll, describe, expect, test } from "bun:test";

import { SOL, ZEC, formatZec, parseZec, setCurrency } from "./money.ts";

// These amounts are written in the 8-decimal currency of the in-memory wallet.
beforeAll(() => setCurrency(ZEC));
afterAll(() => setCurrency(SOL));

describe("formatZec", () => {
  test.each([
    [0, "0"],
    [1, "0.00000001"],
    [100_000, "0.001"],
    [1_100_000, "0.011"],
    [250_000_000, "2.5"],
    [100_000_000, "1"],
    [-1_000_000, "-0.01"],
    [2_100_000_000_000_000, "21000000"],
  ])("%d zatoshi is %s ZEC", (zatoshi, text) => {
    expect(formatZec(zatoshi)).toBe(text);
  });
});

describe("parseZec", () => {
  test.each([
    ["0.01", 1_000_000],
    [" 2.5 ", 250_000_000],
    ["1", 100_000_000],
    ["0.00000001", 1],
    // Amounts a float would get wrong: 0.1 + 0.2, and 0.29 * 1e8.
    ["0.3", 30_000_000],
    ["0.29", 29_000_000],
    ["21000000", 2_100_000_000_000_000],
  ])("%s ZEC is %d zatoshi", (text, zatoshi) => {
    expect(parseZec(text)).toBe(zatoshi);
    expect(formatZec(zatoshi)).toBe(text.trim());
  });

  test.each(["", "abc", "1,5", "1e2", "-1", ".5", "0.000000001", "0", "0.0", "NaN", "999999999999999999"])(
    "refuses %p",
    (text) => {
      expect(() => parseZec(text)).toThrow();
    },
  );
});
