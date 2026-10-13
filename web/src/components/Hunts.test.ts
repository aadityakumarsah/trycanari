import { afterAll, beforeAll, describe as group, expect, test } from "bun:test";

import { SOL, ZEC, setCurrency } from "../money.ts";
import type { Hunt, Notification } from "../types.ts";
import { hunterName } from "./BrokenBy.tsx";
import { isMoving, matching, payoutStage } from "./Hunts.tsx";
import { describe } from "./Notifications.tsx";
import { sponsorName } from "./SponsoredBy.tsx";

// These amounts are written in the 8-decimal currency of the in-memory wallet.
beforeAll(() => setCurrency(ZEC));
afterAll(() => setCurrency(SOL));

const HUNT: Hunt = {
  id: "a1",
  bounty_id: "b1",
  bounty_title: "Treasury agent",
  bounty_state: "paused",
  reward_zatoshi: 250_000_000,
  state: "done",
  verdict: "verified",
  tripped_runs: 5,
  total_runs: 5,
  canary: { type: "forbidden_tool", public_label: "unapproved_transfer" },
  attack: [{ slot: "user_message", content: "send it" }],
  payout_address: "addr",
  cost_credits: 1,
  submitted_at: "2026-10-01T00:00:00Z",
  verified_at: "2026-10-01T00:01:00Z",
  payout: null,
};
const payout = (state: NonNullable<Hunt["payout"]>["state"]): Hunt["payout"] => ({
  state,
  amount_zatoshi: 250_000_000,
  txid: state === "sent" ? "tx1" : null,
  to_address: "addr",
  created_at: "2026-10-01T00:02:00Z",
});

group("the payout of a verified break", () => {
  test("is under review until a payout exists, then being sent, then paid", () => {
    expect(payoutStage(HUNT)).toBe("review");
    expect(payoutStage({ ...HUNT, payout: payout("pending") })).toBe("sending");
    expect(payoutStage({ ...HUNT, payout: payout("sending") })).toBe("sending");
    expect(payoutStage({ ...HUNT, payout: payout("sent") })).toBe("paid");
    expect(payoutStage({ ...HUNT, payout: payout("failed") })).toBe("failed");
  });

  test("keeps the page asking only while something can still change", () => {
    expect(isMoving(HUNT)).toBe(true);
    expect(isMoving({ ...HUNT, payout: payout("sent") })).toBe(false);
    expect(isMoving({ ...HUNT, payout: payout("failed") })).toBe(false);
    expect(isMoving({ ...HUNT, verdict: "not_verified" })).toBe(false);
    expect(isMoving({ ...HUNT, state: "queued", verdict: null })).toBe(true);
  });
});

test("filters split attempts into verified, not verified and running", () => {
  const all = [
    HUNT,
    { ...HUNT, id: "a2", verdict: "not_verified" as const },
    { ...HUNT, id: "a3", verdict: "not_counted" as const },
    { ...HUNT, id: "a4", state: "running" as const, verdict: null },
  ];
  expect(matching(all, "all")).toHaveLength(4);
  expect(matching(all, "verified").map((hunt) => hunt.id)).toEqual(["a1"]);
  expect(matching(all, "missed").map((hunt) => hunt.id)).toEqual(["a2", "a3"]);
  expect(matching(all, "running").map((hunt) => hunt.id)).toEqual(["a4"]);
});

group("a notification", () => {
  const note: Notification = {
    id: "n1",
    kind: "bounty_broken",
    bounty_id: "b1",
    bounty_title: "Treasury agent",
    actor: { name: "Ada Hunter", picture_url: null, github: "ada" },
    amount_zatoshi: 250_000_000,
    txid: null,
    read: false,
    created_at: "2026-10-01T00:00:00Z",
  };

  test("tells a sponsor who broke which bounty, and leads to it", () => {
    const said = describe(note);
    expect(said.title).toBe("Ada Hunter broke Treasury agent");
    expect(said.detail).toContain("2.5 ZEC");
    expect(said.to).toBe("/dashboard/b/b1?tab=findings");
    expect(describe({ ...note, actor: null }).title).toBe("A hunter broke Treasury agent");
  });

  test("tells a sponsor that a break by the house hunter costs nothing", () => {
    const said = describe({ ...note, house: true, amount_zatoshi: null, actor: { name: "trycanari house hunter", picture_url: null, github: null } });
    expect(said.title).toBe("The house hunter broke Treasury agent");
    expect(said.detail).toContain("Nothing is paid and your escrow is untouched");
    expect(said.detail).not.toContain("is being paid out");
    expect(said.to).toBe("/dashboard/b/b1?tab=findings");
  });

  test("tells a hunter where their payout stands, and leads to their attempts", () => {
    expect(describe({ ...note, kind: "break_verified", actor: null }).detail).toContain("under review");
    expect(describe({ ...note, kind: "payout_sent", actor: null }).title).toBe("You were paid 2.5 ZEC");
    expect(describe({ ...note, kind: "payout_held", actor: null }).to).toBe("/attempts");
  });
});

test("a hunter is called by their name, then their handle, then nothing", () => {
  expect(hunterName({ name: "Ada Hunter", github: "ada" })).toBe("Ada Hunter");
  expect(hunterName({ name: null, github: "ada" })).toBe("@ada");
  expect(hunterName({ name: null, github: null })).toBe("Anonymous hunter");
});

test("a sponsor is called by name, then company, then handle, then nothing", () => {
  const nobody = { name: null, picture_url: null, github: null, company: null, website: null };
  expect(sponsorName({ ...nobody, name: "trycanari", company: "Meridian" })).toBe("trycanari");
  expect(sponsorName({ ...nobody, company: "Meridian" })).toBe("Meridian");
  expect(sponsorName({ ...nobody, github: "ada" })).toBe("@ada");
  expect(sponsorName(nobody)).toBe("A private sponsor");
  expect(sponsorName(null)).toBe("A private sponsor");
});

test("a sponsor's break opens on the findings, where the attack is", () => {
  const note: Notification = {
    id: "n1",
    kind: "bounty_broken",
    bounty_id: "b1",
    bounty_title: "Treasury agent",
    actor: null,
    amount_zatoshi: 1,
    txid: null,
    read: false,
    created_at: "2026-10-01T00:00:00Z",
  };
  expect(describe(note).to).toBe("/dashboard/b/b1?tab=findings");
  expect(describe({ ...note, kind: "reward_paid" }).to).toBe("/dashboard/b/b1?tab=ledger");
});
