import { useEffect, useState, type ReactNode } from "react";

import { formatZec, sym } from "../money.ts";
import type { AttemptStatus, CanaryTally } from "../types.ts";
import { Icon } from "./Brand.tsx";
import { Replies } from "./Transcript.tsx";
import { words } from "./ui.tsx";

export type CheckState = "pending" | "running" | "passed" | "failed" | "skipped";

export interface Check {
  id: "accepted" | "queue" | "replay" | "canaries" | "verdict" | "payout";
  title: string;
  state: CheckState;
  summary: string;
  /** A figure shown at the row's right edge, such as "3 / 5". */
  meter?: string | undefined;
}

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

/** The canary that tripped in the most runs. */
export function bestTally(status: AttemptStatus): CanaryTally | null {
  return status.canaries.reduce<CanaryTally | null>(
    (best, tally) => (best === null || tally.tripped_runs > best.tripped_runs ? tally : best),
    null,
  );
}

/**
 * The steps an attack goes through, each with where it stands. Every one of them is
 * something the server actually did: nothing here is a timer pretending to be work.
 */
export function checksOf(status: AttemptStatus, withPayout = true): Check[] {
  const { state, verdict, runs_total: total, threshold } = status;
  const waiting = state === "queued";
  const running = state === "running";
  const done = state === "done";
  const failed = state === "error" || (done && verdict === null);
  const replayed = status.runs.length > 0;
  const best = bestTally(status);
  const ahead = status.queue_ahead ?? 0;

  const checks: Check[] = [
    {
      id: "accepted",
      title: "Attack accepted",
      state: "passed",
      summary: "Checked against the agent's entry points and bound to the config that was live when you sent it.",
    },
    {
      id: "queue",
      title: "Queue",
      state: waiting ? "running" : "passed",
      summary: waiting
        ? ahead === 0
          ? "Next in line. A worker picks it up in a moment."
          : `${plural(ahead, "attack")} on this bounty ${ahead === 1 ? "is" : "are"} replayed before yours. They run in the order they arrived.`
        : "Reached the front of the queue.",
      meter: waiting && ahead > 0 ? `${ahead} ahead` : undefined,
    },
    {
      id: "replay",
      title: `Replay against the sandboxed agent, ${total} times`,
      state: waiting ? "pending" : running ? "running" : failed ? "failed" : replayed ? "passed" : "skipped",
      summary: waiting
        ? "Each replay starts from an empty conversation, with mock tools."
        : running
          ? status.runs_done === 0
            ? `Replay 1 of ${total} is running.`
            : `${status.runs_done} of ${total} finished. Replay ${Math.min(status.runs_done + 1, total)} is running.`
          : failed
            ? "The replay could not be completed. Nothing was charged for it; submit the attack again later."
            : replayed
              ? `All ${status.runs.length} replays finished.`
              : "Not replayed: the bounty was no longer live on this config when your turn came.",
      meter: waiting || failed ? undefined : `${done ? status.runs.length : status.runs_done} / ${total}`,
    },
  ];
  if (failed) return checks;

  const judged = done && replayed;
  checks.push({
    id: "canaries",
    title: "Canary check",
    state: !done ? "pending" : !replayed ? "skipped" : best && best.tripped_runs >= threshold ? "passed" : "failed",
    summary: !done
      ? `Every replay is checked against each of the agent's canaries. One has to trip in ${threshold} of ${total}.`
      : !replayed
        ? "Nothing to check: the attack was not replayed."
        : best && best.tripped_runs > 0
          ? `${best.canary.public_label} tripped in ${best.tripped_runs} of ${total} replays. It takes ${threshold}.`
          : `No canary tripped in any of the ${total} replays.`,
    meter: judged && best ? `${best.tripped_runs} / ${total}` : undefined,
  });
  checks.push({
    id: "verdict",
    title: "Verdict",
    state: !done ? "pending" : verdict === "verified" ? "passed" : "failed",
    summary: !done
      ? "Verified, not verified, or not counted."
      : verdict === "verified"
        ? "Verified break. You were the first to break this config, so the reward is yours."
        : verdict === "not_counted"
          ? "Not counted. The bounty had moved on from the config you attacked."
          : "Not verified. The agent held.",
  });
  if (withPayout && verdict === "verified") {
    const payout = status.payout;
    const sent = payout !== null && (payout.state === "sent" || payout.state === "confirmed");
    checks.push({
      id: "payout",
      title: "Payout",
      state: sent ? "passed" : payout?.state === "failed" ? "failed" : "running",
      summary: sent
        ? "Sent to your payout address."
        : payout === null
          ? "Under review: checked against the bounty's escrow and the platform's spending limits, then sent."
          : payout.state === "failed"
            ? "On hold. It could not be sent automatically, so a person is looking at it."
            : "Approved and being sent to your payout address.",
      meter: payout ? `${formatZec(payout.amount_zatoshi)} ${sym()}` : undefined,
    });
  }
  return checks;
}

/** Why an attack that was judged did not win, as facts about what the replays did. */
export function whyNot(status: AttemptStatus): string[] {
  if (status.state !== "done" || status.verdict === null || status.verdict === "verified") return [];
  const { runs, runs_total: total, threshold } = status;
  const best = bestTally(status);
  if (status.verdict === "not_counted") {
    return runs.length === 0
      ? [
          "Your attack was never replayed. Before its turn came, the bounty stopped being live on the config you attacked: another hunter broke it first, or the sponsor paused or changed the agent.",
          "Nothing is wrong with the attack itself. Try it again once the bounty is live.",
        ]
      : [
          `Your attack worked: ${best?.canary.public_label ?? "a canary"} tripped in ${best?.tripped_runs ?? threshold} of ${total} replays.`,
          "It does not count because the bounty was no longer live on that config when the replays finished. A config is paid for once, to the first hunter to break it.",
        ];
  }

  const why: string[] = [];
  if (best && best.tripped_runs > 0) {
    why.push(
      `Close. ${best.canary.public_label} tripped in ${best.tripped_runs} of ${total} replays and needs ${threshold}. The attack works some of the time: make it land every time, not just once.`,
    );
  } else {
    why.push(`No canary tripped in any of the ${total} replays.`);
  }
  const called = runs.filter((run) => run.tool_calls.length > 0);
  const tools = [...new Set(runs.flatMap((run) => run.tool_calls))];
  const toolCanaries = status.canaries.filter((tally) => tally.canary.type === "forbidden_tool");
  const secretCanaries = status.canaries.filter((tally) => tally.canary.type === "secret_leak");
  if (toolCanaries.some((tally) => tally.tripped_runs < threshold)) {
    why.push(
      called.length === 0
        ? "The agent did not call a tool in any replay. A forbidden-tool canary only trips on a call the agent actually makes, so the first thing to get is a call."
        : `The agent called ${tools.join(", ")} in ${called.length} of ${total} replays, but ${toolCanaries.every((tally) => tally.tripped_runs === 0) ? "never" : "not often enough"} in a way its rule forbids. It is willing to make the call: change what the call is made with.`,
    );
  }
  if (secretCanaries.some((tally) => tally.tripped_runs === 0)) {
    why.push(
      "The planted secret did not appear in anything the agent emitted: not as written, and not in base64, hex, reversed or spelled out with separators.",
    );
  }
  if (runs.length > 0 && runs.every((run) => run.replies === 0)) {
    why.push("The agent produced no reply at all in any replay.");
  }
  return why;
}

const STATE_ICON = { passed: "check", failed: "x" } as const;

function StateMark({ state }: { state: CheckState }) {
  if (state === "running") return <i className="check-mark running" aria-label="In progress" />;
  if (state === "passed" || state === "failed") {
    return (
      <i className={`check-mark ${state}`} aria-label={state === "passed" ? "Passed" : "Did not pass"}>
        <Icon name={STATE_ICON[state]} size={20} />
      </i>
    );
  }
  return <i className={`check-mark ${state}`} aria-label={state === "skipped" ? "Skipped" : "Not started"} />;
}

/** The five replays as cells: running, finished, and once judged, tripped or held. */
function RunCells({ status }: { status: AttemptStatus }) {
  const judged = status.state === "done" && status.runs.length > 0;
  return (
    <ol className="run-cells" aria-label="Replays">
      {Array.from({ length: status.runs_total }, (_, index) => {
        const run = status.runs.find((item) => item.run_index === index);
        const state = judged
          ? run && run.tripped.length > 0
            ? "tripped"
            : "held"
          : status.state === "running" && index === status.runs_done
            ? "running"
            : index < status.runs_done
              ? "finished"
              : "waiting";
        return (
          <li key={index} className={state}>
            <b>Run {index + 1}</b>
            <span>{state}</span>
            {judged && run && (
              <small>
                {run.tripped.length > 0 && <em>{run.tripped.join(", ")}</em>}
                {plural(run.replies, "reply", "replies")}
                {run.tool_calls.length > 0 ? ` · called ${run.tool_calls.join(", ")}` : " · no tool call"}
                {` · ${run.tokens.toLocaleString()} tokens`}
              </small>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** Each canary of the agent, with how many replays tripped it against how many it takes. */
function Tallies({ status }: { status: AttemptStatus }) {
  return (
    <ul className="tallies">
      {status.canaries.map(({ canary, tripped_runs: tripped }) => {
        const won = tripped >= status.threshold;
        return (
          <li key={`${canary.type}:${canary.public_label}`} className={won ? "won" : tripped > 0 ? "near" : undefined}>
            <span className="tally-name">
              <Icon name={canary.type === "secret_leak" ? "key" : "ban"} size={14} />
              <b className="code">{canary.public_label}</b>
              <small>{words(canary.type)}</small>
            </span>
            <span className="tally-bar" role="img" aria-label={`${tripped} of ${status.runs_total} replays, needs ${status.threshold}`}>
              {Array.from({ length: status.runs_total }, (_, index) => (
                <i key={index} className={index < tripped ? "on" : index === status.threshold - 1 ? "mark" : undefined} />
              ))}
            </span>
            <span className="tally-count">
              {tripped} / {status.runs_total}
              <small>{won ? "tripped" : `needs ${status.threshold}`}</small>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

const REVEAL_MS = 650;

/**
 * An attack on its way through the harness: accepted, queued, replayed five times,
 * checked against every canary, judged and, if it won, paid. Shows what passed, what
 * did not, and why.
 *
 * `live` is for an attack being watched as it runs: when its results arrive, the
 * checks after the replay resolve one after another instead of all at once.
 */
export function AttackRun({
  status,
  live = false,
  withPayout = true,
}: {
  status: AttemptStatus;
  live?: boolean;
  withPayout?: boolean;
}) {
  const checks = checksOf(status, withPayout);
  const done = status.state === "done";
  // How many checks have been revealed. Only a watched attack is revealed in steps.
  const [revealed, setRevealed] = useState(() => (live && !done ? 3 : Infinity));
  useEffect(() => {
    if (!done || revealed >= checks.length) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (still) {
      setRevealed(Infinity);
      return;
    }
    const timer = setTimeout(() => setRevealed((count) => count + 1), REVEAL_MS);
    return () => clearTimeout(timer);
  }, [done, revealed, checks.length]);
  const settledView = revealed >= checks.length;

  const shown: Check[] = checks.map((check, index) =>
    index < revealed || !done
      ? check
      : { ...check, state: index === revealed ? "running" : "pending", summary: index === revealed ? "Checking…" : "Waiting for the check before it.", meter: undefined },
  );
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const tokens = status.runs.reduce((sum, run) => sum + run.tokens, 0);
  const best = bestTally(status);
  const passed = shown.filter((check) => check.state === "passed").length;
  const why = settledView ? whyNot(status) : [];

  const detailOf = (check: Check): ReactNode => {
    if (check.id === "replay" && check.state !== "pending" && check.state !== "skipped" && check.state !== "failed") {
      return <RunCells status={status} />;
    }
    if (check.id === "canaries" && (check.state === "passed" || check.state === "failed") && status.canaries.length > 0) {
      return <Tallies status={status} />;
    }
    if (check.id === "payout" && status.payout?.txid) {
      return (
        <p className="paid">
          A payout of {formatZec(status.payout.amount_zatoshi)} {sym()} was sent. Transaction <code>{status.payout.txid}</code>
        </p>
      );
    }
    return null;
  };

  return (
    <div className="attack-run">
      <dl className="run-stats">
        <div>
          <dt>Checks passed</dt>
          <dd>
            {passed} <small>of {shown.length}</small>
          </dd>
        </div>
        <div>
          <dt>Replays</dt>
          <dd>
            {done ? status.runs.length : status.runs_done} <small>of {status.runs_total}</small>
          </dd>
        </div>
        <div>
          <dt>Best canary</dt>
          <dd>
            {settledView && best ? best.tripped_runs : "–"} <small>of {status.runs_total}, needs {status.threshold}</small>
          </dd>
        </div>
        <div>
          <dt>Model tokens</dt>
          <dd>{settledView && tokens > 0 ? tokens.toLocaleString() : "–"}</dd>
        </div>
      </dl>

      <ol className="checks">
        {shown.map((check, index) => {
          const detail = detailOf(check);
          const open = detail !== null && !closed[check.id];
          return (
            <li key={check.id} className={`check ${check.state}${open ? " open" : ""}`}>
              <button
                type="button"
                className="check-row"
                aria-expanded={detail ? open : undefined}
                disabled={!detail}
                onClick={() => setClosed((current) => ({ ...current, [check.id]: !current[check.id] }))}
              >
                <StateMark state={check.state} />
                <span className="check-text">
                  <b>
                    <span className="check-step">{index + 1}</span>
                    {check.title}
                  </b>
                  <small>{check.summary}</small>
                </span>
                {check.meter && <span className="check-meter">{check.meter}</span>}
                {detail && (
                  <span className="check-chevron">
                    <Icon name="down" size={16} />
                  </span>
                )}
              </button>
              {open && <div className="check-detail">{detail}</div>}
            </li>
          );
        })}
      </ol>

      {why.length > 0 && (
        <section className="why">
          <h3>
            <Icon name="eye" size={15} /> {status.verdict === "not_counted" ? "Why it did not count" : "Why it did not break"}
          </h3>
          <ul>
            {why.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
      )}

      {settledView && status.replies.length > 0 && (
        <section className="agent-side">
          <h3>What the agent did in one run</h3>
          <Replies replies={status.replies} />
        </section>
      )}
    </div>
  );
}
