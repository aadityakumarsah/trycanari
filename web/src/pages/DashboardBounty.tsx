import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { pictureOf } from "../account.tsx";
import { api } from "../api.ts";
import { Transcript } from "../components/Transcript.tsx";
import { Icon } from "../components/Brand.tsx";
import { WalletFunding } from "../components/WalletFunding.tsx";
import { Ago, Avatar, Badge, Copyable, ErrorNote, Skeleton, when, words } from "../components/ui.tsx";
import { useAction, useInterval, useLoad } from "../hooks.ts";
import { currency, formatZec, sym } from "../money.ts";
import type { AttemptActivity, EntryPoint, Finding, Hunter, Ledger, SponsorBountyDetail } from "../types.ts";

interface Everything {
  bounty: SponsorBountyDetail;
  findings: Finding[];
  ledger: Ledger;
}

/** What the page needs to offer each form: the bounty, a way to act, and a way to refresh. */
interface Props {
  bounty: SponsorBountyDetail;
  act: (path: string, body?: unknown, method?: string) => Promise<void>;
}

const OPERATORS: [string, string][] = [
  ["not_in", "is not one of"],
  ["in", "is one of"],
  ["eq", "equals"],
  ["ne", "does not equal"],
  ["gt", "is greater than"],
  ["ge", "is at least"],
  ["lt", "is less than"],
  ["le", "is at most"],
];

function editable(bounty: SponsorBountyDetail): boolean {
  return (bounty.state === "draft" || bounty.state === "paused") && !bounty.closing;
}

/** A form whose submit runs one action and shows its error beneath the fields. */
function ActionForm({
  children,
  submit,
  label,
  className,
}: {
  children?: ReactNode;
  submit: () => Promise<void>;
  label: string;
  className?: string;
}) {
  const action = useAction();
  return (
    <form
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        void action.run(submit);
      }}
    >
      {children}
      <ErrorNote>{action.error}</ErrorNote>
      <button className={className} disabled={action.busy}>
        {label}
      </button>
    </form>
  );
}

function Actions({ bounty, act, broken }: Props & { broken: boolean }) {
  const open = bounty.state !== "closed" && !bounty.closing;
  return (
    <section>
      <h2>Actions</h2>
      <div className="actions">
        {bounty.state === "live" && !bounty.closing && <ActionForm submit={() => act("pause")} label="Pause" />}
        {editable(bounty) && (
          <ActionForm submit={() => act("activate")} label={bounty.state === "paused" ? "Resume" : "Go live"} />
        )}
        {open && (
          <ActionForm
            submit={async () => {
              if (confirm("Close this bounty and refund the unspent escrow? This cannot be undone.")) {
                await act("close", { refund_address: bounty.refund_address });
              }
            }}
            label="Close and refund"
            className="danger"
          />
        )}
      </div>
      {bounty.closing && (
        <p className="muted">
          Closing: the unspent escrow is refunded once attempts in flight have been judged and paid.
        </p>
      )}
      {broken && bounty.state === "paused" && !bounty.closing && <Rearm bounty={bounty} act={act} />}
    </section>
  );
}

function Rearm({ bounty, act }: Props) {
  const check = bounty.rearm;
  let body: ReactNode;
  if (bounty.config?.live) {
    body = (
      <p className="muted">
        This config was broken. Save a patched config, then replay the paid attack against it. The bounty can
        reopen only once that replay fails.
      </p>
    );
  } else if (check?.state === "passed") {
    body = (
      <p className="done">
        The paid attack no longer breaks this config ({check.tripped_runs} of 5 runs tripped). Fund the bounty to
        reopen it.
      </p>
    );
  } else if (check?.state === "failed") {
    body = (
      <p className="error">
        The paid attack still breaks this config ({check.tripped_runs} of 5 runs tripped). Patch it again.
      </p>
    );
  } else if (check) {
    body = <p className="notice">The paid attack is being replayed against this config…</p>;
  } else {
    body = (
      <>
        <ActionForm submit={() => act("rearm")} label="Replay the paid attack" />
        <p className="muted">The bounty can reopen only once the attack you paid for fails against the new config.</p>
      </>
    );
  }
  return (
    <>
      <h3>Re-arm</h3>
      {body}
    </>
  );
}

function Funding({ bounty, act, base, reload }: Props & { base: string; reload: () => Promise<void> }) {
  const [refund, setRefund] = useState(bounty.refund_address ?? "");
  const due = Math.max(bounty.required_zatoshi - bounty.balance_zatoshi, 0);
  const onSolana = !currency().private;
  const canFund = bounty.state === "awaiting_deposit" || editable(bounty);

  const byHand =
    bounty.state === "awaiting_deposit" ? (
      <>
        <p>
          Send <strong>{formatZec(due)} {sym()}</strong> to the escrow address, with the deposit code as the memo.
          The bounty goes live once the network has confirmed it. This page updates by itself.
        </p>
        <Copyable label="Escrow address" value={bounty.escrow_address} />
        <Copyable label="Deposit code (memo)" value={bounty.deposit_code} />
        {onSolana && (
          <p className="pay-link">
            <a
              className="button secondary"
              href={`solana:${bounty.escrow_address}?amount=${formatZec(due)}&memo=${encodeURIComponent(bounty.deposit_code)}&label=trycanari`}
            >
              Open in a Solana wallet app
            </a>
            <small className="muted">
              The memo matters: it is how your deposit is matched to this bounty.
            </small>
          </p>
        )}
      </>
    ) : editable(bounty) ? (
      <ActionForm submit={() => act("fund", { refund_address: refund.trim() })} label="Get deposit instructions">
        <label>
          Refund address
          <input
            required
            spellCheck={false}
            autoComplete="off"
            className="code"
            value={refund}
            onChange={(e) => setRefund(e.target.value)}
          />
          <small>
            Unspent escrow returns here when you close. You escrow the reward plus the fee; the fee is charged only
            if a payout is made.
          </small>
        </label>
      </ActionForm>
    ) : null;

  return (
    <section>
      <h2>Funding</h2>
      {!canFund ? (
        <p className="muted">
          {bounty.state === "live"
            ? `This bounty is funded and live. Escrow holds ${formatZec(bounty.balance_zatoshi)} ${sym()}.`
            : "Funding is available while the bounty is a draft or paused."}
        </p>
      ) : onSolana ? (
        <>
          <WalletFunding bounty={bounty} base={base} onMoved={reload} />
          <details className="by-hand">
            <summary>Pay another way: send it yourself</summary>
            {byHand}
          </details>
        </>
      ) : (
        byHand
      )}
    </section>
  );
}

function ConfigForm({ bounty, act }: Props) {
  const config = bounty.config;
  const [prompt, setPrompt] = useState(config?.system_prompt ?? "");
  const [model, setModel] = useState(config?.model ?? "");
  const [tools, setTools] = useState(JSON.stringify(config?.tools ?? [], null, 2));
  const [params, setParams] = useState(JSON.stringify(config?.params ?? {}, null, 2));
  const [slots, setSlots] = useState<EntryPoint[]>(config?.entry_points ?? ["user_message"]);

  function toggle(slot: EntryPoint) {
    setSlots((current) => (current.includes(slot) ? current.filter((s) => s !== slot) : [...current, slot]));
  }

  async function save() {
    let parsedTools: unknown;
    let parsedParams: unknown;
    try {
      parsedTools = JSON.parse(tools || "[]");
      parsedParams = JSON.parse(params || "{}");
    } catch {
      throw new Error("Tools and model params must be valid JSON.");
    }
    const body = {
      system_prompt: prompt,
      model: model.trim(),
      tools: parsedTools,
      params: parsedParams,
      entry_points: slots,
    };
    await act("config", body, "PUT");
  }

  return (
    <ActionForm submit={save} label="Save config">
      <label>
        System prompt
        <textarea rows={8} required spellCheck={false} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <small>Encrypted at rest and never shown to hunters.</small>
      </label>
      <label>
        Model
        <input required spellCheck={false} className="code" value={model} onChange={(e) => setModel(e.target.value)} />
      </label>
      <label>
        Tools (JSON)
        <textarea rows={6} spellCheck={false} value={tools} onChange={(e) => setTools(e.target.value)} />
        <small>
          A list of objects with <code>name</code>, <code>description</code>, <code>parameters</code> (JSON Schema)
          and <code>result</code>, the canned reply the mock returns. No real tool runs.
        </small>
      </label>
      <label>
        Model params (JSON)
        <textarea rows={2} spellCheck={false} value={params} onChange={(e) => setParams(e.target.value)} />
      </label>
      <fieldset>
        <legend>Where hunters can put text</legend>
        {(["user_message", "document"] as const).map((slot) => (
          <label key={slot} className="check">
            <input type="checkbox" checked={slots.includes(slot)} onChange={() => toggle(slot)} /> {words(slot)}
          </label>
        ))}
      </fieldset>
    </ActionForm>
  );
}

function Config({ bounty, act }: Props) {
  const config = bounty.config;
  return (
    <section>
      <h2>Agent config</h2>
      {config && (
        <p className="muted">
          Version {config.version}, {config.live ? "live" : "not live yet"}. Hash{" "}
          <code>{config.config_hash.slice(0, 16)}</code>
        </p>
      )}
      {editable(bounty) ? (
        // Keyed by the saved config, so the form picks up what the server now holds.
        <ConfigForm key={config?.config_hash ?? "new"} bounty={bounty} act={act} />
      ) : (
        config && (
          <>
            <pre>{config.system_prompt}</pre>
            <p className="muted">
              Model {config.model}. Entry points: {config.entry_points.map(words).join(", ")}. The config can be
              edited while the bounty is a draft or paused.
            </p>
          </>
        )
      )}
    </section>
  );
}

/** The value of a rule as typed into a text box. */
export function ruleValue(operator: string, raw: string): unknown {
  if (operator === "in" || operator === "not_in") {
    return raw
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
  }
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return raw.trim();
  }
}

function SecretCanaryForm({ act }: Pick<Props, "act">) {
  const [label, setLabel] = useState("");
  const [secret, setSecret] = useState("");
  return (
    <ActionForm
      submit={async () => {
        await act("canaries", { type: "secret_leak", public_label: label.trim(), secret });
        setLabel("");
        setSecret("");
      }}
      label="Add canary"
    >
      <h3>Add a secret that must never leak</h3>
      <label>
        Public label
        <input required maxLength={64} value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      <label>
        Secret
        <input
          required
          spellCheck={false}
          autoComplete="off"
          className="code"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
        />
        <small>At least 12 characters. It must appear in the system prompt or a tool result.</small>
      </label>
    </ActionForm>
  );
}

function ToolCanaryForm({ act }: Pick<Props, "act">) {
  const [label, setLabel] = useState("");
  const [tool, setTool] = useState("");
  const [argument, setArgument] = useState("");
  const [operator, setOperator] = useState("not_in");
  const [value, setValue] = useState("");
  return (
    <ActionForm
      submit={async () => {
        const body: Record<string, unknown> = {
          type: "forbidden_tool",
          public_label: label.trim(),
          tool: tool.trim(),
        };
        if (argument.trim()) {
          body.predicate = { argument: argument.trim(), operator, value: ruleValue(operator, value) };
        }
        await act("canaries", body);
        setLabel("");
      }}
      label="Add canary"
    >
      <h3>Add a tool call that must never happen</h3>
      <label>
        Public label
        <input required maxLength={64} value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      <label>
        Tool
        <input required spellCheck={false} className="code" value={tool} onChange={(e) => setTool(e.target.value)} />
      </label>
      <label>
        Argument
        <input spellCheck={false} className="code" value={argument} onChange={(e) => setArgument(e.target.value)} />
        <small>Leave empty to trip on any call to the tool.</small>
      </label>
      <label>
        Condition
        <select value={operator} onChange={(e) => setOperator(e.target.value)}>
          {OPERATORS.map(([name, text]) => (
            <option key={name} value={name}>
              {text}
            </option>
          ))}
        </select>
      </label>
      <label>
        Value
        <input spellCheck={false} className="code" value={value} onChange={(e) => setValue(e.target.value)} />
        <small>Comma-separated for “one of”.</small>
      </label>
    </ActionForm>
  );
}

function CanarySection({ bounty, act }: Props) {
  return (
    <section>
      <h2>Canaries</h2>
      {bounty.canaries.length === 0 ? (
        <p className="muted">No canaries yet. A bounty needs at least one.</p>
      ) : (
        <ul className="list">
          {bounty.canaries.map((canary) => (
            <li key={canary.id}>
              <strong>{canary.public_label}</strong> <span className="kind">{words(canary.type)}</span>{" "}
              <code>{canary.secret ?? JSON.stringify(canary.rule)}</code>
            </li>
          ))}
        </ul>
      )}
      {editable(bounty) && bounty.config && (
        <div className="columns">
          <SecretCanaryForm act={act} />
          <ToolCanaryForm act={act} />
        </div>
      )}
    </section>
  );
}

/** Who made the winning attack, as they describe themselves on their profile. */
function HunterCard({ hunter }: { hunter: Hunter }) {
  const line = [hunter.role, hunter.company].filter(Boolean).join(" at ");
  return (
    <div className="hunter-card">
      <Avatar name={hunter.name} picture={pictureOf(hunter)} size={44} />
      <div>
        <p className="eyebrow">Found by</p>
        <b>{hunter.name ?? "A hunter who has not added a name"}</b>
        {line && <small>{line}</small>}
      </div>
      <span className="hunter-links">
        {hunter.github && (
          <a href={`https://github.com/${hunter.github}`} target="_blank" rel="noreferrer">
            <Icon name="github" size={15} /> {hunter.github}
          </a>
        )}
        {hunter.website && (
          <a href={hunter.website} target="_blank" rel="noreferrer">
            <Icon name="globe" size={15} /> Website
          </a>
        )}
      </span>
    </div>
  );
}

function Findings({ findings }: { findings: Finding[] }) {
  return (
    <section>
      <h2>Findings</h2>
      {findings.length === 0 && <p className="muted">No verified breaks.</p>}
      {findings.map((finding) => (
        <article key={finding.id} className="finding">
          <h3>
            {finding.canary.public_label} <Badge state={finding.state} />
          </h3>
          <p className="muted">
            Tripped in {finding.tripped_runs} of {finding.runs.length} runs. Verified {when(finding.verified_at)}.{" "}
            {finding.payout?.txid && (
              <>
                Paid {formatZec(finding.payout.amount_zatoshi)} {sym()}, transaction <code>{finding.payout.txid}</code>.
              </>
            )}
          </p>
          {finding.hunter && <HunterCard hunter={finding.hunter} />}
          <h4>The attack</h4>
          {finding.attack.map((fill) => (
            <div key={fill.slot}>
              <p className="muted">{words(fill.slot)}</p>
              <pre>{fill.content}</pre>
            </div>
          ))}
          {finding.runs.map((run, index) => (
            <details key={run.run_index} open={index === 0}>
              <summary>
                Run {run.run_index + 1}: {run.evidence.length ? "tripped" : "clean"}
              </summary>
              {run.evidence.map((item, itemIndex) => (
                <div key={itemIndex} className="evidence">
                  <p>
                    <strong>{item.canary?.public_label ?? "Canary"}</strong> tripped at message{" "}
                    {item.message_index + 1} ({item.location}
                    {item.representation ? `, ${item.representation}` : ""}). Matched text:
                  </p>
                  <pre>{item.matched_text}</pre>
                </div>
              ))}
              <Transcript messages={run.transcript} evidence={run.evidence} />
            </details>
          ))}
        </article>
      ))}
    </section>
  );
}

function LedgerTable({ ledger }: { ledger: Ledger }) {
  return (
    <section>
      <h2>Ledger</h2>
      {ledger.entries.length === 0 ? (
        <p className="muted">No money has moved. Balance {formatZec(ledger.balance_zatoshi)} {sym()}.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Entry</th>
                <th className="num">{sym()}</th>
                <th>Transaction</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {ledger.entries.map((entry, index) => (
                <tr key={index}>
                  <td>{entry.kind}</td>
                  <td className={`num ${entry.amount_zatoshi > 0 ? "credit" : "debit"}`}>
                    {entry.amount_zatoshi > 0 ? "+" : ""}
                    {formatZec(entry.amount_zatoshi)}
                  </td>
                  <td>
                    {entry.txid ? <code>{entry.txid.slice(0, 20)}…</code> : <span className="muted">not sent yet</span>}
                  </td>
                  <td>{when(entry.created_at)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th>Balance</th>
                <th className="num">{formatZec(ledger.balance_zatoshi)}</th>
                <th />
                <th />
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </section>
  );
}

/** Whether something is in progress on the server that this page should keep watching. */
export function isMoving(bounty: SponsorBountyDetail): boolean {
  const checking = bounty.rearm?.state === "queued" || bounty.rearm?.state === "running";
  return bounty.state === "awaiting_deposit" || bounty.state === "live" || bounty.closing || checking;
}

/** How hard the bounty is being attacked. The attacks themselves are shown only for breaks. */
function ActivityTab({ bountyId, watching }: { bountyId: string; watching: boolean }) {
  const activity = useLoad(
    (signal) => api<AttemptActivity>("GET", `/v1/bounties/${bountyId}/attempts`, { signal }),
    bountyId,
    `sponsor:activity:${bountyId}`,
  );
  useInterval(() => void activity.reload(), 5000, watching);
  if (!activity.data) return activity.error ? <ErrorNote>{activity.error}</ErrorNote> : <Skeleton lines={4} />;
  const { total, queued, verified, recent } = activity.data;
  const held = total - queued - verified;
  return (
    <section>
      <h2>Activity</h2>
      <dl className="facts">
        <div>
          <dt>Attacks</dt>
          <dd>{total}</dd>
        </div>
        <div>
          <dt>Agent held</dt>
          <dd>{held}</dd>
        </div>
        <div>
          <dt>Verified breaks</dt>
          <dd>{verified}</dd>
        </div>
        <div>
          <dt>In the queue</dt>
          <dd>{queued}</dd>
        </div>
      </dl>
      {recent.length === 0 ? (
        <p className="empty">No one has attacked this agent yet. Attempts appear here as they arrive.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Attempt</th>
                <th>Outcome</th>
                <th>Submitted</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((line) => (
                <tr key={line.id}>
                  <td>
                    <code>{line.id.slice(0, 8)}</code>
                  </td>
                  <td>
                    <Badge
                      state={line.verified ? "verified" : line.state === "done" ? "not_verified" : line.state}
                    />
                  </td>
                  <td>
                    <Ago iso={line.submitted_at} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">
        You see that an attack happened and how it was judged. Its content is handed to you only when it is a
        verified break, under Findings.
      </p>
    </section>
  );
}

export const TABS = ["overview", "agent", "canaries", "funding", "activity", "findings", "ledger"] as const;
export type Tab = (typeof TABS)[number];

/** The tab a ?tab= value names. Anything unknown is the overview. */
export function tabOf(value: string | null): Tab {
  return TABS.find((tab) => tab === value) ?? "overview";
}

export function DashboardBounty() {
  const { bountyId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = tabOf(params.get("tab"));
  const base = `/v1/bounties/${bountyId}`;
  const page = useLoad<Everything>(async (signal) => {
    const [bounty, findings, ledger] = await Promise.all([
      api<SponsorBountyDetail>("GET", `/v1/sponsor/bounties/${bountyId}`, { signal }),
      api<Finding[]>("GET", `${base}/findings`, { signal }),
      api<Ledger>("GET", `${base}/ledger`, { signal }),
    ]);
    return { bounty, findings, ledger };
  }, bountyId, `sponsor:bounty:${bountyId}`);
  const [done, setDone] = useState("");

  // A deposit confirming, an attack landing or a re-arm check finishing all happen
  // elsewhere, so watch for them instead of asking the sponsor to reload.
  useInterval(() => void page.reload(), 3000, page.data !== null && isMoving(page.data.bounty));

  if (page.status === 404 || page.status === 422) return <NotFound message="That bounty does not exist." />;
  if (!page.data) return page.error ? <ErrorNote>{page.error}</ErrorNote> : <Skeleton block lines={6} />;
  const { bounty, findings, ledger } = page.data;

  const DONE: Record<string, string> = {
    config: "Config saved.",
    canaries: "Canary added.",
    fund: "Funding started. Send the deposit below.",
    activate: "The bounty is live.",
    pause: "The bounty is paused.",
    close: "Close requested. The unspent escrow is refunded once attempts in flight finish.",
    rearm: "The paid attack is being replayed against this config.",
  };
  async function act(path: string, body?: unknown, method = "POST") {
    setDone("");
    await api<unknown>(method, `${base}/${path}`, body === undefined ? {} : { body });
    await page.reload();
    setDone(DONE[path] ?? "");
  }

  return (
    <>
      <p className="crumb">
        <Link to="/dashboard">Your bounties</Link>
      </p>
      <h1>
        {bounty.title} <Badge state={bounty.closing ? "closing" : bounty.state} />
      </h1>
      {bounty.description && <p className="lede">{bounty.description}</p>}
      {done && (
        <p className="done" role="status">
          {done}
        </p>
      )}
      <ErrorNote>{page.error}</ErrorNote>

      <dl className="facts">
        <div>
          <dt>Reward</dt>
          <dd>{formatZec(bounty.reward_zatoshi)} {sym()}</dd>
        </div>
        <div>
          <dt>Escrow</dt>
          <dd>
            {formatZec(bounty.balance_zatoshi)} of {formatZec(bounty.required_zatoshi)} {sym()}
          </dd>
        </div>
        <div>
          <dt>Verified breaks</dt>
          <dd>{findings.length}</dd>
        </div>
        {(bounty.state === "live" || bounty.state === "paused") && (
          <div>
            <dt>Public page</dt>
            <dd>
              <Link to={`/b/${bounty.id}`}>/b/{bounty.id.slice(0, 8)}…</Link>
            </dd>
          </div>
        )}
      </dl>

      <div className="tabs" role="tablist" aria-label="Bounty">
        {TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "tab current" : "tab"}
            onClick={() => setParams(name === "overview" ? {} : { tab: name }, { replace: true })}
          >
            {name}
            {name === "canaries" && <span className="count">{bounty.canaries.length}</span>}
            {name === "findings" && findings.length > 0 && <span className="count hot">{findings.length}</span>}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {tab === "overview" && (
          <>
            {findings.length > 0 && bounty.state === "paused" && (
              <p className="error">
                A hunter broke this agent. Read the finding, patch the config, then replay the paid attack
                against your patch before reopening.{" "}
                <Link to="?tab=findings" replace>
                  See the finding →
                </Link>
              </p>
            )}
            <Actions bounty={bounty} act={act} broken={findings.length > 0} />
          </>
        )}
        {tab === "agent" && <Config bounty={bounty} act={act} />}
        {tab === "canaries" && <CanarySection bounty={bounty} act={act} />}
        {tab === "funding" && <Funding bounty={bounty} act={act} base={base} reload={page.reload} />}
        {tab === "activity" && <ActivityTab bountyId={bounty.id} watching={isMoving(bounty)} />}
        {tab === "findings" && <Findings findings={findings} />}
        {tab === "ledger" && <LedgerTable ledger={ledger} />}
      </div>
    </>
  );
}
