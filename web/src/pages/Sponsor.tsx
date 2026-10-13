import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { pictureOf, useAccount } from "../account.tsx";
import { api } from "../api.ts";
import { Icon, type IconName } from "../components/Brand.tsx";
import { Avatar, Badge, ErrorNote, Skeleton, when, words } from "../components/ui.tsx";
import { useAction, useInterval, useLoad } from "../hooks.ts";
import { formatZec, parseZec, sym } from "../money.ts";
import type { Account, Sponsorship, SponsorBreak, SponsorOverview } from "../types.ts";

/** The states a bounty can be in while it is still the sponsor's to act on. */
const OPEN = ["draft", "awaiting_deposit", "live", "paused"];

export const TABS = ["overview", "agents", "past", "breaks"] as const;
export type Tab = (typeof TABS)[number];

export function tabOf(asked: string | null): Tab {
  return TABS.includes(asked as Tab) ? (asked as Tab) : "overview";
}

/** Bounties still in play, newest first. */
export function openAgents(bounties: Sponsorship[]): Sponsorship[] {
  return bounties.filter((bounty) => OPEN.includes(bounty.state)).reverse();
}

/** Bounties that are over: closed, with any unspent escrow refunded. */
export function pastAgents(bounties: Sponsorship[]): Sponsorship[] {
  return bounties.filter((bounty) => bounty.state === "closed").reverse();
}

/** Whether anything on the page is moving, so it is worth asking again. */
export function isBusy(overview: SponsorOverview): boolean {
  return overview.bounties.some((bounty) => bounty.state === "live" || bounty.state === "awaiting_deposit");
}

/** What a bounty is doing right now, in a sentence a sponsor can act on. */
export function standing(bounty: Sponsorship): string {
  if (bounty.closing) return "Closing: the escrow comes back once attempts in flight finish.";
  switch (bounty.state) {
    case "draft":
      return bounty.model
        ? "A draft. Plant canaries and fund it to put it in front of hunters."
        : "A draft. Describe the agent, plant canaries, then fund it.";
    case "awaiting_deposit":
      return "Waiting for your deposit. It goes live by itself once the network confirms it.";
    case "live":
      return bounty.attempts === 0
        ? "Live. Hunters can attack it; nothing has come in yet."
        : `Live. ${bounty.attempts} ${bounty.attempts === 1 ? "attack" : "attacks"} so far.`;
    case "paused":
      return bounty.breaks > 0
        ? "Broken. Read the finding, patch the agent and re-arm to reopen."
        : "Paused. It takes no attacks until you resume it.";
    default:
      return "Closed. Nothing more happens to it.";
  }
}

function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string | undefined }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
      {hint && <small>{hint}</small>}
    </div>
  );
}

/** Who the sponsor is, and what they have put into the platform. */
function SponsorCard({ account, overview }: { account: Account; overview: SponsorOverview }) {
  const { totals } = overview;
  const line = [account.role, account.company].filter(Boolean).join(" at ");
  return (
    <aside className="sponsor-profile">
      <div className="sponsor-cover" aria-hidden="true" />
      <div className="sponsor-face">
        <Avatar name={account.name} picture={pictureOf(account)} size={88} />
        <span className="sponsor-pill">
          <Icon name="shield" size={12} /> Sponsor
        </span>
      </div>
      <h2>{account.name ?? "Your name"}</h2>
      <p className="sponsor-line">{line || "Add a role and company on your profile"}</p>
      <ul className="profile-links">
        <li>
          <Icon name="chat" size={15} />
          <span>{account.email}</span>
        </li>
        {account.github && (
          <li>
            <Icon name="github" size={15} />
            <span>
              <a href={`https://github.com/${account.github}`} target="_blank" rel="noreferrer">
                github.com/{account.github}
              </a>
            </span>
          </li>
        )}
        {account.website && (
          <li>
            <Icon name="globe" size={15} />
            <span>
              <a href={account.website} target="_blank" rel="noreferrer">
                {account.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}
              </a>
            </span>
          </li>
        )}
        <li>
          <Icon name="clock" size={15} />
          <span>Sponsor since {when(overview.member_since).split(",")[0]}</span>
        </li>
      </ul>
      <dl className="profile-stats">
        <Stat label="Agents" value={totals.bounties} />
        <Stat label="Live" value={totals.live} />
        <Stat label="Breaks" value={totals.breaks} />
      </dl>
      <div className="sponsor-escrow">
        <p className="eyebrow">In escrow</p>
        <b>
          {formatZec(totals.escrow_zatoshi)} <span>{sym()}</span>
        </b>
        <small>
          Paid out {formatZec(totals.paid_zatoshi)} {sym()} for {totals.breaks}{" "}
          {totals.breaks === 1 ? "break" : "breaks"}
        </small>
      </div>
      <Link className="button secondary sponsor-edit" to="/profile">
        <Icon name="user" size={15} /> Edit your details
      </Link>
    </aside>
  );
}

/** A bounty as a card: what it is, where it stands, and what it has cost. */
function AgentCard({ bounty }: { bounty: Sponsorship }) {
  const due = Math.max(bounty.required_zatoshi - bounty.balance_zatoshi, 0);
  return (
    <li className="agent-card">
      <Link to={`/dashboard/b/${bounty.id}`}>
        <div className="agent-top">
          <b>{bounty.title}</b>
          <Badge state={bounty.closing ? "closing" : bounty.state} />
        </div>
        <p className="agent-standing">{standing(bounty)}</p>
        <dl className="agent-facts">
          <Stat label="Reward" value={`${formatZec(bounty.reward_zatoshi)} ${sym()}`} />
          <Stat label="Attacks" value={bounty.attempts} />
          <Stat label="Breaks" value={bounty.breaks} />
          <Stat
            label={bounty.state === "closed" ? "Paid out" : "Escrow"}
            value={
              bounty.state === "closed"
                ? `${formatZec(bounty.paid_zatoshi)} ${sym()}`
                : `${formatZec(bounty.balance_zatoshi)} ${sym()}`
            }
            hint={due > 0 && bounty.state !== "closed" ? `${formatZec(due)} ${sym()} to go` : undefined}
          />
        </dl>
        <p className="agent-meta">
          {bounty.model ? (
            <span>
              <Icon name="bot" size={13} /> {bounty.model}
            </span>
          ) : (
            <span className="warn-text">
              <Icon name="eye" size={13} /> No agent described yet
            </span>
          )}
          <span>
            <Icon name="target" size={13} /> {bounty.canaries} {bounty.canaries === 1 ? "canary" : "canaries"}
          </span>
          {bounty.entry_points.length > 0 && (
            <span>
              <Icon name="layers" size={13} /> {bounty.entry_points.map(words).join(", ")}
            </span>
          )}
          <span>
            <Icon name="clock" size={13} />{" "}
            {bounty.last_attack_at ? `Last attack ${when(bounty.last_attack_at)}` : `Listed ${when(bounty.created_at)}`}
          </span>
        </p>
      </Link>
    </li>
  );
}

/** One verified break: who did it, what fell, and what it cost. */
function BreakRow({ found }: { found: SponsorBreak }) {
  const who = found.house ? "The house hunter" : (found.hunter?.name ?? "A hunter with no account");
  const line = found.hunter ? [found.hunter.role, found.hunter.company].filter(Boolean).join(" at ") : "";
  const paid = found.payout_state === "sent" || found.payout_state === "confirmed";
  return (
    <li className="break-row">
      <Avatar name={found.hunter?.name ?? null} picture={found.hunter ? pictureOf(found.hunter) : null} size={44} />
      <div className="break-who">
        <b>{who}</b>
        <small>
          {found.house ? (
            "trycanari's own attacker. Nothing was paid for it."
          ) : (
            <>
              {line && <span>{line} · </span>}
              {found.hunter?.github && (
                <a href={`https://github.com/${found.hunter.github}`} target="_blank" rel="noreferrer">
                  @{found.hunter.github}
                </a>
              )}
            </>
          )}
        </small>
      </div>
      <div className="break-what">
        <Link to={`/dashboard/b/${found.bounty_id}?tab=findings`}>{found.bounty_title}</Link>
        <small>
          tripped <code>{found.canary.public_label}</code> in {found.tripped_runs} of {found.total_runs} replays ·{" "}
          {when(found.verified_at)}
        </small>
      </div>
      <div className="break-cost">
        {found.house ? (
          <small className="stage review">Not paid</small>
        ) : (
          <>
            <b>
              {formatZec(found.reward_zatoshi)} <span>{sym()}</span>
            </b>
            <small className={paid ? "stage paid" : "stage review"}>{paid ? "Paid" : "Payout under way"}</small>
          </>
        )}
      </div>
    </li>
  );
}

/** List an agent: the draft a sponsor starts from. */
function NewAgent({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [reward, setReward] = useState("");
  const creating = useAction();

  async function create(event: FormEvent) {
    event.preventDefault();
    await creating.run(async () => {
      const body = { title: title.trim(), description: description.trim(), reward_zatoshi: parseZec(reward) };
      const made = await api<{ id: string }>("POST", "/v1/bounties", { body });
      navigate(`/dashboard/b/${made.id}?tab=agent`);
    });
  }

  return (
    <section className="panel">
      <h2>List an agent</h2>
      <p className="muted">
        This makes a draft. Next you describe the agent, plant canaries and fund the reward. Nothing is public
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
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          Public description
          <textarea
            rows={3}
            maxLength={2000}
            placeholder="What the agent does, and what you want hunters to try."
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <small>Hunters see this.</small>
        </label>
        <label>
          Reward in {sym()}
          <input
            required
            inputMode="decimal"
            placeholder="0.5"
            value={reward}
            onChange={(event) => setReward(event.target.value)}
          />
          <small>You escrow the reward plus a 10% fee. The fee is charged only if a hunter is paid.</small>
        </label>
        <ErrorNote>{creating.error}</ErrorNote>
        <div className="form-foot">
          <button disabled={creating.busy}>{creating.busy ? "Creating…" : "Create draft"}</button>
          <button type="button" className="secondary" onClick={onDone}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}

function Empty({ icon, children }: { icon: IconName; children: ReactNode }) {
  return (
    <div className="empty sponsor-empty">
      <Icon name={icon} size={22} />
      <p>{children}</p>
    </div>
  );
}

/** Everything a sponsor has listed, how each is faring, and who broke what. */
export function Sponsor() {
  const account = useAccount().data;
  const [params, setParams] = useSearchParams();
  const tab = tabOf(params.get("tab"));
  const [adding, setAdding] = useState(false);
  const page = useLoad<SponsorOverview>(
    (signal) => api<SponsorOverview>("GET", "/v1/sponsor/overview", { signal }),
    "",
    "sponsor:overview",
  );
  const { reload } = page;
  useInterval(() => void reload(), 5000, page.data !== null && isBusy(page.data));

  if (!page.data || !account) {
    return page.error ? <ErrorNote>{page.error}</ErrorNote> : <Skeleton block lines={6} />;
  }
  const overview = page.data;
  const { totals } = overview;
  const open = openAgents(overview.bounties);
  const past = pastAgents(overview.bounties);
  const counts: Record<Tab, number | null> = {
    overview: null,
    agents: open.length,
    past: past.length,
    breaks: overview.breaks.length,
  };

  function show(next: Tab) {
    setParams(next === "overview" ? {} : { tab: next }, { replace: true });
    setAdding(false);
  }

  return (
    <>
      <p className="crumb">
        <Link to="/dashboard">← Dashboard</Link>
      </p>
      <div className="sponsor-layout">
        <SponsorCard account={account} overview={overview} />

        <div className="sponsor-main">
          <header className="page-head">
            <div>
              <p className="eyebrow">Sponsor</p>
              <h1>Your agents</h1>
            </div>
            <button type="button" className="accent" onClick={() => setAdding(true)} disabled={!account.can_sponsor}>
              <Icon name="plus" size={15} /> List an agent
            </button>
          </header>
          {!account.can_sponsor && (
            <p className="notice">Listing an agent needs a sponsor code. Enter one on your dashboard.</p>
          )}

          <div className="tabs" role="tablist" aria-label="Sponsor">
            {TABS.map((name) => (
              <button
                key={name}
                type="button"
                role="tab"
                aria-selected={tab === name}
                className={tab === name ? "current" : undefined}
                onClick={() => show(name)}
              >
                {name === "agents" ? "Agents" : name === "past" ? "Past" : name === "breaks" ? "Breaks" : "Overview"}
                {counts[name] !== null && <span className="count">{counts[name]}</span>}
              </button>
            ))}
          </div>

          {adding && <NewAgent onDone={() => setAdding(false)} />}

          <div role="tabpanel">
            {tab === "overview" && (
              <section>
                <dl className="facts sponsor-totals">
                  <Stat label="Agents listed" value={totals.bounties} hint={`${totals.live} live now`} />
                  <Stat label="Attacks received" value={totals.attempts} />
                  <Stat label="Verified breaks" value={totals.breaks} />
                  <Stat
                    label="Paid to hunters"
                    value={`${formatZec(totals.paid_zatoshi)} ${sym()}`}
                    hint={`${formatZec(totals.escrow_zatoshi)} ${sym()} still in escrow`}
                  />
                </dl>
                <p className="muted">
                  {totals.bounties === 0
                    ? "You have not listed an agent yet. Listing one puts it in front of hunters, and you pay only for attacks that provably work."
                    : `${totals.drafts} in preparation, ${totals.live} live, ${totals.paused} waiting for a patch, ${totals.past} finished.`}
                </p>
                {open.length > 0 && (
                  <>
                    <div className="page-head sub">
                      <h2>In play</h2>
                      <button type="button" className="text-button" onClick={() => show("agents")}>
                        See all {open.length}
                      </button>
                    </div>
                    <ul className="agent-cards">
                      {open.slice(0, 2).map((bounty) => (
                        <AgentCard key={bounty.id} bounty={bounty} />
                      ))}
                    </ul>
                  </>
                )}
                {overview.breaks.length > 0 && (
                  <>
                    <div className="page-head sub">
                      <h2>Latest break</h2>
                      <button type="button" className="text-button" onClick={() => show("breaks")}>
                        See all {overview.breaks.length}
                      </button>
                    </div>
                    <ul className="break-rows">
                      <BreakRow found={overview.breaks[0]!} />
                    </ul>
                  </>
                )}
              </section>
            )}

            {tab === "agents" && (
              <section>
                <h2>Agents in play</h2>
                {open.length === 0 ? (
                  <Empty icon="grid">
                    Nothing in play. List an agent and it appears here as a draft, then live once it is funded.
                  </Empty>
                ) : (
                  <ul className="agent-cards">
                    {open.map((bounty) => (
                      <AgentCard key={bounty.id} bounty={bounty} />
                    ))}
                  </ul>
                )}
              </section>
            )}

            {tab === "past" && (
              <section>
                <h2>Past agents</h2>
                <p className="muted">
                  Agents whose bounties are over. Their findings, transcripts and ledgers are kept, and their
                  unspent escrow was refunded when they closed.
                </p>
                {past.length === 0 ? (
                  <Empty icon="clock">No agent of yours has finished its bounty yet.</Empty>
                ) : (
                  <ul className="agent-cards">
                    {past.map((bounty) => (
                      <AgentCard key={bounty.id} bounty={bounty} />
                    ))}
                  </ul>
                )}
              </section>
            )}

            {tab === "breaks" && (
              <section>
                <h2>Who broke your agents</h2>
                <p className="muted">
                  Every verified break: the same canary tripped in at least 3 of 5 replays. Open a break to read
                  the attack and every transcript.
                </p>
                {overview.breaks.length === 0 ? (
                  <Empty icon="shield">
                    Nobody has broken an agent of yours. Every attack so far was held off.
                  </Empty>
                ) : (
                  <ul className="break-rows">
                    {overview.breaks.map((found) => (
                      <BreakRow key={found.id} found={found} />
                    ))}
                  </ul>
                )}
              </section>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

export default Sponsor;
