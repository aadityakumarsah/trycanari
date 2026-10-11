import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { forgetLoaded } from "../hooks.ts";
import { SessionProvider } from "../session.tsx";
import { GOOGLE_ERRORS, Login } from "./Login.tsx";

const realFetch = globalThis.fetch;
afterEach(() => {
  cleanup();
  forgetLoaded();
  globalThis.fetch = realFetch;
});

/** The page on a server that does, or does not, offer Google sign-in. */
function page(google: boolean, path = "/login", invite_only = false) {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const body = String(input).endsWith("/v1/auth/providers") ? { google, invite_only } : { signed_in: false, email: null };
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch;
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider>
        <Login />
      </SessionProvider>
    </MemoryRouter>,
  );
}

describe("Login", () => {
  test("offers Google when the server does", async () => {
    const view = page(true);
    const link = await waitFor(() => view.getByText("Continue with Google").closest("a"));
    expect(link?.getAttribute("href")).toBe("/v1/auth/google/start");
    // The API key forms stay.
    expect(view.getByText("I have an API key")).toBeDefined();
  });

  test("shows no Google button when the server offers none", async () => {
    const view = page(false);
    await waitFor(() => expect(view.getByText("I have an API key")).toBeDefined());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(view.queryByText("Continue with Google")).toBeNull();
  });

  test("explains a refused Google sign-in, and ignores an error it does not know", () => {
    for (const [code, message] of Object.entries(GOOGLE_ERRORS)) {
      expect(page(false, `/login?error=${code}`).getByText(message)).toBeDefined();
      cleanup();
    }
    expect(page(false, "/login?error=<script>").container.querySelector(".error")).toBeNull();
  });

  test("needs no referral code to join, and carries one from an invite link to Google", async () => {
    const view = page(true, "/login?code=ABCD-2345", true);
    const google = (await waitFor(() => view.getByText("Continue with Google"))).closest("a");
    expect(google?.getAttribute("href")).toBe("/v1/auth/google/start?code=ABCD-2345");
    expect(view.queryByLabelText("Referral code")).toBeNull();
    expect(view.getByText("Create an account").hasAttribute("disabled")).toBe(false);
    expect(sessionStorage.getItem("trycanari.referral")).toBe("ABCD-2345");
  });
});
