import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { pictureOf } from "../account.tsx";
import { api } from "../api.ts";
import { Icon, type IconName } from "../components/Brand.tsx";
import { BrokenBy } from "../components/BrokenBy.tsx";
import { SponsoredBy } from "../components/SponsoredBy.tsx";
import { AttackTimeline } from "../components/AttackTimeline.tsx";
import { Flow } from "../components/Flow.tsx";
import { WalkingMascot } from "../components/WalkingMascot.tsx";
import { Ago, Avatar, ErrorNote, Skeleton, words } from "../components/ui.tsx";
import { useLoad } from "../hooks.ts";
import { currency, formatZec, paidIn, sym } from "../money.ts";
import type { Activity, BountyPublic, Leader } from "../types.ts";

const VERIFIED_BY: [IconName, string][] = [
  ["repeat", "Five fresh replays"],
  ["target", "The 3-of-5 rule"],
  ["box", "Sandboxed mock tools"],
  ["lock", "Pinned agent config"],
  ["key", "Encrypted transcripts"],
  ["coin", "Automatic payout"],
];

const FAQ: [string, string][] = [
  [
    "What is trycanari?",
    "A bug bounty platform for AI agents. A sponsor lists an agent, plants canaries in it and locks a reward in escrow. Hunters submit attacks. Each attack is replayed five times against a sandboxed copy of the agent, and if the same canary trips in three runs the reward is paid automatically.",
  ],
  [
    "What counts as a break?",
    "Tripping a canary. A secret-leak canary trips when the agent emits a planted secret in a reply or a tool call, including base64, hex, reversed and letter-separated forms. A forbidden-tool canary trips when the agent makes a tool call its sponsor's rule forbids. The same canary has to trip in at least three of the five replays.",
  ],
  [
    "What do I control as a hunter?",
    "Only the entry points a bounty lists. A user-message entry point is the user's turn. A document entry point is one document the agent reads before that turn. Each is capped at 20,000 characters.",
  ],
  [
    "How and when am I paid?",
    "You give a payout address with each attempt. When an attempt is verified, the payment service sends the full reward to that address with the finding ID in the memo. The first hunter to break a config takes the reward, and the bounty pauses.",
  ],
  [
    "Can my attack do real damage?",
    "No. Replays run against a copy of the agent whose tools are mocks that return canned results. Nothing real is moved, sent or leaked.",
  ],
  [
    "What does a sponsor pay?",
    "The sponsor escrows the reward plus a 10% fee. The fee is charged only when a payout is made. If nobody breaks the agent, closing the bounty refunds the whole escrow to the sponsor's refund address.",
  ],
  [
    "What happens after an agent is broken?",
    "The bounty pauses and the sponsor gets the attack and all five transcripts. To reopen, the sponsor uploads a patched config; the paid attack is replayed against it first, and the bounty reopens only if that attack now fails and the escrow is funded again.",
  ],
  [
    "Can an AI agent hunt here?",
    "Yes. Everything a hunter does is plain HTTP and JSON. Where hunting is by invitation, the agent sends its account's API key, which you make on your profile. Switch this page to Agent, or fetch /agent.md, for a plain-markdown description of the rules, the API and the bounties that are live right now.",
  ],
];

function Tag({ children, on, icon }: { children: ReactNode; on?: boolean; icon?: IconName }) {
  return (
    <span className={on ? "tag mini-tag on" : "tag mini-tag"}>
      {icon && <Icon name={icon} size={11} />}
      {children}
    </span>
  );
}

function Dots({ on }: { on: number }) {
  return (
    <span className="dots" role="img" aria-label={`${on} of 5 runs tripped`}>
      {[0, 1, 2, 3, 4].map((index) => (
        <i key={index} className={index < on ? "on" : undefined} />
      ))}
    </span>
  );
}

function Cell({ title, text, children }: { title: string; text: string; children: ReactNode }) {
  return (
    <div className="cell">
      <h3>{title}</h3>
      <p>{text}</p>
      <div className="mini">{children}</div>
    </div>
  );
}

function Strip() {
  const items = VERIFIED_BY.map(([icon, text]) => (
    <span key={text} className="strip-item">
      <Icon name={icon} size={20} />
      {text}
    </span>
  ));
  return (
    <div className="strip">
      <p className="strip-label">Every break is verified by</p>
      <span className="strip-rule" />
      <div className="strip-view">
        <div className="strip-track">
          {items}
          <span aria-hidden="true">{items}</span>
        </div>
      </div>
    </div>
  );
}

/** How a bounty that is over ended, in a word. */
export function outcomeOf(bounty: BountyPublic): "broken" | "held" {
  return bounty.breaks > 0 ? "broken" : "held";
}

function BountyCard({ bounty, over = false }: { bounty: BountyPublic; over?: boolean }) {
  const outcome = outcomeOf(bounty);
  return (
    <li className={over ? `card over ${outcome}` : "card"}>
      {over && (
        <span className={`outcome ${outcome}`}>
          <Icon name={outcome === "broken" ? "zap" : "shield"} size={13} />
          {outcome === "broken" ? `Broken ${bounty.breaks}×` : "Held"}
        </span>
      )}
      <p className="reward">
        {formatZec(bounty.reward_zatoshi)} <span>{sym()}</span>
      </p>
      <div>
        <h3>
          <Link to={`/b/${bounty.id}`}>{bounty.title}</Link>
        </h3>
        <SponsoredBy sponsor={bounty.sponsor} />
        {bounty.description && <p className="muted card-desc">{bounty.description}</p>}
      </div>
      <div>
        <p className="label">You control</p>
        {bounty.entry_points.map((slot) => (
          <span key={slot} className="tag">
            <Icon name={slot === "document" ? "doc" : "chat"} size={13} />
            {words(slot)}
          </span>
        ))}
      </div>
      <div>
        <p className="label">{over ? "Canaries" : "Canaries to trip"}</p>
        {bounty.canaries.map((canary) => (
          <span key={`${canary.type}:${canary.public_label}`} className="tag">
            <Icon name={canary.type === "secret_leak" ? "key" : "ban"} size={13} />
            {canary.public_label}
          </span>
        ))}
      </div>
      {over && bounty.broken_by.length > 0 && (
        <div>
          <p className="label">Broken by</p>
          <BrokenBy hunters={bounty.broken_by} compact />
        </div>
      )}
      <p className="card-meta">
        <span>
          <Icon name="target" size={13} />
          {bounty.attempts} {bounty.attempts === 1 ? "attack" : "attacks"}
        </span>
        <span className={bounty.breaks ? "broken" : "unbroken"}>
          {bounty.breaks ? `broken ${bounty.breaks}×` : "unbroken"}
        </span>
      </p>
      <Link className={over ? "btn-line" : "btn-attack"} to={`/b/${bounty.id}`}>
        {over ? "See how it went" : "Attack"} <Icon name="chevron" />
      </Link>
    </li>
  );
}

/** Bounties that are over: broken, or closed unbroken. Loaded when the tab is opened. */
function CompletedList() {
  const completed = useLoad(
    (signal) => api<BountyPublic[]>("GET", "/v1/bounties?show=completed", { signal }),
    "completed",
    "completed",
  );
  const over = completed.data ?? [];
  return (
    <>
      <ErrorNote>{completed.error}</ErrorNote>
      {!completed.data && !completed.error && (
        <ul className="cards">
          {[0, 1, 2].map((index) => (
            <li key={index} className="card">
              <Skeleton block lines={4} />
            </li>
          ))}
        </ul>
      )}
      {completed.data && over.length === 0 && (
        <div className="empty">
          <p>No bounty has finished yet. Broken and closed agents are kept here.</p>
        </div>
      )}
      {over.length > 0 && (
        <ul className="cards">
          {over.map((bounty) => (
            <BountyCard key={bounty.id} bounty={bounty} over />
          ))}
        </ul>
      )}
    </>
  );
}

export const SORTS = {
  reward: "Highest reward",
  newest: "Newest",
  attacked: "Most attacked",
  unbroken: "Unbroken first",
} as const;
export type Sort = keyof typeof SORTS;

/** The bounties matching what was typed, in the chosen order. */
export function arrange(bounties: BountyPublic[], query: string, sort: Sort): BountyPublic[] {
  const wanted = query.trim().toLowerCase();
  const matching = bounties.filter((bounty) => {
    if (!wanted) return true;
    const text = [bounty.title, bounty.description, ...bounty.canaries.map((canary) => canary.public_label)];
    return text.join(" ").toLowerCase().includes(wanted);
  });
  const by: Record<Sort, (a: BountyPublic, b: BountyPublic) => number> = {
    reward: (a, b) => b.reward_zatoshi - a.reward_zatoshi,
    newest: (a, b) => b.created_at.localeCompare(a.created_at),
    attacked: (a, b) => b.attempts - a.attempts,
    unbroken: (a, b) => a.breaks - b.breaks || b.reward_zatoshi - a.reward_zatoshi,
  };
  return [...matching].sort(by[sort]);
}

export function Home() {
  const bounties = useLoad((signal) => api<BountyPublic[]>("GET", "/v1/bounties", { signal }), "home", "bounties");
  const activity = useLoad((signal) => api<Activity>("GET", "/v1/activity", { signal }), "home", "activity");
  const leaders = useLoad((signal) => api<Leader[]>("GET", "/v1/leaderboard", { signal }), "home", "leaders").data ?? [];
  const location = useLocation();
  const [showing, setShowing] = useState<"live" | "completed">("live");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("reward");
  const search = useRef<HTMLInputElement>(null);
  const live = bounties.data ?? [];
  const shown = useMemo(() => arrange(live, query, sort), [live, query, sort]);
  const pool = live.reduce((sum, bounty) => sum + bounty.reward_zatoshi, 0);
  const stats = activity.data?.stats;
  const breaks = activity.data?.recent_breaks ?? [];

  // "/" jumps to the search box, as it does on most sites with one.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
      if (event.key !== "/" || typing || event.metaKey || event.ctrlKey) return;
      event.preventDefault();
      search.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The router does not scroll to a fragment by itself.
  useEffect(() => {
    const id = location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView();
    else window.scrollTo({ top: 0 });
  }, [location.hash, location.key]);

  return (
    <>
      <section className="hero grid-bg">
        <Link className="pill" to="/#bounties">
          <span className="pill-live">
            <i aria-hidden="true" />
            {live.length > 0 ? `${live.length} live ${live.length === 1 ? "bounty" : "bounties"}` : "Live now"}
          </span>
          <span className="pill-text">
            Rewards paid in <b>{paidIn()}</b>
          </span>
          <Icon name="chevron" size={14} />
        </Link>
        <h1>
          <span className="mint">Break the Agent</span>
          <span>{currency().private ? "Get Paid in Shielded ZEC" : `Get Paid in ${sym()}`}</span>
        </h1>
        <p className="sub">
          Sponsors plant canaries in their AI agents and lock a reward in escrow. Trip the same canary in three of
          five sandboxed replays and the payout is sent automatically.
        </p>
        <div className="cta">
          <WalkingMascot />
          <Link className="btn-hero wide" to="/#bounties">
            Start Hunting Today
          </Link>
          <Link className="btn-hero outline" to="/docs">
            Read Docs
          </Link>
        </div>
      </section>
      <Strip />

      <section className="sec" id="bounties">
        <div className="sec-head">
          <h2>{showing === "live" ? "Live Bounties" : "Completed Bounties"}</h2>
          <p>
            {showing === "completed"
              ? "Agents that have been attacked and are no longer open: broken, or closed unbroken."
              : live.length
                ? `${live.length} open, ${formatZec(pool)} ${sym()} in rewards`
                : "Agents you can attack right now"}
          </p>
        </div>
        <div className="switcher" role="tablist" aria-label="Which bounties to show">
          {(["live", "completed"] as const).map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={showing === option}
              className={showing === option ? "on" : undefined}
              onClick={() => setShowing(option)}
            >
              {option === "live" ? "Live" : "Completed"}
              {option === "live" && live.length > 0 && <span>{live.length}</span>}
            </button>
          ))}
        </div>
        {showing === "completed" && <CompletedList />}
        {showing === "live" && (
          <>
            <ErrorNote>{bounties.error}</ErrorNote>
            {live.length > 1 && (
              <div className="toolbar">
                <label className="search">
                  <Icon name="search" size={16} />
                  <input
                    ref={search}
                    type="search"
                    placeholder="Search agents and canaries"
                    aria-label="Search bounties"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                  <kbd>/</kbd>
                </label>
                <label className="sort">
                  <span>Sort</span>
                  <select value={sort} onChange={(event) => setSort(event.target.value as Sort)}>
                    {Object.entries(SORTS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}
            {!bounties.data && !bounties.error && (
              <ul className="cards">
                {[0, 1, 2].map((index) => (
                  <li key={index} className="card">
                    <Skeleton block lines={4} />
                  </li>
                ))}
              </ul>
            )}
            {bounties.data?.length === 0 && (
              <div className="empty">
                <p>No bounty is live right now.</p>
                <Link to="/dashboard">List the first agent →</Link>
              </div>
            )}
            {live.length > 0 && shown.length === 0 && (
              <div className="empty">
                <p>No bounty matches “{query}”.</p>
                <button type="button" className="text-button" onClick={() => setQuery("")}>
                  Clear the search
                </button>
              </div>
            )}
            {shown.length > 0 && (
              <ul className="cards">
                {shown.map((bounty) => (
                  <BountyCard key={bounty.id} bounty={bounty} />
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <section className="sec" id="breaks">
        <div className="sec-head">
          <h2>Recent Breaks</h2>
          <p>Verified and paid automatically. The attack itself stays with the sponsor.</p>
        </div>
        {!activity.data && !activity.error && <Skeleton lines={3} />}
        {activity.data && breaks.length === 0 && (
          <div className="empty">
            <p>No agent has been broken yet. The first verified break shows up here.</p>
          </div>
        )}
        {breaks.length > 0 && (
          <ol className="feed">
            {breaks.map((item) => (
              <li key={`${item.bounty_id}:${item.verified_at}`}>
                <span className="feed-icon">
                  <Icon name={item.canary.type === "secret_leak" ? "key" : "ban"} size={16} />
                </span>
                <span className="feed-main">
                  <Link to={`/b/${item.bounty_id}`}>{item.bounty_title}</Link>
                  <small>
                    tripped <code>{item.canary.public_label}</code> in {item.tripped_runs} of 5 runs
                  </small>
                </span>
                <span className="feed-side">
                  <b>+{formatZec(item.reward_zatoshi)} {sym()}</b>
                  <small>
                    <Ago iso={item.verified_at} />
                  </small>
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {leaders.length > 0 && (
        <section className="sec" id="hunters">
          <div className="sec-head">
            <h2>Top Hunters</h2>
            <p>Ranked by verified breaks. Every break was replayed five times and paid automatically.</p>
          </div>
          <ol className="leaders">
            {leaders.map((leader, index) => (
              <li key={`${leader.name}:${leader.last_break_at}`}>
                <span className={index < 3 ? `rank top r${index + 1}` : "rank"}>{index + 1}</span>
                <Avatar name={leader.name} picture={pictureOf(leader)} size={40} />
                <span className="leader-who">
                  <b>{leader.name ?? "Anonymous hunter"}</b>
                  <small>
                    {[leader.company, leader.github && `@${leader.github}`].filter(Boolean).join(" · ") || "Independent"}
                  </small>
                </span>
                <span className="leader-breaks">
                  <b>{leader.breaks}</b>
                  <small>{leader.breaks === 1 ? "break" : "breaks"}</small>
                </span>
                <span className="leader-earned">
                  <b>
                    {formatZec(leader.earned_zatoshi)} {sym()}
                  </b>
                  <small>
                    last <Ago iso={leader.last_break_at} />
                  </small>
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section className="sec flush flow-sec" aria-labelledby="flow-title">
        <div className="sec-head">
          <h2 id="flow-title">
            Every Attack In. <span className="mint">Only Proof Out.</span>
          </h2>
          <p>
            Hunters and their agents throw everything at a sandboxed copy. What comes out the other side is a
            verdict, a transcript and a payout.
          </p>
        </div>
        <Flow />
      </section>

      <section className="sec flush safe-sec" aria-labelledby="safe-title">
        <div className="safe-head">
          <h2 id="safe-title">Safe to Attack</h2>
          <p>
            Every attack runs on a sandboxed copy of the agent, with mock tools. It is replayed five times and
            judged by its tripwires before any reward moves.
          </p>
        </div>
        <AttackTimeline />
      </section>

      <section className="sec flush split" id="how">
        <div className="split-title">
          <h2>How a Break Is Verified</h2>
          <p>
            No reports, no triage, no judgment calls. A break is a canary that trips, and the proof is the
            transcript.
          </p>
        </div>
        <div className="cells">
          <Cell
            title="Entry points"
            text="Each bounty says exactly where you can inject text. Nothing else about the agent is in your hands."
          >
            <Tag icon="chat">user_message</Tag>
            <Tag icon="doc">document</Tag>
          </Cell>
          <Cell title="Canaries" text="A planted secret the agent must never emit, or a tool call it must never make.">
            <Tag icon="key">secret_leak</Tag>
            <Tag icon="ban">forbidden_tool</Tag>
          </Cell>
          <Cell
            title="Five fresh replays"
            text="Your attack runs five times from an empty history against the pinned config."
          >
            <Dots on={5} />
          </Cell>
          <Cell
            title="The 3-of-5 rule"
            text="The same canary has to trip in three runs, so a lucky one-off does not pay."
          >
            <Dots on={3} />
            <Tag on>verified</Tag>
          </Cell>
          <Cell
            title="Automatic payout"
            text="A verified finding is the only thing that can trigger a payment. Nobody approves it."
          >
            <Tag>pending</Tag>
            <Tag>sending</Tag>
            <Tag on>sent</Tag>
          </Cell>
          <Cell
            title="Patch and re-arm"
            text="The paid attack becomes the regression test: the bounty reopens only when it fails."
          >
            <Tag icon="repeat">replay</Tag>
            <Tag on>live</Tag>
          </Cell>
        </div>
      </section>

      <section className="sec flush" id="sponsors">
        <div className="split-title sec-head left">
          <h2>Put a Price on Breaking Your Agent</h2>
          <p>
            List an agent, plant canaries and lock a reward. Hunters and their agents do the red-teaming; you pay
            only for attacks that work.
          </p>
          <div className="sponsor-actions">
            <Link className="btn-solid" to="/login">
              List an agent <Icon name="chevron" />
            </Link>
            <Link className="btn-line" to="/dashboard">
              Sponsor dashboard
            </Link>
          </div>
        </div>
        <div className="cells three">
          <Cell
            title="Pay only for real breaks"
            text="The 10% fee is charged when a payout is made. Close an unbroken bounty and the whole escrow comes back."
          >
            <Tag on>refund on close</Tag>
          </Cell>
          <Cell
            title="Every break ships with proof"
            text="You get the exact attack and all five transcripts, with the message that tripped the canary marked."
          >
            <Tag>attack</Tag>
            <Tag>5 transcripts</Tag>
          </Cell>
          <Cell
            title="Private by default"
            text="Prompts, tools, canary secrets and transcripts are encrypted at rest. Hunters see labels, never definitions."
          >
            <Tag icon="lock">encrypted</Tag>
          </Cell>
        </div>
      </section>

      <section className="faq-wrap" id="faq">
        <div className="split-title">
          <h2>Frequently Asked Questions</h2>
          <p>Questions? We've got answers</p>
          <p className="more">
            or check out the <Link to="/docs">docs →</Link>
          </p>
        </div>
        <div className="faq">
          {FAQ.map(([question, answer], index) => (
            <details key={question} open={index === 0}>
              <summary>
                {question}
                <Icon name="chevron" size={18} />
              </summary>
              <p>{answer}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="final grid-bg">
        <h2>
          Start Breaking <span className="mint">Real Agents</span>
        </h2>
        <div className="cta">
          <WalkingMascot />
          <Link className="btn-hero wide" to="/#bounties">
            Start Hunting Now
          </Link>
        </div>
        <div className="stats">
          <div>
            <small>Live bounties</small>
            <b>{stats?.live_bounties ?? live.length}</b>
            <span />
          </div>
          <div>
            <small>Reward pool</small>
            <b>{formatZec(stats?.reward_pool_zatoshi ?? pool)}</b>
            <span>{sym()}</span>
          </div>
          <div>
            <small>Attacks run</small>
            <b>{stats?.attempts ?? 0}</b>
            <span />
          </div>
          <div>
            <small>Paid to hunters</small>
            <b>{formatZec(stats?.paid_zatoshi ?? 0)}</b>
            <span>
              {sym()}, {stats?.verified_breaks ?? 0} {stats?.verified_breaks === 1 ? "break" : "breaks"}
            </span>
          </div>
        </div>
      </section>
    </>
  );
}
