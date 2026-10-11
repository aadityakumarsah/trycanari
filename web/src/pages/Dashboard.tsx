import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { Badge, ErrorNote, Skeleton } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { formatZec, parseZec } from "../money.ts";
import type { SponsorBounty } from "../types.ts";

/** How much of the escrow a bounty needs is in place, as a whole percentage. */
export function funded(bounty: Pick<SponsorBounty, "balance_zatoshi" | "required_zatoshi">): number {
  if (bounty.required_zatoshi <= 0) return 0;
  return Math.max(0, Math.min(100, Math.floor((bounty.balance_zatoshi / bounty.required_zatoshi) * 100)));
}

function NewBounty({ onCancel }: { onCancel?: () => void }) {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [reward, setReward] = useState("");
  const creating = useAction();

  async function create(event: FormEvent) {
    event.preventDefault();
    await creating.run(async () => {
      const body = { title: title.trim(), description: description.trim(), reward_zatoshi: parseZec(reward) };
      const created = await api<{ id: string }>("POST", "/v1/bounties", { body });
      navigate(`/dashboard/b/${created.id}?tab=agent`);
    });
  }

  return (
    <section className="panel">
      <h2>New bounty</h2>
      <p className="muted">
        This creates a draft. Next you describe the agent, plant canaries and fund the reward; nothing is public
        until it goes live.
      </p>
      <form onSubmit={(event) => void create(event)}>
        <label>
          Title
          <input
            required
            autoFocus
            maxLength={120}
            placeholder="Treasury agent"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label>
          Public description
          <textarea
            rows={3}
            maxLength={2000}
            placeholder="What the agent does, and what you want hunters to try."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <small>Hunters see this: what the agent does and what you want them to try.</small>
        </label>
        <label>
          Reward in ZEC
          <input
            required
            inputMode="decimal"
            placeholder="0.5"
            value={reward}
            onChange={(e) => setReward(e.target.value)}
          />
          <small>You escrow the reward plus a 10% fee. The fee is charged only if a hunter is paid.</small>
        </label>
        <ErrorNote>{creating.error}</ErrorNote>
        <div className="form-foot">
          <button disabled={creating.busy}>{creating.busy ? "Creating…" : "Create draft"}</button>
          {onCancel && (
            <button type="button" className="secondary" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </section>
  );
}

export function Dashboard() {
  const bounties = useLoad(
    (signal) => api<SponsorBounty[]>("GET", "/v1/sponsor/bounties", { signal }),
    "mine",
    "sponsor:bounties",
  );
  const [adding, setAdding] = useState(false);
  const mine = bounties.data ?? [];
  const live = mine.filter((bounty) => bounty.state === "live").length;
  const escrow = mine.reduce((sum, bounty) => sum + bounty.balance_zatoshi, 0);

  return (
    <>
      <div className="page-head">
        <h1>Your bounties</h1>
        {mine.length > 0 && !adding && (
          <button type="button" onClick={() => setAdding(true)}>
            <Icon name="plus" size={15} /> New bounty
          </button>
        )}
      </div>
      <ErrorNote>{bounties.error}</ErrorNote>
      {!bounties.data && !bounties.error && <Skeleton block lines={2} />}

      {mine.length > 0 && (
        <dl className="facts">
          <div>
            <dt>Bounties</dt>
            <dd>{mine.length}</dd>
          </div>
          <div>
            <dt>Live now</dt>
            <dd>{live}</dd>
          </div>
          <div>
            <dt>In escrow</dt>
            <dd>{formatZec(escrow)} ZEC</dd>
          </div>
        </dl>
      )}

      {(adding || bounties.data?.length === 0) && (
        <NewBounty {...(mine.length > 0 ? { onCancel: () => setAdding(false) } : {})} />
      )}

      {mine.length > 0 && (
        <ul className="bounty-rows">
          {mine.map((bounty) => (
            <li key={bounty.id}>
              <Link to={`/dashboard/b/${bounty.id}`}>
                <span className="row-main">
                  <b>{bounty.title}</b>
                  <Badge state={bounty.closing ? "closing" : bounty.state} />
                </span>
                <span className="row-reward">
                  {formatZec(bounty.reward_zatoshi)} <small>ZEC reward</small>
                </span>
                <span className="row-escrow">
                  <small>
                    Escrow {formatZec(bounty.balance_zatoshi)} of {formatZec(bounty.required_zatoshi)} ZEC
                  </small>
                  <span className="meter" role="img" aria-label={`${funded(bounty)}% funded`}>
                    <i className={`fill p${Math.round(funded(bounty) / 10) * 10}`} />
                  </span>
                </span>
                <Icon name="chevron" size={16} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
