import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { formatZec } from "../money.ts";
import type { Breaker } from "../types.ts";
import { BrokenBy } from "./BrokenBy.tsx";

afterEach(cleanup);

const PAID: Breaker = {
  id: "f1",
  name: "Ada Hunter",
  picture_url: null,
  github: "ada",
  canary: { type: "forbidden_tool", public_label: "unapproved_transfer" },
  reward_zatoshi: 250_000_000,
  tripped_runs: 5,
  paid: true,
  verified_at: "2026-10-01T00:00:00Z",
};

describe("who broke a bounty", () => {
  test("a hunter is shown with what the break paid, and the house hunter with no reward", () => {
    const house: Breaker = { ...PAID, id: "f2", name: "trycanari house hunter", github: null, reward_zatoshi: 0, paid: false, house: true, verified_at: "2026-10-02T00:00:00Z" };
    const view = render(
      <MemoryRouter>
        <BrokenBy hunters={[PAID, house]} />
      </MemoryRouter>,
    );
    const [person, own] = [...view.container.querySelectorAll("li")];
    expect(person?.querySelector(".breaker-won")?.textContent).toContain(formatZec(PAID.reward_zatoshi));
    expect(person?.querySelector(".breaker-won")?.textContent).toContain("Paid");
    expect(own?.querySelector(".breaker-who b")?.textContent).toBe("trycanari house hunter");
    expect(own?.querySelector(".breaker-won")?.textContent).toBe("House hunter, not paid");
  });
});
