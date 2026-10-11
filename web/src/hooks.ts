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

/**
 * Loads something when the component mounts and whenever `key` changes.
 * A reload keeps showing the data it already has, so the page does not flicker.
 */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, key: string): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: string; status: number | null; loading: boolean }>({
    data: null,
    error: "",
    status: null,
    loading: true,
  });
  const latest = useRef(load);
  latest.current = load;
  const controller = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    controller.current?.abort();
    const mine = new AbortController();
    controller.current = mine;
    try {
      const data = await latest.current(mine.signal);
      if (!mine.signal.aborted) setState({ data, error: "", status: null, loading: false });
    } catch (error) {
      if (mine.signal.aborted) return;
      const status = error instanceof ApiError ? error.status : null;
      setState((previous) => ({ data: previous.data, error: messageOf(error), status, loading: false }));
    }
  }, []);

  useEffect(() => {
    setState({ data: null, error: "", status: null, loading: true });
    void run();
    return () => controller.current?.abort();
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
