import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError, messageOf } from "../api.ts";
import { useConfirm } from "../confirm.tsx";
import {
  forgetAllAttempts,
  forgetAttempt,
  savedAttempts,
  settleAttempt,
  type SavedAttempt,
} from "../attempts.ts";
import { AttemptResult, isSettled } from "../components/AttemptStatus.tsx";
import { Icon } from "../components/Brand.tsx";
import { Ago, Badge, ErrorNote } from "../components/ui.tsx";
import { useInterval } from "../hooks.ts";
import type { AttemptStatus } from "../types.ts";

/** The attempts this browser remembers, kept in step with every tab. */
export function useSavedAttempts(bountyId?: string): SavedAttempt[] {
  const [attempts, setAttempts] = useState(() => savedAttempts(bountyId));
  useEffect(() => {
    const refresh = () => setAttempts(savedAttempts(bountyId));
    refresh();
    window.addEventListener("trycanari:attempts", refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener("trycanari:attempts", refresh);
      window.removeEventListener("storage", refresh);
    };
  }, [bountyId]);
  return attempts;
}

/** One remembered attempt: where it stands now, and the full result on demand. */
export function AttemptRow({ attempt, showBounty = true }: { attempt: SavedAttempt; showBounty?: boolean }) {
  const [status, setStatus] = useState<AttemptStatus | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [gone, setGone] = useState(false);

  const check = useCallback(async () => {
    try {
      const fresh = await api<AttemptStatus>("GET", `/v1/attempts/${attempt.attempt_id}`, {
        token: attempt.status_token,
      });
      setStatus(fresh);
      setError("");
      if (isSettled(fresh)) settleAttempt(attempt.attempt_id, fresh.state === "error" ? "error" : (fresh.verdict ?? "error"));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) setGone(true);
      else setError(messageOf(caught));
    }
  }, [attempt.attempt_id, attempt.status_token]);

  // A judged attempt never changes, so it is only fetched when its details are opened.
  const waiting = !attempt.outcome && !gone && (status === null || !isSettled(status));
  useEffect(() => {
    if (!attempt.outcome || open) void check();
  }, [attempt.outcome, open, check]);
  useInterval(() => void check(), 2500, waiting);

  const badge = gone ? "error" : (status?.verdict ?? attempt.outcome ?? status?.state ?? "queued");
  return (
    <li className={open ? "attempt-row open" : "attempt-row"}>
      <div className="attempt-line">
        <Badge state={badge} />
        <span className="attempt-what">
          {showBounty && <Link to={`/b/${attempt.bounty_id}`}>{attempt.bounty_title}</Link>}
          <small>
            <Ago iso={attempt.submitted_at} />
            {status?.total_runs ? ` · tripped ${status.tripped_runs} of ${status.total_runs}` : ""}
            {status?.payout?.txid ? " · paid" : ""}
          </small>
        </span>
        <span className="attempt-actions">
          {!gone && (
            <button type="button" className="text-button" aria-expanded={open} onClick={() => setOpen((shown) => !shown)}>
              {open ? "Hide" : "Details"}
            </button>
          )}
          <button
            type="button"
            className="icon-button"
            aria-label="Forget this attempt"
            title="Forget this attempt"
            onClick={() => forgetAttempt(attempt.attempt_id)}
          >
            <Icon name="close" size={15} />
          </button>
        </span>
      </div>
      {gone && <p className="muted">This attempt no longer exists on the server.</p>}
      <ErrorNote>{error}</ErrorNote>
      {open && !gone && (
        <div className="attempt-detail">
          {status ? <AttemptResult status={status} /> : <p className="muted">Fetching the result…</p>}
        </div>
      )}
    </li>
  );
}

export function MyAttempts() {
  const attempts = useSavedAttempts();
  const confirm = useConfirm();
  const won = attempts.filter((attempt) => attempt.outcome === "verified").length;
  const pending = attempts.filter((attempt) => !attempt.outcome).length;

  return (
    <>
      <h1>My attempts</h1>
      <p className="lede">
        Hunters need no account, so your attempts are remembered by this browser only. Clearing your browser data
        forgets them.
      </p>
      {attempts.length === 0 ? (
        <div className="empty">
          <p>You have not attacked anything from this browser yet.</p>
          <Link to="/#bounties">Find a bounty →</Link>
        </div>
      ) : (
        <>
          <dl className="facts">
            <div>
              <dt>Attempts</dt>
              <dd>{attempts.length}</dd>
            </div>
            <div>
              <dt>Verified breaks</dt>
              <dd>{won}</dd>
            </div>
            <div>
              <dt>Still running</dt>
              <dd>{pending}</dd>
            </div>
          </dl>
          <ul className="attempt-list">
            {attempts.map((attempt) => (
              <AttemptRow key={attempt.attempt_id} attempt={attempt} />
            ))}
          </ul>
          <p>
            <button
              type="button"
              className="secondary"
              onClick={async () => {
                const sure = await confirm({
                  title: "Forget every attempt?",
                  body: "This browser will stop remembering them, and their results can no longer be read.",
                  confirm: "Forget all",
                  cancel: "Keep them",
                  danger: true,
                });
                if (sure) forgetAllAttempts();
              }}
            >
              Forget all
            </button>
          </p>
        </>
      )}
    </>
  );
}
