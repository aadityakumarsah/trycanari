import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { Pilot } from "./Pilot.tsx";

const realFetch = globalThis.fetch;
const realScrollTo = window.scrollTo;

afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
  window.scrollTo = realScrollTo;
});

describe("the design partner pilot", () => {
  test("submits the short form with its attribution and shows the next step", async () => {
    let sent: Record<string, unknown> | null = null;
    globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      sent = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return new Response(JSON.stringify({ id: "pilot-1", message: "Application received." }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;
    window.scrollTo = () => undefined;

    const view = render(
      <MemoryRouter initialEntries={["/pilot?source=yc-founder-email"]}>
        <Pilot />
      </MemoryRouter>,
    );
    fireEvent.change(view.getByLabelText("Your name"), { target: { value: "Ada Founder" } });
    fireEvent.change(view.getByLabelText("Work email"), { target: { value: "ada@example.com" } });
    fireEvent.change(view.getByLabelText("Company"), { target: { value: "Example Agents" } });
    fireEvent.change(view.getByLabelText("What does the agent do, and what can go wrong?"), {
      target: { value: "Our support agent can read account data and approve customer refunds." },
    });
    fireEvent.submit(view.getByRole("button", { name: "Apply for the pilot" }).closest("form")!);

    await waitFor(() => expect(view.getByText("Let’s test something real.")).toBeDefined());
    expect(sent).toMatchObject({
      name: "Ada Founder",
      work_email: "ada@example.com",
      company: "Example Agents",
      stage: "beta",
      goals: ["baseline"],
      source: "yc-founder-email",
      fax: "",
    });
    expect(view.getByRole("link", { name: "Create an account" }).getAttribute("href")).toBe("/login");
  });
});
