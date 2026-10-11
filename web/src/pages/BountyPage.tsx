import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { accountChanged, costOf, useAccount, useProviders } from "../account.tsx";
import { api } from "../api.ts";
import { saveAttempt, savedPayoutAddress, savePayoutAddress } from "../attempts.ts";
import { AttemptTracker } from "../components/AttemptStatus.tsx";
import { Icon } from "../components/Brand.tsx";
import { Code } from "../components/Code.tsx";
import { Badge, ErrorNote, Skeleton, words } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { formatZec } from "../money.ts";
import { useSession } from "../session.tsx";
import { useToast } from "../toast.tsx";
import type { AttemptAccepted, BountyPublic, EntryPoint } from "../types.ts";
import { AttemptRow, useSavedAttempts } from "./MyAttempts.tsx";

export const MAX_FILL = 20_000;

const SLOTS: Record<EntryPoint, { label: string; hint: string; placeholder: string }> = {
  document: {
    label: "Document the agent reads first",
    hint: "Placed in <document> tags at the start of the user turn. Good for indirect injection.",
    placeholder: "A runbook, an email, a web page: anything the agent would treat as material to read.",
  },
  user_message: {
    label: "User message",
    hint: "The user's turn in the conversation.",
    placeholder: "What the user says to the agent.",
  },
};

function Counter({ used }: { used: number }) {
  const left = MAX_FILL - used;
  return <span className={left < 500 ? "counter near" : "counter"}>{used.toLocaleString()} / 20,000</span>;
}

function AttemptForm({ bounty, onSubmitted }: { bounty: BountyPublic; onSubmitted: (a: AttemptAccepted) => void }) {
  const [fills, setFills] = useState<Record<EntryPoint, string>>({ document: "", user_message: "" });
  const [address, setAddress] = useState(savedPayoutAddress);
  const [keep, setKeep] = useState(() => savedPayoutAddress() !== "");
  const action = useAction();
  const slots = (["document", "user_message"] as const).filter((slot) => bounty.entry_points.includes(slot));
  const account = useAccount().data;
  const metered = account?.invite_only === true;
  const size = slots.reduce((sum, slot) => sum + fills[slot].length, 0);
  const cost = account ? costOf(size, account.credits.costs) : 0;
  const short = metered && account !== null && cost > account.credits.balance;

  async function send() {
    await action.run(async () => {
      const filled = slots.filter((slot) => fills[slot]).map((slot) => ({ slot, content: fills[slot] }));
      if (filled.length === 0) throw new Error("Write a message or a document to submit.");
      const body = { payout_address: address.trim(), fills: filled };
      const accepted = await api<AttemptAccepted>("POST", `/v1/bounties/${bounty.id}/attempts`, { body });
      savePayoutAddress(keep ? address.trim() : "");
      accountChanged();
      onSubmitted(accepted);
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void send();
  }

  function shortcut(event: KeyboardEvent<HTMLFormElement>) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !action.busy) {
      event.preventDefault();
      event.currentTarget.requestSubmit();
    }
  }

  return (
    <section className="panel composer">
      <h2>Write your attack</h2>
      <form onSubmit={submit} onKeyDown={shortcut}>
        {slots.map((slot) => (
          <label key={slot}>
            <span className="label-row">
              {SLOTS[slot].label}
              <Counter used={fills[slot].length} />
            </span>
            <textarea
              rows={slot === "document" ? 6 : 5}
              maxLength={MAX_FILL}
              placeholder={SLOTS[slot].placeholder}
              value={fills[slot]}
              onChange={(event) => setFills((current) => ({ ...current, [slot]: event.target.value }))}
            />
            <small>{SLOTS[slot].hint}</small>
          </label>
        ))}
        <label>
          Shielded payout address
          <input
            required
            autoComplete="off"
            spellCheck={false}
            className="code"
            placeholder="A shielded Zcash address"
            value={address}
            onChange={(event) => setAddress(event.target.value)}
          />
          <small>The reward is sent here if the attack is verified. Transparent addresses are refused.</small>
        </label>
        <label className="check">
          <input type="checkbox" checked={keep} onChange={(event) => setKeep(event.target.checked)} />
          Remember this address in this browser
        </label>
        {metered && account && (
          <p className={short ? "cost short" : "cost"}>
            <Icon name="zap" size={14} />
            <span>
              This attack costs <b>{cost}</b> {cost === 1 ? "credit" : "credits"}. You have{" "}
              <b>{account.credits.balance}</b> of {account.credits.cap}.
              {short && ` Your allowance refills over ${account.credits.window_hours} hours.`}
            </span>
          </p>
        )}
        <ErrorNote>{action.error}</ErrorNote>
        <div className="form-foot">
          <button disabled={action.busy || short}>
            {action.busy ? "Submitting…" : metered ? `Submit attack · ${cost} ${cost === 1 ? "credit" : "credits"}` : "Submit attack"}
          </button>
          <span className="muted">
            <kbd>⌘</kbd> <kbd>Enter</kbd> to submit
          </span>
        </div>
      </form>
    </section>
  );
}

function Sidebar({ bounty }: { bounty: BountyPublic }) {
  const toast = useToast();
  const origin = window.location.origin;
  const slot = bounty.entry_points.includes("user_message") ? "user_message" : (bounty.entry_points[0] ?? "user_message");
  return (
    <aside className="bounty-side">
      <div className="panel reward-panel">
        <p className="label">Reward</p>
        <p className="reward">
          {formatZec(bounty.reward_zatoshi)} <span>ZEC</span>
        </p>
        <p className="muted">Paid automatically to the first hunter whose attack is verified.</p>
        <dl className="mini-stats">
          <div>
            <dt>Attacks</dt>
            <dd>{bounty.attempts}</dd>
          </div>
          <div>
            <dt>Breaks</dt>
            <dd>{bounty.breaks}</dd>
          </div>
          <div>
            <dt>To win</dt>
            <dd>3 of 5</dd>
          </div>
        </dl>
      </div>

      <div className="panel">
        <p className="label">You control</p>
        <ul className="plain">
          {bounty.entry_points.map((entry) => (
            <li key={entry}>
              <Icon name={entry === "document" ? "doc" : "chat"} size={15} />
              <span>
                <b>{words(entry)}</b>
                <small>{SLOTS[entry].hint}</small>
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="panel">
        <p className="label">Canaries to trip</p>
        <ul className="plain">
          {bounty.canaries.map((canary) => (
            <li key={`${canary.type}:${canary.public_label}`}>
              <Icon name={canary.type === "secret_leak" ? "key" : "ban"} size={15} />
              <span>
                <b className="code">{canary.public_label}</b>
                <small>
                  {canary.type === "secret_leak"
                    ? "Make the agent emit a planted secret, in any encoding."
                    : "Make the agent call a tool in a way its rules forbid."}
                </small>
              </span>
            </li>
          ))}
        </ul>
        <p className="muted small">
          <Link to="/docs#canaries">How canaries are matched →</Link>
        </p>
      </div>

      <div className="panel">
        <p className="label">Share</p>
        <button
          type="button"
          className="secondary full"
          onClick={() => {
            void navigator.clipboard.writeText(window.location.href).then(() => toast("Link copied"));
          }}
        >
          <Icon name="link" size={15} /> Copy link to this bounty
        </button>
      </div>

      <details className="panel api-panel">
        <summary>Attack from a script or an agent</summary>
        <Code>{`curl -X POST ${origin}/v1/bounties/${bounty.id}/attempts \\
  -H 'Content-Type: application/json' \\
  -d '{"payout_address": "<shielded address>",
       "fills": [{"slot": "${slot}", "content": "..."}]}'`}</Code>
        <p className="muted small">
          <Link to="/docs#hunters">Full guide →</Link>
        </p>
      </details>
    </aside>
  );
}

export function BountyPage() {
  const { bountyId = "" } = useParams();
  const bounty = useLoad(
    (signal) => api<BountyPublic>("GET", `/v1/bounties/${bountyId}`, { signal }),
    bountyId,
    `bounty:${bountyId}`,
  );
  const history = useSavedAttempts(bountyId);
  const providers = useProviders();
  const { session } = useSession();
  const location = useLocation();
  const [current, setCurrent] = useState<AttemptAccepted | null>(null);
  const toast = useToast();

  // Coming back to the page picks up an attempt that had not been judged yet.
  useEffect(() => {
    setCurrent(history.find((attempt) => !attempt.outcome) ?? null);
    // Only on arrival: afterwards `current` follows what is submitted here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bountyId]);

  const mustSignIn = providers?.invite_only === true && session?.signed_in === false;

  if (bounty.status === 404 || bounty.status === 422) return <NotFound message="That bounty does not exist." />;
  if (!bounty.data) {
    return bounty.error ? (
      <ErrorNote>{bounty.error}</ErrorNote>
    ) : (
      <div className="bounty-layout">
        <div>
          <Skeleton block lines={5} />
        </div>
        <Skeleton block lines={4} />
      </div>
    );
  }
  const data = bounty.data;
  const earlier = history.filter((attempt) => attempt.attempt_id !== current?.attempt_id);

  return (
    <>
      <p className="crumb">
        <Link to="/#bounties">← All bounties</Link>
      </p>
      <header className="bounty-head">
        <h1>
          {data.title} <Badge state={data.state} />
        </h1>
        {data.description && <p className="lede">{data.description}</p>}
      </header>

      <div className="bounty-layout">
        <div className="bounty-main">
          {data.accepting_attempts && mustSignIn ? (
            <section className="panel gate">
              <span className="gate-icon">
                <Icon name="lock" size={20} />
              </span>
              <h2>Sign in to attack this agent</h2>
              <p className="muted">
                trycanari is invite-only. Every attack is replayed five times against a real model, so each one
                spends credits from your account's daily allowance.
              </p>
              <div className="form-foot">
                <Link className="button" to="/login" state={{ from: location.pathname }}>
                  Sign in
                </Link>
                <Link to="/docs#invites">How invites and credits work →</Link>
              </div>
            </section>
          ) : data.accepting_attempts ? (
            <AttemptForm
              bounty={data}
              onSubmitted={(accepted) => {
                saveAttempt({
                  ...accepted,
                  bounty_id: data.id,
                  bounty_title: data.title,
                  submitted_at: new Date().toISOString(),
                });
                setCurrent(accepted);
                toast("Attack submitted. It is replayed five times.");
              }}
            />
          ) : (
            <div className="notice">
              <b>This bounty is {words(data.state)} and is not taking attempts.</b>{" "}
              {data.breaks > 0
                ? "A hunter broke it. It reopens once the sponsor has patched the agent."
                : "Its sponsor has stopped it for now."}{" "}
              <Link to="/#bounties">See what is live →</Link>
            </div>
          )}

          {current && (
            <AttemptTracker
              key={current.attempt_id}
              attempt={current}
              onSettled={() => {
                void bounty.reload();
                // A verified break moves the account up a tier, so its meter changes too.
                accountChanged();
              }}
            />
          )}

          {earlier.length > 0 && (
            <section>
              <h2>Your earlier attempts here</h2>
              <ul className="attempt-list">
                {earlier.map((attempt) => (
                  <AttemptRow key={attempt.attempt_id} attempt={attempt} showBounty={false} />
                ))}
              </ul>
            </section>
          )}
        </div>
        <Sidebar bounty={data} />
      </div>
    </>
  );
}
