import { useEffect } from "react";

import { api } from "./api.ts";
import { useLoad, type Loaded } from "./hooks.ts";
import { useSession } from "./session.tsx";
import type { Account, CostStep, Credits, Providers } from "./types.ts";

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

const PENDING = "trycanari.referral";

/** Keep a referral code that came in a link until its owner is somewhere they can use it. */
export function rememberReferral(code: string): void {
  try {
    sessionStorage.setItem(PENDING, code);
  } catch {
    // It can always be typed in.
  }
}

export function rememberedReferral(): string {
  try {
    return sessionStorage.getItem(PENDING) ?? "";
  } catch {
    return "";
  }
}

/** The address that lets whoever opens it take part in bounties with this code. */
export function inviteLink(code: string): string {
  return `${window.location.origin}/invite/${encodeURIComponent(code)}`;
}

/** GitHub serves a public picture for every username. */
export function pictureOf(account: Pick<Account, "picture_url" | "github">): string | null {
  return account.picture_url ?? (account.github ? `https://github.com/${account.github}.png?size=160` : null);
}

/** How much of a profile is filled in, as the fields that are and the fields that count. */
export const PROFILE_FIELDS = ["name", "github", "company", "role", "location", "website", "bio"] as const;
export function completeness(account: Account): { done: number; total: number; percent: number } {
  const done = PROFILE_FIELDS.filter((field) => Boolean(account[field])).length;
  return { done, total: PROFILE_FIELDS.length, percent: Math.round((done / PROFILE_FIELDS.length) * 100) };
}

/** Credits an attack of this many characters costs, by length alone. */
export function costOf(characters: number, steps: CostStep[]): number {
  const last = steps[steps.length - 1];
  return steps.find((step) => characters <= step.up_to_characters)?.credits ?? last?.credits ?? 0;
}

/** The whole cost of an attack: its length price, plus the new-bounty premium when this
 * is the first attack the account makes on the bounty. */
export function attackCost(characters: number, credits: Credits, firstOnBounty: boolean): number {
  return costOf(characters, credits.costs) + (firstOnBounty ? credits.new_bounty_premium : 0);
}
