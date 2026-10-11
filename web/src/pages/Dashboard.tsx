import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAccount } from "../account.tsx";
import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { Badge, ErrorNote, Skeleton } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { formatZec, parseZec } from "../money.ts";
import { useToast } from "../toast.tsx";
import type { Account, ReferralCode, SponsorBounty } from "../types.ts";

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

function Welcome({ account }: { account: Account }) {
  const name = account.email.split("@")[0] ?? account.email;
  return (
    <header className="welcome">
      <img src="/app/brand/mascot.png" alt="" width={52} height={52} />
      <div>
        <h1>Welcome back, {name}</h1>
        <p className="muted">Break agents, get paid. List your own, and pay only for proof.</p>
      </div>
      <Link className="button secondary" to="/docs">
        Read the docs
      </Link>
    </header>
  );
}

/** What to do next: the first break, which is also what unlocks the larger allowance. */
function Quest({ account }: { account: Account }) {
  const [open, setOpen] = useState(false);
  const { credits } = account;
  const proven = credits.next_tier === null;
  return (
    <section className="quest">
      <div className="quest-top">
        <span className="quest-icon">
          <Icon name="target" size={22} />
        </span>
        <div className="quest-text">
          <p className="eyebrow">{proven ? "Quest complete" : "Your first quest"}</p>
          <h2>{proven ? "You are a proven hunter" : "Break your first agent"}</h2>
          <p className="muted">
            {proven
              ? `${account.verified_breaks} verified ${account.verified_breaks === 1 ? "break" : "breaks"} to your name. Keep hunting.`
              : "Pick a live bounty, trip the same canary in 3 of 5 replays, and the reward is paid to you automatically."}
          </p>
          <span className="chip">
            <Icon name="coin" size={13} /> Reward: whatever the bounty pays, in shielded ZEC
          </span>
        </div>
        <Link className="button accent" to="/#bounties">
          {proven ? "Find a bounty" : "Start hunting"} <Icon name="chevron" size={15} />
        </Link>
      </div>
      <div className="quest-bottom">
        <span className="quest-icon small">
          <Icon name={proven ? "check" : "lock"} size={16} />
        </span>
        <div className="quest-text">
          <p className="eyebrow">{proven ? "Unlocked" : "Next, after your first verified break"}</p>
          <p>
            <b>Proven tier</b>{" "}
            <span className="chip muted-chip">
              {proven ? "Active on your account" : "Unlocked when an attack of yours is verified"}
            </span>
          </p>
        </div>
        <div className="quest-amount">
          <small>Daily allowance</small>
          <b>{credits.next_cap ?? credits.cap}</b>
        </div>
        <button
          type="button"
          className="icon-button round"
          aria-expanded={open}
          aria-label="How the allowance works"
          onClick={() => setOpen((shown) => !shown)}
        >
          <Icon name={open ? "up" : "down"} size={16} />
        </button>
      </div>
      {open && (
        <div className="quest-more">
          <p>
            Every attack is replayed five times against a real model, which costs money. So each attack spends
            credits from your allowance for the last {credits.window_hours} hours:
          </p>
          <ul>
            {credits.costs.map((step) => (
              <li key={step.up_to_characters}>
                <b>
                  {step.credits} {step.credits === 1 ? "credit" : "credits"}
                </b>{" "}
                for an attack of up to {step.up_to_characters.toLocaleString()} characters
              </li>
            ))}
          </ul>
          <p>
            Credits come back by themselves as attempts get older than {credits.window_hours} hours, and a replay
            the platform fails to run costs nothing.
          </p>
        </div>
      )}
    </section>
  );
}

function Tier({ account }: { account: Account }) {
  const { credits } = account;
  const used = credits.cap - credits.balance;
  return (
    <section className="tier">
      <div className="tier-now">
        <span className="tier-badge">
          <Icon name="shield" size={20} />
        </span>
        <div>
          <p className="eyebrow">Your tier</p>
          <b>{credits.tier}</b>
          <small>
            <Icon name="zap" size={12} /> {credits.cap} credits every {credits.window_hours} hours
          </small>
        </div>
      </div>
      <div className="tier-progress">
        <p>
          {credits.next_tier
            ? "Get one attack verified to move up. Until then, spend your credits on your best ideas."
            : "You are on the top tier. Your allowance refills as attempts age out."}
        </p>
        <div className="tier-bar">
          <span className="meter" role="img" aria-label={`${credits.balance} of ${credits.cap} credits left`}>
            <i className={`fill p${Math.round((credits.balance / credits.cap) * 10) * 10}`} />
          </span>
          <span className="tier-count">
            {credits.balance} / {credits.cap} left
          </span>
        </div>
        <small>{used === 0 ? "Nothing spent in this window." : `${used} spent in the last ${credits.window_hours} hours.`}</small>
      </div>
      <div className="tier-next">
        {credits.next_tier ? (
          <>
            <span className="tier-badge next">
              <Icon name="shield" size={20} />
            </span>
            <div>
              <p className="eyebrow">Next tier</p>
              <b>{credits.next_tier}</b>
              <small>{credits.next_cap} credits every {credits.window_hours} hours</small>
              <Link to="/docs#invites">How tiers and credits work</Link>
            </div>
          </>
        ) : (
          <div>
            <p className="eyebrow">Verified breaks</p>
            <b>{account.verified_breaks}</b>
            <Link to="/attempts">See your attempts</Link>
          </div>
        )}
      </div>
    </section>
  );
}

/** An ambassador's referral codes: each admits a fixed number of new people. */
function Referrals() {
  const codes = useLoad((signal) => api<ReferralCode[]>("GET", "/v1/referral-codes", { signal }), "codes", "codes");
  const [uses, setUses] = useState("5");
  const making = useAction();
  const toast = useToast();

  async function make(event: FormEvent) {
    event.preventDefault();
    await making.run(async () => {
      const made = await api<ReferralCode>("POST", "/v1/referral-codes", { body: { max_uses: Number(uses) } });
      await codes.reload();
      toast(`Code ${made.code} created`);
    });
  }

  return (
    <section className="panel">
      <h2>Your referral codes</h2>
      <p className="muted">
        You are an ambassador. Each code lets a fixed number of new people join; give them out to hunters and
        sponsors you trust.
      </p>
      <form className="inline-form" onSubmit={(event) => void make(event)}>
        <label>
          People it admits
          <select value={uses} onChange={(e) => setUses(e.target.value)}>
            {[1, 3, 5, 10, 25].map((count) => (
              <option key={count} value={count}>
                {count}
              </option>
            ))}
          </select>
        </label>
        <button disabled={making.busy}>
          <Icon name="plus" size={15} /> New code
        </button>
      </form>
      <ErrorNote>{making.error || codes.error}</ErrorNote>
      {codes.data?.length === 0 && <p className="empty">You have not made a code yet.</p>}
      {codes.data && codes.data.length > 0 && (
        <ul className="codes">
          {codes.data.map((item) => (
            <li key={item.code}>
              <code>{item.code}</code>
              <span className="muted">
                {item.used} of {item.max_uses} used
              </span>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  const link = `${window.location.origin}/login?code=${encodeURIComponent(item.code)}`;
                  void navigator.clipboard.writeText(link).then(() => toast("Invite link copied"));
                }}
              >
                <Icon name="link" size={14} /> Copy invite link
              </button>
            </li>
          ))}
        </ul>
      )}
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
  const account = useAccount().data;
  const mine = bounties.data ?? [];
  const live = mine.filter((bounty) => bounty.state === "live").length;
  const escrow = mine.reduce((sum, bounty) => sum + bounty.balance_zatoshi, 0);

  return (
    <>
      {account && <Welcome account={account} />}
      {account?.invite_only && (
        <>
          <Quest account={account} />
          <Tier account={account} />
        </>
      )}
      {account?.is_ambassador && <Referrals />}

      <section className="create">
        <div className="create-earn">
          <p className="eyebrow">You pay</p>
          <b>Only for proof</b>
        </div>
        <div className="create-text">
          <h2>List an agent</h2>
          <p className="muted">Plant canaries, lock a reward, and let hunters red-team it for you.</p>
        </div>
        <div className="create-action">
          <button type="button" className="accent" onClick={() => setAdding(true)} disabled={adding}>
            Start listing <Icon name="chevron" size={15} />
          </button>
          <Link to="/docs#sponsors">Need help? Read the guide</Link>
        </div>
      </section>

      <div className="page-head sub">
        <h2>Your bounties</h2>
        <span className="muted">{mine.length} total</span>
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

      {adding && <NewBounty onCancel={() => setAdding(false)} />}
      {bounties.data?.length === 0 && !adding && (
        <div className="empty">
          <p>You have not listed an agent yet.</p>
          <button type="button" className="text-button" onClick={() => setAdding(true)}>
            List your first agent
          </button>
        </div>
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
