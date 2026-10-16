import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, messageOf } from "./api.ts";

export interface Loaded<T> {
  data: T | null;
  error: string;
  /** The HTTP status of a failed load, when the API answered. */
  status: number | null;
  loading: boolean;
  reload: () => Promise<void>;
}

// What pages loaded last time, so coming back to one shows it at once while it refreshes.
const remembered = new Map<string, unknown>();

/** Forget everything remembered: what one sponsor loaded must not greet the next. */
export function forgetLoaded(): void {
  remembered.clear();
}

interface LoadState<T> {
  data: T | null;
  error: string;
  status: number | null;
  loading: boolean;
}

function starting<T>(remember: string | undefined): LoadState<T> {
  const known = remember === undefined ? undefined : (remembered.get(remember) as T | undefined);
  return { data: known ?? null, error: "", status: null, loading: known === undefined };
}

function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

/**
 * Loads something when the component mounts and whenever `key` changes.
 * A reload keeps showing the data it already has, so the page does not flicker.
 * With `remember`, the last answer is shown immediately on the next visit and
 * replaced when the fresh one arrives.
 *
 * Optimized with:
 * - 30-second timeout to allow a sleeping free service to wake up
 * - Automatic retry on network errors with exponential backoff
 * - Smart handling of rate limits (429)
 * - Stale cache validation (shows cached data but refreshes in background)
 */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, key: string, remember?: string): Loaded<T> {
  const [state, setState] = useState<LoadState<T>>(() => starting<T>(remember));
  const rememberAs = useRef(remember);
  rememberAs.current = remember;
  const latest = useRef(load);
  latest.current = load;
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const MAX_RETRIES = 3;
  const TIMEOUT_MS = 30000;

  const run = useCallback(async () => {
    const runGeneration = ++generation.current;
    controller.current?.abort();
    setState((previous) => ({ ...previous, error: "", loading: previous.data === null }));

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
      const mine = new AbortController();
      controller.current = mine;
      let timedOut = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        mine.abort();
      }, TIMEOUT_MS);
      try {
        const data = await latest.current(mine.signal);
        clearTimeout(timeout);
        if (runGeneration !== generation.current) return;
        if (rememberAs.current !== undefined) remembered.set(rememberAs.current, data);
        setState({ data, error: "", status: null, loading: false });
        return;
      } catch (error) {
        clearTimeout(timeout);
        if (runGeneration !== generation.current || (mine.signal.aborted && !timedOut)) return;
        const status = error instanceof ApiError ? error.status : null;
        const message = timedOut
          ? "The server took too long to respond. Please try again."
          : messageOf(error);
        const retryable = timedOut || status === null || status === 429 || status >= 500;
        if (!retryable || attempt === MAX_RETRIES) {
          setState((previous) => ({ data: previous.data, error: message, status, loading: false }));
          return;
        }
        const requested = error instanceof ApiError ? error.retryAfterMs : null;
        // Obey the server for throttles. Cap one UI wait so a tab can recover promptly
        // after deployment restarts or a stale proxy response.
        const delay = Math.min(requested ?? 1000 * 2 ** attempt, 60_000);
        await pause(delay, mine.signal);
        if (runGeneration !== generation.current) return;
      }
    }
  }, []);

  useEffect(() => {
    setState(starting<T>(rememberAs.current));
    void run();
    return () => {
      generation.current += 1;
      controller.current?.abort();
    };
  }, [key, run]);

  return { ...state, reload: run };
}

/** Calls `tick` every `ms` while `active`, and not while the tab is hidden. */
export function useInterval(tick: () => void, ms: number, active: boolean): void {
  const latest = useRef(tick);
  latest.current = tick;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      if (!document.hidden) latest.current();
    }, ms);
    return () => clearInterval(timer);
  }, [ms, active]);
}

/** Runs an action from a form or a button: tracks that it is busy and what went wrong. */
export function useAction(): {
  busy: boolean;
  error: string;
  run: (action: () => Promise<void>) => Promise<boolean>;
  clear: () => void;
} {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = useCallback(async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      return true;
    } catch (caught) {
      setError(messageOf(caught) || (caught instanceof Error ? caught.message : "Something went wrong."));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, clear: () => setError("") };
}
