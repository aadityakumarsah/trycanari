import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { forgetLoaded } from "../hooks.ts";
import type { BountyPublic } from "../types.ts";
import { Home, outcomeOf } from "./Home.tsx";

const realFetch = globalThis.fetch;

afterEach(() => {
  cleanup();
  forgetLoaded();
  globalThis.fetch = realFetch;
});

const BOUNTY: BountyPublic = {
  id: "b1",
  title: "Treasury agent",
  description: "",
  sponsor: null,
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

describe("the home page", () => {
  test("it tells a sponsor that a coding agent can scan from the editor, and where to set it up", () => {
    // The page asks for the bounties, the activity and the leaders; none are needed here.
    globalThis.fetch = (async () => new Response("[]", { status: 503 })) as unknown as typeof fetch;
    const view = render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    );
    const strip = view.container.querySelector("#sponsors .editor-strip");
    expect(strip?.querySelector("h3")?.textContent).toContain("Scan from your editor");
    expect(strip?.querySelector(".code-block pre")?.textContent).toContain("trycanari-mcp");
    const hrefs = [...(strip?.querySelectorAll("a") ?? [])].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/docs#editor", "/skill.md"]);
    const questions = [...view.container.querySelectorAll("#faq summary")].map((summary) => summary.textContent);
    expect(questions).toContain("Can my coding agent test my agent while I build it?");
  });
});
