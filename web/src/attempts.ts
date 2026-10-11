// A hunter has no account, so the attempts they have made are remembered by this
// browser. Each entry holds the status token, which is what reads the result.
import type { AttemptAccepted, Verdict } from "./types.ts";

export interface SavedAttempt extends AttemptAccepted {
  bounty_id: string;
  bounty_title: string;
  submitted_at: string;
  /** Set once the attempt has been judged, so it need not be asked about again. */
  outcome?: Verdict | "error";
}

const KEY = "trycanari.attempts";
const ADDRESS_KEY = "trycanari.payout_address";
const LIMIT = 50;

function read(): SavedAttempt[] {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) ?? "[]") as unknown;
    return Array.isArray(stored) ? (stored as SavedAttempt[]).filter((item) => item?.attempt_id && item.status_token) : [];
  } catch {
    return [];
  }
}

function write(attempts: SavedAttempt[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(attempts.slice(0, LIMIT)));
    window.dispatchEvent(new Event("trycanari:attempts"));
  } catch {
    // Storage can be full or switched off. The attempt still shows until the page is left.
  }
}

/** Newest first. */
export function savedAttempts(bountyId?: string): SavedAttempt[] {
  const all = read();
  return bountyId ? all.filter((attempt) => attempt.bounty_id === bountyId) : all;
}

export function saveAttempt(attempt: SavedAttempt): void {
  write([attempt, ...read().filter((other) => other.attempt_id !== attempt.attempt_id)]);
}

export function settleAttempt(attemptId: string, outcome: Verdict | "error"): void {
  const all = read();
  const found = all.find((attempt) => attempt.attempt_id === attemptId);
  if (!found || found.outcome === outcome) return;
  found.outcome = outcome;
  write(all);
}

export function forgetAttempt(attemptId: string): void {
  write(read().filter((attempt) => attempt.attempt_id !== attemptId));
}

export function forgetAllAttempts(): void {
  write([]);
}

export function savedPayoutAddress(): string {
  try {
    return localStorage.getItem(ADDRESS_KEY) ?? "";
  } catch {
    return "";
  }
}

export function savePayoutAddress(address: string): void {
  try {
    localStorage.setItem(ADDRESS_KEY, address);
  } catch {
    // Not remembered, then.
  }
}
