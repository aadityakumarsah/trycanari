import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { forgetLoaded } from "../hooks.ts";
import { formatZec } from "../money.ts";
import { SessionProvider } from "../session.tsx";
import type { Sponsorship, SponsorBreak, SponsorOverview } from "../types.ts";
import { isBusy, openAgents, pastAgents, Sponsor, standing, tabOf } from "./Sponsor.tsx";

const realFetch = globalThis.fetch;
afterEach(() => {
  cleanup();
  forgetLoaded();
  globalThis.fetch = realFetch;
});

const AGENT: Sponsorship = {
  id: "b1",
  title: "Treasury agent",
  description: "",
  state: "live",
  closing: false,
  reward_zatoshi: 100_000_000,
  balance_zatoshi: 110_000_000,
  required_zatoshi: 110_000_000,
  created_at: "2026-10-01T00:00:00Z",
  attempts: 4,
  breaks: 1,
  last_attack_at: "2026-10-12T00:00:00Z",
  paid_zatoshi: 100_000_000,
  model: "demo-model",
  entry_points: ["user_message", "document"],
  canaries: 2,
};

const BREAK: SponsorBreak = {
  id: "f1",
  bounty_id: "b1",
  bounty_title: "Treasury agent",
  hunter: { name: "Ada Hunter", picture_url: null, github: "ada", company: "Analytical", role: "Researcher", website: null },
  house: false,
  canary: { type: "forbidden_tool", public_label: "unapproved_transfer" },
  tripped_runs: 5,
  total_runs: 5,
  reward_zatoshi: 100_000_000,
  payout_state: "sent",
  txid: "tx1",
  verified_at: "2026-10-12T00:00:00Z",
};

const OVERVIEW: SponsorOverview = {
  member_since: "2026-09-01T00:00:00Z",
  totals: {
    bounties: 3,
    live: 1,
    drafts: 1,
    paused: 0,
    past: 1,
    escrow_zatoshi: 110_000_000,
    attempts: 4,
    breaks: 1,
    paid_zatoshi: 100_000_000,
  },
  bounties: [
    AGENT,
    { ...AGENT, id: "b2", title: "Draft agent", state: "draft", attempts: 0, breaks: 0, last_attack_at: null, paid_zatoshi: 0, model: null, entry_points: [], canaries: 0, balance_zatoshi: 0 },
    { ...AGENT, id: "b3", title: "Closed agent", state: "closed", balance_zatoshi: 0 },
  ],
  breaks: [BREAK],
};

const ACCOUNT = {
  email: "team@example.com",
  name: "Northwind Labs",
  picture_url: null,
  github: "northwind",
  company: "Northwind",
  role: "Platform team",
  website: "https://northwind.example",
  can_sponsor: true,
  can_hunt: true,
  is_admin: false,
  is_owner: false,
  is_ambassador: false,
  invite_only: false,
  verified_breaks: 0,
  picture_uploaded: false,
  can_upload_picture: false,
  google_linked: false,
  location: null,
  bio: null,
  credits: { tier: "Starter", limit: 5, used: 0, left: 5, next_tier: null, costs: [], new_bounty_premium: 0 },
};

async function page(overview: SponsorOverview = OVERVIEW, at = "/sponsor") {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://localhost").pathname;
    const body = path === "/v1/sponsor/overview" ? overview : path === "/v1/account" ? ACCOUNT : { signed_in: true, email: ACCOUNT.email };
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch;
  const view = render(
    <MemoryRouter initialEntries={[at]}>
      <SessionProvider>
        <Routes>
          <Route path="/sponsor" element={<Sponsor />} />
        </Routes>
      </SessionProvider>
    </MemoryRouter>,
  );
  await view.findByText("Your agents");
  return view;
}

describe("sorting what a sponsor has listed", () => {
  test("what is in play is apart from what is over, newest first", () => {
    const made = OVERVIEW.bounties;
    expect(openAgents(made).map((b) => b.id)).toEqual(["b2", "b1"]);
    expect(pastAgents(made).map((b) => b.id)).toEqual(["b3"]);
    expect(openAgents([]).length).toBe(0);
  });

  test("the page keeps watching only while something can move", () => {
    expect(isBusy(OVERVIEW)).toBe(true);
    expect(isBusy({ ...OVERVIEW, bounties: [{ ...AGENT, state: "paused" }] })).toBe(false);
    expect(isBusy({ ...OVERVIEW, bounties: [{ ...AGENT, state: "awaiting_deposit" }] })).toBe(true);
    expect(isBusy({ ...OVERVIEW, bounties: [] })).toBe(false);
  });

  test("the tab comes from the address, and anything else is the overview", () => {
    expect(tabOf("breaks")).toBe("breaks");
    expect(tabOf(null)).toBe("overview");
    expect(tabOf("nonsense")).toBe("overview");
  });

  test("each bounty says what it is doing and what to do next", () => {
    expect(standing(AGENT)).toBe("Live. 4 attacks so far.");
    expect(standing({ ...AGENT, attempts: 0 })).toContain("nothing has come in yet");
    expect(standing({ ...AGENT, attempts: 1 })).toBe("Live. 1 attack so far.");
    expect(standing({ ...AGENT, state: "draft", model: null })).toContain("Describe the agent");
    expect(standing({ ...AGENT, state: "draft" })).toContain("Plant canaries and fund it");
    expect(standing({ ...AGENT, state: "awaiting_deposit" })).toContain("Waiting for your deposit");
    expect(standing({ ...AGENT, state: "paused", breaks: 1 })).toContain("patch the agent and re-arm");
    expect(standing({ ...AGENT, state: "paused", breaks: 0 })).toContain("takes no attacks");
    expect(standing({ ...AGENT, closing: true })).toContain("Closing");
    expect(standing({ ...AGENT, state: "closed" })).toContain("Closed");
  });
});

describe("the sponsor page", () => {
  test("it shows who the sponsor is and what they have put in", async () => {
    const view = await page();
    const card = view.container.querySelector(".sponsor-profile");
    expect(card?.querySelector("h2")?.textContent).toBe("Northwind Labs");
    expect(card?.querySelector(".sponsor-line")?.textContent).toBe("Platform team at Northwind");
    expect(card?.textContent).toContain("team@example.com");
    expect(card?.textContent).toContain("github.com/northwind");
    expect(card?.textContent).toContain("Sponsor since");
    // The escrow and what it has already cost.
    const escrow = card?.querySelector(".sponsor-escrow")?.textContent ?? "";
    expect(escrow).toContain(formatZec(OVERVIEW.totals.escrow_zatoshi));
    expect(escrow).toContain(`Paid out ${formatZec(OVERVIEW.totals.paid_zatoshi)}`);
    expect(escrow).toContain("for 1 break");
  });

  test("the overview totals every agent, attack and break", async () => {
    const view = await page();
    const totals = view.container.querySelector(".sponsor-totals")?.textContent ?? "";
    expect(totals).toContain("Agents listed3");
    expect(totals).toContain("1 live now");
    expect(totals).toContain("Attacks received4");
    expect(totals).toContain("Verified breaks1");
    // What is in play and the latest break are both on it.
    expect(view.container.querySelectorAll(".agent-card").length).toBe(2);
    expect(view.container.querySelectorAll(".break-row").length).toBe(1);
  });

  test("the agents tab lists what is in play, with how each is faring", async () => {
    const view = await page(OVERVIEW, "/sponsor?tab=agents");
    const cards = [...view.container.querySelectorAll(".agent-card")].map((card) => card.textContent ?? "");
    expect(cards.length).toBe(2);
    const live = cards.find((text) => text.includes("Treasury agent")) ?? "";
    expect(live).toContain("Live. 4 attacks so far.");
    expect(live).toContain("demo-model");
    expect(live).toContain("2 canaries");
    expect(live).toContain("Last attack");
    const draft = cards.find((text) => text.includes("Draft agent")) ?? "";
    expect(draft).toContain("No agent described yet");
    expect(draft).toContain("Describe the agent");
    // A closed one is not in play.
    expect(cards.some((text) => text.includes("Closed agent"))).toBe(false);
  });

  test("the past tab holds the agents whose bounties are over", async () => {
    const view = await page(OVERVIEW, "/sponsor?tab=past");
    const cards = [...view.container.querySelectorAll(".agent-card")].map((card) => card.textContent ?? "");
    expect(cards.length).toBe(1);
    expect(cards[0]).toContain("Closed agent");
    expect(cards[0]).toContain("Paid out");
  });

  test("the breaks tab says who broke what, and what it cost", async () => {
    const view = await page(OVERVIEW, "/sponsor?tab=breaks");
    const row = view.container.querySelector(".break-row");
    expect(row?.querySelector(".break-who b")?.textContent).toBe("Ada Hunter");
    expect(row?.querySelector(".break-who small")?.textContent).toContain("Researcher at Analytical");
    expect(row?.querySelector(".break-what")?.textContent).toContain("unapproved_transfer");
    expect(row?.querySelector(".break-what")?.textContent).toContain("5 of 5 replays");
    expect(row?.querySelector(".break-what a")?.getAttribute("href")).toBe("/dashboard/b/b1?tab=findings");
    expect(row?.querySelector(".break-cost")?.textContent).toContain("Paid");
  });

  test("a break by the house hunter is named as such and shows no reward", async () => {
    const house: SponsorBreak = { ...BREAK, id: "f2", hunter: { name: "trycanari house hunter", picture_url: null, github: null, company: null, role: null, website: null }, house: true, reward_zatoshi: 0, payout_state: null, txid: null };
    const view = await page({ ...OVERVIEW, breaks: [house] }, "/sponsor?tab=breaks");
    const row = view.container.querySelector(".break-row");
    expect(row?.querySelector(".break-who b")?.textContent).toBe("The house hunter");
    expect(row?.textContent).toContain("Nothing was paid for it");
    expect(row?.querySelector(".break-cost")?.textContent).toBe("Not paid");
  });

  test("a sponsor with nothing listed is told what to do, not shown empty tables", async () => {
    const nothing: SponsorOverview = {
      member_since: "2026-09-01T00:00:00Z",
      totals: { bounties: 0, live: 0, drafts: 0, paused: 0, past: 0, escrow_zatoshi: 0, attempts: 0, breaks: 0, paid_zatoshi: 0 },
      bounties: [],
      breaks: [],
    };
    const view = await page(nothing);
    expect(view.container.textContent).toContain("You have not listed an agent yet");
    expect(view.container.querySelectorAll(".agent-card").length).toBe(0);
    cleanup();
    const breaks = await page(nothing, "/sponsor?tab=breaks");
    expect(breaks.container.querySelector(".sponsor-empty")?.textContent).toContain("Nobody has broken an agent of yours");
  });
});
