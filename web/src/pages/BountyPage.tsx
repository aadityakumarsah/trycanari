import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { api } from "../api.ts";
import { AttemptTracker } from "../components/AttemptStatus.tsx";
import { Badge, Canaries, ErrorNote, words } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { formatZec } from "../money.ts";
import type { AttemptAccepted, BountyPublic } from "../types.ts";

const MAX_FILL = 20_000;

/** A hunter's attempt in progress, kept for this tab so a reload does not lose it. */
function remembered(bountyId: string): AttemptAccepted | null {
  try {
    const stored = sessionStorage.getItem(`attempt:${bountyId}`);
    return stored ? (JSON.parse(stored) as AttemptAccepted) : null;
  } catch {
    return null;
  }
}

function remember(bountyId: string, attempt: AttemptAccepted): void {
  try {
    sessionStorage.setItem(`attempt:${bountyId}`, JSON.stringify(attempt));
  } catch {
    // Storage can be unavailable; the attempt is still tracked until the page is left.
  }
}

function AttemptForm({ bounty, onSubmitted }: { bounty: BountyPublic; onSubmitted: (a: AttemptAccepted) => void }) {
  const [document, setDocument] = useState("");
  const [message, setMessage] = useState("");
  const [address, setAddress] = useState("");
  const action = useAction();

  async function submit(event: FormEvent) {
    event.preventDefault();
    await action.run(async () => {
      const fills = [
        { slot: "document", content: document },
        { slot: "user_message", content: message },
      ].filter((fill) => fill.content && bounty.entry_points.includes(fill.slot as never));
      if (fills.length === 0) throw new Error("Write a message or a document to submit.");
      const body = { payout_address: address.trim(), fills };
      onSubmitted(await api<AttemptAccepted>("POST", `/v1/bounties/${bounty.id}/attempts`, { body }));
    });
  }

  return (
    <section>
      <h2>Submit an attack</h2>
      <form onSubmit={(event) => void submit(event)}>
        {bounty.entry_points.includes("document") && (
          <label>
            Document the agent reads first
            <textarea rows={5} maxLength={MAX_FILL} value={document} onChange={(e) => setDocument(e.target.value)} />
          </label>
        )}
        {bounty.entry_points.includes("user_message") && (
          <label>
            User message
            <textarea rows={5} maxLength={MAX_FILL} value={message} onChange={(e) => setMessage(e.target.value)} />
          </label>
        )}
        <label>
          Shielded payout address
          <input
            required
            autoComplete="off"
            spellCheck={false}
            className="code"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
          <small>The reward is sent here if the attack is verified. Transparent addresses are refused.</small>
        </label>
        <ErrorNote>{action.error}</ErrorNote>
        <button disabled={action.busy}>{action.busy ? "Submitting…" : "Submit attack"}</button>
      </form>
    </section>
  );
}

export function BountyPage() {
  const { bountyId = "" } = useParams();
  const bounty = useLoad((signal) => api<BountyPublic>("GET", `/v1/bounties/${bountyId}`, { signal }), bountyId);
  const [attempt, setAttempt] = useState<AttemptAccepted | null>(() => remembered(bountyId));

  if (bounty.status === 404 || bounty.status === 422) return <NotFound message="That bounty does not exist." />;
  if (!bounty.data) return bounty.error ? <ErrorNote>{bounty.error}</ErrorNote> : <p className="muted">Loading…</p>;
  const data = bounty.data;

  return (
    <>
      <p className="crumb">
        <Link to="/">Bounties</Link>
      </p>
      <h1>
        {data.title} <Badge state={data.state} />
      </h1>
      {data.description && <p className="lede">{data.description}</p>}

      <dl className="facts">
        <div>
          <dt>Reward</dt>
          <dd>{formatZec(data.reward_zatoshi)} ZEC</dd>
        </div>
        <div>
          <dt>You control</dt>
          <dd>
            {data.entry_points.map((slot) => (
              <span key={slot} className="tag">
                {words(slot)}
              </span>
            ))}
          </dd>
        </div>
        <div>
          <dt>Canaries to trip</dt>
          <dd>
            <Canaries items={data.canaries} />
          </dd>
        </div>
      </dl>

      {data.accepting_attempts ? (
        <AttemptForm
          bounty={data}
          onSubmitted={(accepted) => {
            remember(bountyId, accepted);
            setAttempt(accepted);
          }}
        />
      ) : (
        <p className="notice">This bounty is {words(data.state)} and is not taking attempts.</p>
      )}

      {attempt && (
        <AttemptTracker key={attempt.attempt_id} attempt={attempt} onSettled={() => void bounty.reload()} />
      )}
    </>
  );
}
