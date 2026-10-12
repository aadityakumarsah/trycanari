import { useCallback, useEffect, useRef, useState } from "react";

import { api, messageOf } from "../api.ts";
import { settleAttempt } from "../attempts.ts";
import { useInterval } from "../hooks.ts";
import type { AttemptAccepted, AttemptStatus } from "../types.ts";
import { AttackRun } from "./AttackRun.tsx";
import { ErrorNote } from "./ui.tsx";

const POLL_MS = 1500;

/** Whether there is nothing more to wait for: judged, and paid if it won. */
export function isSettled(status: AttemptStatus): boolean {
  if (status.state === "queued" || status.state === "running") return false;
  if (status.verdict !== "verified") return true;
  return status.payout !== null && status.payout.state !== "pending" && status.payout.state !== "sending";
}

/** An attempt's result as its checks: what passed, what did not, and why. */
export function AttemptResult({ status, live = false }: { status: AttemptStatus; live?: boolean }) {
  return <AttackRun status={status} live={live} />;
}

/** Polls an attempt until it is judged, and paid if it won, and shows where it stands. */
export function AttemptTracker({ attempt, onSettled }: { attempt: AttemptAccepted; onSettled?: () => void }) {
  const [status, setStatus] = useState<AttemptStatus | null>(null);
  const [error, setError] = useState("");

  const check = useCallback(async () => {
    try {
      // The status token goes in a header, so it never appears in a URL or a log.
      setStatus(await api<AttemptStatus>("GET", `/v1/attempts/${attempt.attempt_id}`, { token: attempt.status_token }));
      setError("");
    } catch (caught) {
      setError(messageOf(caught));
    }
  }, [attempt]);

  useEffect(() => {
    setStatus(null);
    void check();
  }, [check]);
  const settled = status !== null && isSettled(status);
  useInterval(() => void check(), POLL_MS, !settled);
  // A verified attempt pauses the bounty, so let the page around this refresh itself.
  const notify = useRef(onSettled);
  notify.current = onSettled;
  useEffect(() => {
    if (!settled || !status) return;
    settleAttempt(attempt.attempt_id, status.state === "error" ? "error" : (status.verdict ?? "error"));
    notify.current?.();
    // `status` is read once, at the moment the attempt settles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled]);

  return (
    <section className="status" aria-live="polite">
      <h2>Your attempt</h2>
      <ErrorNote>{error}</ErrorNote>
      {status ? <AttemptResult status={status} live /> : !error && <p className="muted">Fetching the result…</p>}
    </section>
  );
}
