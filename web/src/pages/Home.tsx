import { useEffect, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { api } from "../api.ts";
import { Icon, type IconName } from "../components/Brand.tsx";
import { WalkingMascot } from "../components/WalkingMascot.tsx";
import { ErrorNote, words } from "../components/ui.tsx";
import { useLoad } from "../hooks.ts";
import { formatZec } from "../money.ts";
import type { BountyPublic } from "../types.ts";

const VERIFIED_BY: [IconName, string][] = [
  ["repeat", "Five fresh replays"],
  ["target", "The 3-of-5 rule"],
  ["box", "Sandboxed mock tools"],
  ["lock", "Pinned agent config"],
  ["key", "Encrypted transcripts"],
  ["coin", "Automatic shielded payout"],
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
    "You give a shielded Zcash address with each attempt. When an attempt is verified, the payment service sends the full reward to that address with the finding ID in the memo. The first hunter to break a config takes the reward, and the bounty pauses.",
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
    "Yes. Hunters use four public HTTP endpoints and need no account. Switch this page to Agent, or fetch /agent.md, for a plain-markdown description of the rules, the API and the bounties that are live right now.",
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

function BountyCard({ bounty }: { bounty: BountyPublic }) {
  return (
    <li className="card">
      <p className="reward">
        {formatZec(bounty.reward_zatoshi)} <span>ZEC</span>
      </p>
      <div>
        <h3>
          <Link to={`/b/${bounty.id}`}>{bounty.title}</Link>
        </h3>
        {bounty.description && <p className="muted">{bounty.description}</p>}
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
        <p className="label">Canaries to trip</p>
        {bounty.canaries.map((canary) => (
          <span key={`${canary.type}:${canary.public_label}`} className="tag">
            <Icon name={canary.type === "secret_leak" ? "key" : "ban"} size={13} />
            {canary.public_label}
          </span>
        ))}
      </div>
      <Link className="btn-attack" to={`/b/${bounty.id}`}>
        Attack <Icon name="chevron" />
      </Link>
    </li>
  );
}

export function Home() {
  const bounties = useLoad((signal) => api<BountyPublic[]>("GET", "/v1/bounties", { signal }), "home");
  const location = useLocation();
  const live = bounties.data ?? [];
  const pool = live.reduce((sum, bounty) => sum + bounty.reward_zatoshi, 0);

  // The router does not scroll to a fragment by itself.
  useEffect(() => {
    const id = location.hash.slice(1);
    if (id) document.getElementById(id)?.scrollIntoView();
    else window.scrollTo({ top: 0 });
  }, [location.hash, location.key]);

  return (
    <>
      <section className="hero grid-bg">
        <span className="pill">
          <i>Z</i>Paid in shielded Zcash
        </span>
        <h1>
          <span className="mint">Break the Agent</span>
          <span>Get Paid in Shielded ZEC</span>
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
          <a className="btn-hero outline" href="/docs">
            Read Docs
          </a>
        </div>
      </section>
      <Strip />

      <section className="sec" id="bounties">
        <div className="sec-head">
          <h2>Live Bounties</h2>
          <p>{live.length ? `${live.length} open, ${formatZec(pool)} ZEC in rewards` : "Agents you can attack right now"}</p>
        </div>
        <ErrorNote>{bounties.error}</ErrorNote>
        {bounties.data?.length === 0 && <p className="empty">No bounty is live right now.</p>}
        {live.length > 0 && (
          <ul className="cards">
            {live.map((bounty) => (
              <BountyCard key={bounty.id} bounty={bounty} />
            ))}
          </ul>
        )}
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
            or check out the <a href="/docs">API reference →</a>
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
            <b>{live.length}</b>
            <span />
          </div>
          <div>
            <small>Reward pool</small>
            <b>{formatZec(pool)}</b>
            <span>ZEC</span>
          </div>
          <div>
            <small>Replays per attack</small>
            <b>5</b>
            <span />
          </div>
          <div>
            <small>Trips to win</small>
            <b>3</b>
            <span>of 5</span>
          </div>
        </div>
      </section>
    </>
  );
}
