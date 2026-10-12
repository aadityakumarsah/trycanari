import { useCallback, useEffect, useRef, useState } from "react";

import { api, messageOf } from "../api.ts";
import { settleAttempt } from "../attempts.ts";
import { useInterval } from "../hooks.ts";
import { formatZec, sym } from "../money.ts";
import type { AttemptAccepted, AttemptStatus } from "../types.ts";
import { Replies } from "./Transcript.tsx";
import { Badge, ErrorNote } from "./ui.tsx";

const POLL_MS = 1500;

/** Whether there is nothing more to wait for: judged, and paid if it won. */
export function isSettled(status: AttemptStatus): boolean {
  if (status.state === "queued" || status.state === "running") return false;
  if (status.verdict !== "verified") return true;
  return status.payout !== null && status.payout.state !== "pending" && status.payout.state !== "sending";
}

const WAITING: Record<string, string> = {
  queued: "Waiting in the queue. Attempts on a bounty run in the order they arrive.",
  running: "Being replayed against the sandboxed agent.",
};
const EXPLAIN: Record<string, string> = {
  not_verified: "No canary tripped in three of the five runs. Read what the agent did and change approach.",
  not_counted: "The bounty was no longer live on the config you attacked, so this attempt does not count.",
};

export function AttemptResult({ status }: { status: AttemptStatus }) {
  if (status.state === "queued" || status.state === "running") {
    return (
      <div className="replaying">
        <p>
          <Badge state={status.state} /> {WAITING[status.state]}
        </p>
        <div className="progress" role="progressbar" aria-label="Replaying the attack">
          <i />
        </div>
      </div>
    );
  }
  if (status.state === "error" || status.verdict === null) {
    return (
      <p>
        <Badge state="error" /> The replay could not be completed. Submit the attack again later.
      </p>
    );
  }
  const { payout } = status;
  return (
    <>
      <p>
        <Badge state={status.verdict} />{" "}
        {status.total_runs ? `Tripped in ${status.tripped_runs} of ${status.total_runs} runs. ` : ""}
        {status.canary ? `Canary: ${status.canary.public_label}.` : ""}
      </p>
      {status.total_runs ? (
        <p className="run-dots" role="img" aria-label={`${status.tripped_runs} of ${status.total_runs} runs tripped`}>
          {Array.from({ length: status.total_runs }, (_, index) => (
            <i key={index} className={index < (status.tripped_runs ?? 0) ? "on" : undefined} />
          ))}
        </p>
      ) : null}
      {EXPLAIN[status.verdict] && <p className="muted">{EXPLAIN[status.verdict]}</p>}
      {status.verdict === "verified" &&
        (payout?.txid ? (
          <p className="paid">
            A payout of {formatZec(payout.amount_zatoshi)} {sym()} was sent. Transaction <code>{payout.txid}</code>
          </p>
        ) : (
          <p className="muted">Verified. The payout is being sent.</p>
        ))}
      {status.replies.length > 0 && (
        <>
          <h3>What the agent did in one run</h3>
          <Replies replies={status.replies} />
        </>
      )}
    </>
  );
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
      {status ? <AttemptResult status={status} /> : !error && <p className="muted">Fetching the result…</p>}
    </section>
  );
}
