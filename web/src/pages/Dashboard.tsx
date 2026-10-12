import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { completeness, inviteLink, pictureOf, useAccount } from "../account.tsx";
import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { ReferralGate } from "../components/ReferralGate.tsx";
import { Avatar, Badge, ErrorNote, firstName, Skeleton } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { formatZec, paidIn, parseZec, sym } from "../money.ts";
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
          Reward in {sym()}
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
  const called = firstName(account.name);
  const profile = completeness(account);
  return (
    <header className="welcome">
      <Avatar name={account.name} picture={pictureOf(account)} size={56} />
      <div>
        <h1>{called ? `Welcome back, ${called}` : "Welcome back"}</h1>
        <p className="muted">Break agents, get paid. List your own, and pay only for proof.</p>
      </div>
      <Link className="button secondary" to="/profile">
        {profile.percent === 100 ? "Your profile" : `Complete your profile · ${profile.percent}%`}
      </Link>
    </header>
  );
}

/** What to do next: the first break, which is also what unlocks the larger allowance. */
function Quest({ account }: { account: Account }) {
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
            <Icon name="coin" size={13} /> Reward: whatever the bounty pays, in {paidIn()}
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
        <Link className="quest-how" to="/docs#invites">
          How tiers and tokens work →
        </Link>
      </div>
    </section>
  );
}

function Tier({ account }: { account: Account }) {
  const { credits } = account;
  return (
    <section className="tier">
      <div className="tier-now">
        <span className="tier-badge">
          <Icon name="shield" size={20} />
        </span>
        <div>
          <p className="eyebrow">Your tier</p>
          <b>{credits.tier}</b>
          <small>{account.verified_breaks === 0 ? "Where every hunter starts" : "Earned with a verified break"}</small>
        </div>
      </div>
      <div className="tier-progress">
        <p>
          {credits.next_tier
            ? "Get one attack verified to move up a tier and be trusted with a larger daily allowance. You will see your tokens when you open a bounty to attack it."
            : "You are on the top tier, with the largest daily allowance. You will see your tokens when you open a bounty to attack it."}
        </p>
        <Link className="button secondary" to="/#bounties">
          Open a bounty <Icon name="chevron" size={15} />
        </Link>
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
              <small>After your first verified break</small>
              <Link to="/docs#invites">How tiers and tokens work</Link>
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

/** An ambassador's referral codes. Each one lets one person in, once. */
function Referrals() {
  const codes = useLoad((signal) => api<ReferralCode[]>("GET", "/v1/referral-codes", { signal }), "codes", "codes");
  const [count, setCount] = useState("1");
  const making = useAction();
  const toast = useToast();
  const list = codes.data ?? [];
  const unused = list.filter((item) => !item.used).length;

  async function make(event: FormEvent) {
    event.preventDefault();
    await making.run(async () => {
      const made = await api<ReferralCode[]>("POST", "/v1/referral-codes", { body: { count: Number(count) } });
      const links = made.map((item) => inviteLink(item.code)).join("\n");
      await navigator.clipboard.writeText(links).catch(() => undefined);
      await codes.reload();
      toast(made.length === 1 ? "Invite link created and copied" : `${made.length} invite links created and copied`);
    });
  }

  return (
    <section className="panel">
      <h2>Your invites</h2>
      <p className="muted">
        You are an ambassador. Each invite link lets <b>one</b> person take part in bounties, and stops working
        once they have used it. You can hold up to 25 unused at a time.
      </p>
      <form className="inline-form" onSubmit={(event) => void make(event)}>
        <label>
          How many
          <select value={count} onChange={(e) => setCount(e.target.value)}>
            {[1, 3, 5, 10].map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
        <button disabled={making.busy}>
          <Icon name="plus" size={15} /> {making.busy ? "Creating…" : "Create invite links"}
        </button>
        {list.length > 0 && (
          <span className="muted count-note">
            {unused} unused, {list.length - unused} used
          </span>
        )}
      </form>
      <ErrorNote>{making.error || codes.error}</ErrorNote>
      {codes.data?.length === 0 && <p className="empty">You have not made an invite yet.</p>}
      {list.length > 0 && (
        <ul className="codes">
          {list.map((item) => (
            <li key={item.code} className={item.used ? "spent" : undefined}>
              <code>{item.code}</code>
              <span className={item.used ? "badge" : "badge badge-live"}>{item.used ? "used" : "available"}</span>
              <span className="muted" />
              {!item.used && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    void navigator.clipboard.writeText(inviteLink(item.code)).then(() => toast("Invite link copied"));
                  }}
                >
                  <Icon name="link" size={14} /> Copy invite link
                </button>
              )}
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
      {account?.invite_only && !account.can_hunt && <ReferralGate compact />}
      {account?.invite_only && account.can_hunt && (
        <>
          <Quest account={account} />
          <Tier account={account} />
        </>
      )}
      {account?.is_ambassador && <Referrals />}

      {account && !account.can_sponsor && <ReferralGate compact kind="sponsor" />}
      <section className={account && !account.can_sponsor ? "create locked" : "create"}>
        <div className="create-earn">
          <p className="eyebrow">You pay</p>
          <b>Only for proof</b>
        </div>
        <div className="create-text">
          <h2>List an agent</h2>
          <p className="muted">Plant canaries, lock a reward, and let hunters red-team it for you.</p>
        </div>
        <div className="create-action">
          <button
            type="button"
            className="accent"
            onClick={() => setAdding(true)}
            disabled={adding || account?.can_sponsor === false}
            title={account?.can_sponsor === false ? "Listing an agent needs a sponsor code" : undefined}
          >
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
            <dd>{formatZec(escrow)} {sym()}</dd>
          </div>
        </dl>
      )}

      {adding && <NewBounty onCancel={() => setAdding(false)} />}
      {bounties.data?.length === 0 && !adding && (
        <div className="empty">
          <p>You have not listed an agent yet.</p>
          {account?.can_sponsor !== false && (
            <button type="button" className="text-button" onClick={() => setAdding(true)}>
              List your first agent
            </button>
          )}
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
                  {formatZec(bounty.reward_zatoshi)} <small>{sym()} reward</small>
                </span>
                <span className="row-escrow">
                  <small>
                    Escrow {formatZec(bounty.balance_zatoshi)} of {formatZec(bounty.required_zatoshi)} {sym()}
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
