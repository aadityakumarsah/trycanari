import { useEffect } from "react";

import { api } from "./api.ts";
import { useLoad, type Loaded } from "./hooks.ts";
import { useSession } from "./session.tsx";
import type { Account, CostStep, Providers } from "./types.ts";

const CHANGED = "trycanari:account";

/** Say that the account's credits have changed, so every meter on the page catches up. */
export function accountChanged(): void {
  window.dispatchEvent(new Event(CHANGED));
}

/** The signed-in account and its credits. `data` stays null for a visitor. */
export function useAccount(): Loaded<Account> {
  const { session } = useSession();
  const signedIn = session?.signed_in === true;
  const account = useLoad<Account | null>(
    (signal) => (signedIn ? api<Account>("GET", "/v1/account", { signal }) : Promise.resolve(null)),
    signedIn ? "in" : "out",
    signedIn ? "account" : undefined,
  );
  const { reload } = account;
  useEffect(() => {
    const refresh = () => void reload();
    window.addEventListener(CHANGED, refresh);
    return () => window.removeEventListener(CHANGED, refresh);
  }, [reload]);
  return account as Loaded<Account>;
}

/** How this deployment lets people in. The same for everyone, so it is remembered. */
export function useProviders(): Providers | null {
  return useLoad((signal) => api<Providers>("GET", "/v1/auth/providers", { signal }), "", "providers").data;
}

/** Credits an attack of this many characters costs. */
export function costOf(characters: number, steps: CostStep[]): number {
  const last = steps[steps.length - 1];
  return steps.find((step) => characters <= step.up_to_characters)?.credits ?? last?.credits ?? 0;
}
