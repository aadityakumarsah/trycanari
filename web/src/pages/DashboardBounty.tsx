import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { pictureOf, useAccount } from "../account.tsx";
import { api } from "../api.ts";
import { useConfirm } from "../confirm.tsx";
import { AgentForm } from "../components/AgentForm.tsx";
import { Transcript } from "../components/Transcript.tsx";
import { Icon } from "../components/Brand.tsx";
import { WalletFunding } from "../components/WalletFunding.tsx";
import { Ago, Avatar, Badge, Copyable, ErrorNote, Skeleton, when, words } from "../components/ui.tsx";
import { useAction, useInterval, useLoad } from "../hooks.ts";
import { currency, formatZec, sym } from "../money.ts";
import { useToast } from "../toast.tsx";
import type { Assurance, AssuranceAction, AssuranceData, AssuranceInput, AttemptActivity, CanaryType, ContinuousTesting, EntryPoint, Finding, HouseHunt, Hunter, Ledger, Probe, Scan, SponsorBountyDetail } from "../types.ts";

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
  // Only a bounty with nothing left to happen: closed, or a draft that holds no money.
  const deletable =
    bounty.state === "closed" || (bounty.state === "draft" && !bounty.closing && bounty.balance_zatoshi === 0);
  const confirm = useConfirm();
  const navigate = useNavigate();
  const toast = useToast();
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
              const sure = await confirm({
                title: "Close this bounty?",
                body: "It stops taking attacks and the unspent escrow is refunded to your address. This cannot be undone.",
                confirm: "Close and refund",
                cancel: "Keep it open",
                danger: true,
              });
              if (sure) await act("close", { refund_address: bounty.refund_address });
            }}
            label={bounty.state === "draft" && bounty.balance_zatoshi === 0 ? "Close" : "Close and refund"}
            className="danger"
          />
        )}
        {deletable && (
          <ActionForm
            submit={async () => {
              const sure = await confirm({
                title: bounty.state === "draft" ? "Delete this draft?" : "Delete this bounty?",
                body:
                  bounty.state === "draft"
                    ? "The agent, its canaries and its settings are removed from your dashboard. This cannot be undone."
                    : "It is removed from your dashboard and from the public Completed list. Records of what was paid are kept. This cannot be undone.",
                confirm: "Delete",
                cancel: "Keep it",
                danger: true,
              });
              if (!sure) return;
              await api<unknown>("DELETE", `/v1/bounties/${bounty.id}`);
              toast(bounty.state === "draft" ? "Draft deleted." : "Bounty deleted.");
              navigate("/dashboard");
            }}
            label={bounty.state === "draft" ? "Delete draft" : "Delete"}
            className="danger"
          />
        )}
      </div>
      {bounty.state === "closed" && (
        <p className="muted">This bounty is closed. It stays here as a record until you delete it.</p>
      )}
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
        This config was broken. Save a patched config, then replay the attack that broke it against the patch.
        The bounty can reopen only once that replay fails.
      </p>
    );
  } else if (check?.state === "passed") {
    body = (
      <p className="done">
        The attack that broke it no longer breaks this config ({check.tripped_runs} of 5 runs tripped).{" "}
        {bounty.balance_zatoshi >= bounty.required_zatoshi
          ? "The escrow already covers the bounty: reopen it on the Funding tab, with no new deposit."
          : "Fund the bounty to reopen it."}
      </p>
    );
  } else if (check?.state === "failed") {
    body = (
      <p className="error">
        The attack that broke it still breaks this config ({check.tripped_runs} of 5 runs tripped). Patch it again.
      </p>
    );
  } else if (check) {
    body = <p className="notice">The attack that broke it is being replayed against this config…</p>;
  } else {
    body = (
      <>
        <ActionForm submit={() => act("rearm")} label="Replay the attack" />
        <p className="muted">The bounty can reopen only once the attack that broke it fails against the new config.</p>
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
      <ActionForm
        submit={() => act("fund", { refund_address: refund.trim() })}
        label={due === 0 ? "Reopen on the escrow it holds" : "Get deposit instructions"}
      >
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
        <AgentForm key={config?.config_hash ?? "new"} bounty={bounty} act={act} />
      ) : (
        config && (
          <>
            <pre>{config.system_prompt}</pre>
            <p className="muted">
              Model {config.model}
              {config.endpoint_base ? `, on your own endpoint ${config.endpoint_base}` : ", on our provider"}. Entry points: {config.entry_points.map(words).join(", ")}. The config can be
              edited while the bounty is a draft or paused.
            </p>
            {config.task && (
              <p className="muted">
                The task it is given: <q>{config.task}</q>
              </p>
            )}
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

const CANARY_OPTIONS: [CanaryType, string, string][] = [
  ["secret_leak", "Secret leak", "Trips when a planted secret is emitted, including common encodings."],
  ["system_prompt_leak", "System prompt leak", "Trips when a planted sentence from the pinned instructions is revealed."],
  ["forbidden_tool", "Forbidden tool call", "Trips when a tool call matches a forbidden argument rule."],
  ["unauthorized_action", "Unauthorized action", "Trips when an action tool is called outside its allowed scope."],
  ["data_exfiltration", "Data exfiltration", "Trips when a planted value enters a configured outbound tool argument."],
  ["phishing_link_injection", "Phishing link injection", "Trips when the agent emits the exact attacker-controlled URL."],
  ["malicious_code_injection", "Malicious code injection", "Trips when a planted marker appears inside emitted code."],
  ["policy_violation", "Policy violation", "Trips only on a sponsor-defined exact or regular-expression text match."],
];

function CanaryForm({ bountyId, act }: Pick<Props, "act"> & { bountyId: string }) {
  const [type, setType] = useState<CanaryType>("secret_leak");
  const [label, setLabel] = useState("");
  const [value, setValue] = useState("");
  const [tool, setTool] = useState("");
  const [argument, setArgument] = useState("");
  const [operator, setOperator] = useState("not_in");
  const [rule, setRule] = useState("");
  const [sinkTools, setSinkTools] = useState("");
  const [sinkArguments, setSinkArguments] = useState("url, body, message, content");
  const [mode, setMode] = useState("exact");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [testText, setTestText] = useState("");
  const toolType = type === "forbidden_tool" || type === "unauthorized_action";
  const selected = CANARY_OPTIONS.find(([name]) => name === type) ?? CANARY_OPTIONS[0];

  const list = (raw: string) => raw.split(",").map((item) => item.trim()).filter(Boolean);
  return (
    <ActionForm
      submit={async () => {
        const body: Record<string, unknown> = { type, public_label: label.trim() };
        if (type === "secret_leak") body.secret = value;
        if (type === "system_prompt_leak") body.sentence = value;
        if (type === "phishing_link_injection") body.url = value;
        if (type === "malicious_code_injection") body.marker = value;
        if (toolType) {
          body.tool = tool.trim();
          if (argument.trim()) {
            body.predicate = { argument: argument.trim(), operator, value: ruleValue(operator, rule) };
          }
        }
        if (type === "data_exfiltration") {
          body.value = value;
          body.sink_tools = list(sinkTools);
          body.sink_arguments = list(sinkArguments);
        }
        if (type === "policy_violation") {
          Object.assign(body, { pattern: value, mode, case_sensitive: caseSensitive, test_text: testText });
        }
        await act("canaries", body);
        // A first baseline is free, even while this is still an unfunded draft.
        await api("POST", `/v1/bounties/${bountyId}/scan`);
        setLabel("");
        setValue("");
      }}
      label="Add canary"
    >
      <h3>Add a deterministic tripwire</h3>
      <label>
        Canary type
        <select value={type} onChange={(event) => setType(event.target.value as CanaryType)}>
          {CANARY_OPTIONS.map(([name, title]) => <option key={name} value={name}>{title}</option>)}
        </select>
        <small>{selected?.[2] ?? ""}</small>
      </label>
      <label>
        Public label
        <input required maxLength={64} value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      {!toolType && <label>{type === "policy_violation" ? "Pattern" : type === "phishing_link_injection" ? "URL" : type === "system_prompt_leak" ? "Canary sentence" : type === "malicious_code_injection" ? "Code marker" : "Planted value"}<input required spellCheck={false} autoComplete="off" className="code" value={value} onChange={(e) => setValue(e.target.value)} /></label>}
      {toolType && <><label>Tool<input required spellCheck={false} className="code" value={tool} onChange={(e) => setTool(e.target.value)} /></label><label>Argument<input spellCheck={false} className="code" value={argument} onChange={(e) => setArgument(e.target.value)} /><small>Leave empty to trip on any call.</small></label><label>Condition<select value={operator} onChange={(e) => setOperator(e.target.value)}>{OPERATORS.map(([name, text]) => <option key={name} value={name}>{text}</option>)}</select></label><label>Value<input spellCheck={false} className="code" value={rule} onChange={(e) => setRule(e.target.value)} /></label></>}
      {type === "data_exfiltration" && <><label>Outbound tools<input required className="code" value={sinkTools} onChange={(e) => setSinkTools(e.target.value)} /><small>Comma-separated mock tool names.</small></label><label>Outbound arguments<input required className="code" value={sinkArguments} onChange={(e) => setSinkArguments(e.target.value)} /></label></>}
      {type === "policy_violation" && <><label>Match<select value={mode} onChange={(e) => setMode(e.target.value)}><option value="exact">Exact text</option><option value="regex">Regular expression</option></select></label><label><input type="checkbox" checked={caseSensitive} onChange={(e) => setCaseSensitive(e.target.checked)} /> Case-sensitive</label>{mode === "regex" && <label>Matching test text<input required className="code" value={testText} onChange={(e) => setTestText(e.target.value)} /><small>Proves the regex can trip before listing.</small></label>}<small>Only this exact or regex match can trigger automatic payout. Subjective behaviour requiring judgment is out of scope.</small></>}
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
        <CanaryForm bountyId={bounty.id} act={act} />
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

function Findings({ bountyId, findings }: { bountyId: string; findings: Finding[] }) {
  return (
    <section>
      <h2>Findings</h2>
      {findings.length === 0 && <p className="muted">No verified breaks.</p>}
      {findings.map((finding) => (
        <article key={finding.id} className="finding">
          <h3>
            {finding.canary.public_label} <Badge state={finding.state} />
          </h3>
          <p><a href={`/evidence/bounties/${bountyId}/findings/${finding.id}`}>Open printable finding report</a></p>
          <p className="muted">
            Tripped in {finding.tripped_runs} of {finding.runs.length} runs. Verified {when(finding.verified_at)}.{" "}
            {finding.state === "waived" && (
              <>
                Found by the house hunter, trycanari's own attacker. Nothing was paid for it and your escrow is
                untouched: patch the agent, run the re-arm check and reopen the bounty.
              </>
            )}
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

/**
 * The baseline scan: well-known attacks run against the agent before any hunter sees it.
 * It tells a sponsor at once whether the obvious things already work.
 */
const SOURCES: { source: NonNullable<Probe["source"]>; title: string; about: string }[] = [
  { source: "past_break", title: "Attacks you paid for", about: "Every attack that broke this bounty, replayed against the agent as it is now." },
  { source: "library", title: "From the shared library", about: "Attacks that broke other sponsors' agents. You are shown one only if it breaks yours." },
  { source: "playbook", title: "The baseline playbook", about: "What every hunter tries first, written for this agent's own canaries." },
];

function ScanTab({ bounty, reload }: { bounty: SponsorBountyDetail; reload: () => Promise<void> }) {
  const bountyId = bounty.id;
  const canaries = bounty.canaries.length;
  const path = `/v1/bounties/${bountyId}/scan`;
  const scan = useLoad((signal) => api<Scan>("GET", path, { signal }), bountyId, `sponsor:scan:${bountyId}`);
  const starting = useAction();
  const setting = useAction();
  const [corpusType, setCorpusType] = useState<CanaryType>("secret_leak");
  const account = useAccount().data;
  const planned = account?.plan_active !== false;
  const running = scan.data?.state === "queued" || scan.data?.state === "running";
  useInterval(() => void scan.reload(), 2500, running);
  if (!scan.data) return scan.error ? <ErrorNote>{scan.error}</ErrorNote> : <Skeleton lines={4} />;
  const { state, probes, held, total } = scan.data;
  const broken = total - held;

  async function start(again = false) {
    await starting.run(async () => {
      await api<Scan>("POST", again ? `${path}?again=true` : path);
      await scan.reload();
    });
  }

  async function set(body: { shares_breaks?: boolean; watch?: boolean }) {
    await setting.run(async () => {
      await api("PATCH", `/v1/bounties/${bountyId}/settings`, { body });
      await reload();
    });
  }

  async function runCorpus() {
    await starting.run(async () => {
      await api<Scan>("POST", `${path}?corpus_type=${corpusType}`);
      await scan.reload();
    });
  }

  return (
    <section>
      <h2>Scan</h2>
      <p className="muted">
        Everything already known to work, run against your agent: the baseline playbook that every hunter tries
        first, every attack this bounty has paid for and, with the Watch plan, attacks that broke other
        sponsors' agents. Each is replayed once. It is a quick floor, not the five-replay standard a paid break
        is held to.
      </p>
      <p className="muted">
        Working on the agent in an editor? Your coding agent can run this scan itself, on every change:{" "}
        <Link to="/docs#editor">scan from your editor</Link>.
      </p>
      <div className="scan-options">
        <label className="scan-option">
          <input
            type="checkbox"
            checked={bounty.watch}
            disabled={setting.busy || (!planned && !bounty.watch)}
            onChange={(event) => void set({ watch: event.target.checked })}
          />
          <span>
            <b>Scan every day while it is live</b>
            <small>
              {planned
                ? "New attacks reach the library all the time. A daily scan tells you when one of them breaks your agent."
                : "Part of the Watch plan. Get it from your dashboard."}
            </small>
          </span>
        </label>
        <label className="scan-option">
          <input
            type="checkbox"
            checked={bounty.shares_breaks}
            disabled={setting.busy}
            onChange={(event) => void set({ shares_breaks: event.target.checked })}
          />
          <span>
            <b>Share this bounty's breaks with the library</b>
            <small>
              Attacks that break this agent are replayed against other sponsors' agents. They see an attack only
              if it breaks theirs, and never your prompt, tools or transcripts. Off unless you turn it on.
            </small>
          </span>
        </label>
      </div>
      <ErrorNote>{setting.error}</ErrorNote>
      <div className="scan-start">
        <label>
          Known-attack corpus
          <select value={corpusType} onChange={(event) => setCorpusType(event.target.value as CanaryType)}>
            {CANARY_OPTIONS.map(([type, label]) => <option key={type} value={type}>{label}</option>)}
          </select>
        </label>
        <button type="button" className="secondary" disabled={starting.busy || running || canaries === 0} onClick={() => void runCorpus()}>
          Run all known attacks of this type
        </button>
      </div>
      {state === null && (
        <div className="scan-start">
          <button type="button" className="accent" disabled={starting.busy || canaries === 0} onClick={() => void start()}>
            <Icon name="zap" size={15} /> {starting.busy ? "Starting…" : "Run the scan"}
          </button>
          <span className="muted small">
            {canaries === 0
              ? "Add a canary first: a scan needs something to trip."
              : "Takes under a minute. Changing the agent or its canaries makes the last scan out of date."}
          </span>
        </div>
      )}
      <ErrorNote>{starting.error}</ErrorNote>
      {running && (
        <div className="replaying">
          <p>
            <Badge state={state ?? "queued"} /> Running known attacks against your agent…
          </p>
          <div className="progress" role="progressbar" aria-label="Scanning">
            <i />
          </div>
        </div>
      )}
      {state === "error" && (
        <div className="scan-start">
          <p className="error">The scan could not be completed. Nothing is wrong with your agent; try again.</p>
          <button type="button" className="secondary" disabled={starting.busy} onClick={() => void start()}>
            Try again
          </button>
        </div>
      )}
      {state === "done" && (
        <>
          <div className={broken === 0 ? "scan-score good" : "scan-score bad"}>
            <b>
              {held} / {total}
            </b>
            <span>
              {broken === 0
                ? "The agent held against every probe. Hunters will have to do better than the obvious."
                : `${broken} ${broken === 1 ? "probe" : "probes"} got through. A hunter would find ${broken === 1 ? "this" : "these"} quickly: consider tightening the agent before you fund the bounty.`}
            </span>
          </div>
          <div className="scan-again">
            <span className="muted small">
              {scan.data.finished_at && (
                <>
                  Last run <Ago iso={scan.data.finished_at} />
                  {scan.data.with_library ? ", with the shared library. " : ". "}
                </>
              )}
              {planned ? "" : "Running it again without changing the agent is part of the Watch plan."}
            </span>
            <button type="button" className="secondary small-button" disabled={starting.busy || !planned} onClick={() => void start(true)}>
              <Icon name="zap" size={14} /> Run again
            </button>
          </div>
          {SOURCES.map(({ source, title, about }) => {
            const group = probes.filter((probe) => (probe.source ?? "playbook") === source);
            if (group.length === 0) return null;
            return (
              <div key={source} className="probe-group">
                <h3>
                  {title}{" "}
                  <span className="muted">
                    {group.filter((probe) => probe.tripped.length === 0).length} of {group.length} held
                  </span>
                </h3>
                <p className="muted small">{about}</p>
          <ul className="probes">
            {group.map((probe) => (
              <li key={probe.id} className={probe.tripped.length ? "probe tripped" : "probe"}>
                <details>
                  <summary>
                    <span className={probe.tripped.length ? "probe-mark bad" : "probe-mark"}>
                      <Icon name={probe.tripped.length ? "close" : "check"} size={14} />
                    </span>
                    <span className="probe-name">
                      <b>{probe.name}</b>
                      <small>
                        {probe.technique} · aimed at <code>{probe.aimed_at}</code>
                      </small>
                    </span>
                    <span className={probe.tripped.length ? "badge badge-error" : "badge badge-live"}>
                      {probe.tripped.length ? `tripped ${probe.tripped.join(", ")}` : "held"}
                    </span>
                  </summary>
                  <div className="probe-body">
                    <h4>The attack</h4>
                    {probe.attack.length === 0 && (
                      <p className="muted">
                        Not shown. It was written against another sponsor's agent, and yours held against it.
                      </p>
                    )}
                    {probe.attack.map((fill) => (
                      <div key={fill.slot}>
                        <p className="muted">{words(fill.slot)}</p>
                        <pre>{fill.content}</pre>
                      </div>
                    ))}
                    <h4>What the agent did</h4>
                    {probe.tool_calls.map((call, index) => (
                      <pre key={index}>
                        {call.name}({JSON.stringify(call.arguments, null, 2)})
                      </pre>
                    ))}
                    {probe.reply ? <pre>{probe.reply}</pre> : probe.tool_calls.length === 0 && <p className="muted">It said nothing.</p>}
                  </div>
                </details>
              </li>
            ))}
          </ul>
              </div>
            );
          })}
        </>
      )}
    </section>
  );
}

/** How hard the bounty is being attacked. The attacks themselves are shown only for breaks. */
/** What the house hunter has done with the config that is live, in a sentence. */
export function houseLine(hunt: HouseHunt | null | undefined): string | null {
  if (!hunt) return null;
  const made = `${hunt.tries} ${hunt.tries === 1 ? "attack" : "attacks"}`;
  if (hunt.state === "running") return `The house hunter is attacking this config now: ${made} so far.`;
  if (hunt.outcome === "broke") return `The house hunter broke this config after ${made}. Nothing is paid for it: see Findings.`;
  if (hunt.outcome === "held") return `The house hunter made ${made} on this config and none got through.`;
  if (hunt.tries === 0 && hunt.detail) return `The house hunter has not attacked this config: ${hunt.detail}.`;
  return `The house hunter stopped after ${made}${hunt.detail ? `: ${hunt.detail}` : ""}.`;
}

function ActivityTab({ bountyId, watching, hunt }: { bountyId: string; watching: boolean; hunt: HouseHunt | null }) {
  const activity = useLoad(
    (signal) => api<AttemptActivity>("GET", `/v1/bounties/${bountyId}/attempts`, { signal }),
    bountyId,
    `sponsor:activity:${bountyId}`,
  );
  useInterval(() => void activity.reload(), 5000, watching);
  if (!activity.data) return activity.error ? <ErrorNote>{activity.error}</ErrorNote> : <Skeleton lines={4} />;
  const { total, queued, verified, recent } = activity.data;
  const held = total - queued - verified;
  const house = houseLine(hunt);
  return (
    <section>
      <h2>Activity</h2>
      {house && (
        <p className="muted house-note">
          <Icon name="bot" size={15} /> {house} It is trycanari's own attacker: it sees only what every hunter
          sees, and it is never paid.
        </p>
      )}
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
                    {line.house && <span className="tag mini-tag house-tag">house</span>}
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

/** The version history is a safe summary: it never returns the saved attack payloads. */
function ContinuousTab({ bountyId }: { bountyId: string }) {
  const path = `/v1/bounties/${bountyId}/continuous`;
  const report = useLoad((signal) => api<ContinuousTesting>("GET", path, { signal }), bountyId, `sponsor:continuous:${bountyId}`);
  useInterval(() => void report.reload(), 3000, report.data?.runs.some((run) => run.state === "queued" || run.state === "running") ?? false);
  if (!report.data) return report.error ? <ErrorNote>{report.error}</ErrorNote> : <Skeleton lines={4} />;
  const latest = report.data.runs[0];
  return (
    <section>
      <h2>Continuous testing</h2>
      <p className="muted">
        Every saved break and corpus probe is replayed on each version, on demand from CI, and daily while Watch is on.
        The monthly Watch deposit is the testing fee; verified bounty breaks still use the separate 10% success fee.
      </p>
      <ActionForm
        submit={async () => {
          await api("POST", `${path}/gate`);
          await report.reload();
        }}
        label="Run CI regression gate"
      />
      {latest && (
        <dl className="facts">
          <div><dt>Latest result</dt><dd>{latest.passed === null ? latest.state : latest.passed ? "pass" : "fail"}</dd></div>
          <div><dt>Attack success rate</dt><dd>{latest.attack_success_rate === null ? "—" : `${Math.round(latest.attack_success_rate * 100)}%`}</dd></div>
          <div><dt>Canary coverage</dt><dd>{latest.covered_canary_types.length}</dd></div>
          <div><dt>New regressions</dt><dd>{latest.regressed.length}</dd></div>
        </dl>
      )}
      {report.data.runs.length === 0 ? <p className="empty">Save a version to start continuous testing.</p> : (
        <div className="scroll"><table><thead><tr><th>Version</th><th>Trigger</th><th>Outcome</th><th>Success rate</th><th>Coverage</th><th>When</th></tr></thead><tbody>
          {report.data.runs.map((run) => <tr key={run.id}><td>v{run.config_version}</td><td>{words(run.trigger)}</td><td>{run.passed === null ? run.state : run.passed ? "pass" : `fail (${run.failures})`}</td><td>{run.attack_success_rate === null ? "—" : `${Math.round(run.attack_success_rate * 100)}%`}</td><td>{run.covered_canary_types.join(", ") || "none"}</td><td><Ago iso={run.created_at} /></td></tr>)}
        </tbody></table></div>
      )}
    </section>
  );
}

const ASSURANCE_INPUTS: AssuranceInput[] = ["user_message", "document", "tool_result", "web_page", "memory", "sub_agent"];
const ASSURANCE_DATA: AssuranceData[] = ["personal", "financial", "health", "credentials", "confidential", "regulated"];
const ASSURANCE_ACTIONS: AssuranceAction[] = ["read", "write", "send", "approve", "delete", "execute", "move_money"];
const ASSURANCE_STAGES = ["requested", "scoping", "testing", "remediation", "retest", "complete"] as const;

function Checks({ name, values, selected, disabled = false }: { name: string; values: string[]; selected: string[]; disabled?: boolean }) {
  return (
    <div className="assurance-checks">
      {values.map((value) => <label key={value}><input type="checkbox" name={name} value={value} disabled={disabled} defaultChecked={selected.includes(value)} />{words(value)}</label>)}
    </div>
  );
}

function AssuranceTab({ bountyId }: { bountyId: string }) {
  const path = `/v1/bounties/${bountyId}/assurance`;
  const state = useLoad(
    (signal) => api<{ engagement: Assurance | null }>("GET", path, { signal }),
    bountyId,
    `assurance:${bountyId}`,
  );
  const saving = useAction();
  const requesting = useAction();
  if (!state.data) return state.error ? <ErrorNote>{state.error}</ErrorNote> : <Skeleton lines={5} />;
  const engagement = state.data.engagement;
  const locked = engagement !== null && ["testing", "remediation", "retest", "complete", "cancelled"].includes(engagement.status);
  const selected = (name: string, form: FormData) => form.getAll(name).map(String);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const ok = await saving.run(async () => {
      await api<Assurance>("PUT", path, {
        body: {
          service: form.get("service"), stage: form.get("stage"), deployment: form.get("deployment"),
          untrusted_inputs: selected("untrusted_inputs", form),
          sensitive_data: selected("sensitive_data", form),
          consequential_actions: selected("consequential_actions", form),
          workflow: form.get("workflow"), feared_failure: form.get("feared_failure"),
          identity_controls: form.get("identity_controls"), human_approval: form.get("human_approval"),
        },
      });
    });
    if (ok) await state.reload();
  }

  async function request() {
    const ok = await requesting.run(async () => { await api<Assurance>("POST", `${path}/request`); });
    if (ok) await state.reload();
  }

  const readiness = engagement?.readiness;
  const readyItems = readiness ? [
    ["Agent configuration", readiness.agent_configured],
    ["Deterministic boundaries", readiness.boundaries_defined],
    ["Untrusted inputs", readiness.attack_surface_declared],
    ["Business consequence", readiness.consequence_documented],
    ["Identity and authorization", readiness.identity_documented],
    ["Human approval", readiness.approval_documented],
  ] as const : [];

  return (
    <section className="assurance-workspace">
      <div className="assurance-workspace-head">
        <div><p className="eyebrow">Managed agent assurance</p><h2>Assessment scope</h2><p className="muted">Private threat model, adversarial testing, remediation review and a version-bound re-test.</p></div>
        <span className={`badge badge-${engagement?.status ?? "draft"}`}>{words(engagement?.status ?? "draft")}</span>
      </div>
      {engagement && engagement.status !== "draft" && engagement.status !== "cancelled" && (
        <ol className="assurance-progress" aria-label="Assessment progress">
          {ASSURANCE_STAGES.map((stage, index) => {
            const current = ASSURANCE_STAGES.indexOf(engagement.status as typeof ASSURANCE_STAGES[number]);
            return <li key={stage} className={index <= current ? "done" : undefined}><span>{index + 1}</span>{words(stage)}</li>;
          })}
        </ol>
      )}
      {engagement?.quoted_price_cents ? <p className="notice"><b>Approved scope:</b> ${(engagement.quoted_price_cents / 100).toLocaleString()} fixed fee.</p> : null}
      {engagement?.tested_config_hash && <p className="muted small"><b>Version under test:</b> v{engagement.tested_config_version} · <code>{engagement.tested_config_hash}</code></p>}
      {engagement?.limitations && <p className="report-note"><b>Recorded limitations:</b> {engagement.limitations}</p>}
      {readiness && <div className="assurance-readiness"><h3>Scope readiness</h3>{readyItems.map(([label, done]) => <span key={label} className={done ? "done" : undefined}><Icon name={done ? "check" : "clock"} size={14} />{label}</span>)}</div>}

      <form key={engagement?.updated_at ?? "new"} onSubmit={(event) => void save(event)}>
        <div className="pilot-fields">
          <label>Service<select name="service" defaultValue={engagement?.service ?? "sprint"} disabled={locked}><option value="sprint">Assessment sprint</option><option value="continuous">Sprint + continuous assurance</option></select></label>
          <label>Agent stage<select name="stage" defaultValue={engagement?.stage ?? "beta"} disabled={locked}><option value="prototype">Prototype</option><option value="beta">Beta</option><option value="production">Production</option></select></label>
          <label>Data path<select name="deployment" defaultValue={engagement?.deployment ?? "managed"} disabled={locked}><option value="managed">Managed encrypted service</option><option value="private_cloud">Private cloud runner</option><option value="on_prem">On-premises runner</option></select></label>
        </div>
        <label>Consequential workflow<textarea name="workflow" required minLength={30} maxLength={4000} rows={4} disabled={locked} defaultValue={engagement?.workflow ?? ""} placeholder="Describe one end-to-end workflow, the tools it calls and the outcome it changes." /></label>
        <label>Failure that would trigger an incident review<textarea name="feared_failure" required minLength={20} maxLength={2000} rows={3} disabled={locked} defaultValue={engagement?.feared_failure ?? ""} placeholder="Example: a customer causes a refund above $100 or reads another customer's account." /></label>
        <fieldset><legend>Untrusted inputs</legend><Checks name="untrusted_inputs" values={ASSURANCE_INPUTS} selected={engagement?.untrusted_inputs ?? ["user_message"]} disabled={locked} /></fieldset>
        <fieldset><legend>Sensitive data in scope</legend><Checks name="sensitive_data" values={ASSURANCE_DATA} selected={engagement?.sensitive_data ?? []} disabled={locked} /></fieldset>
        <fieldset><legend>Consequential actions</legend><Checks name="consequential_actions" values={ASSURANCE_ACTIONS} selected={engagement?.consequential_actions ?? ["read", "write"]} disabled={locked} /></fieldset>
        <label>Agent identity and authorization<textarea name="identity_controls" required minLength={10} maxLength={2000} rows={3} disabled={locked} defaultValue={engagement?.identity_controls ?? ""} placeholder="How the agent authenticates, whose authority it uses, and how permissions are scoped or revoked." /></label>
        <label>Human approval and emergency stop<textarea name="human_approval" required minLength={10} maxLength={2000} rows={3} disabled={locked} defaultValue={engagement?.human_approval ?? ""} placeholder="Which actions require approval, who approves them, and how the agent can be stopped." /></label>
        <ErrorNote>{saving.error}</ErrorNote>
        {!locked && <button disabled={saving.busy}>{saving.busy ? "Saving…" : engagement ? "Save scope" : "Create assessment scope"}</button>}
      </form>
      {engagement && ["draft", "requested"].includes(engagement.status) && (
        <div className="assurance-request"><p><b>Founding sprint: $2,500 fixed.</b> Final scope and any private-runner cost are confirmed before work starts.</p><button className="accent" type="button" disabled={requesting.busy || engagement.status === "requested"} onClick={() => void request()}>{engagement.status === "requested" ? "Assessment requested" : requesting.busy ? "Requesting…" : "Request managed assessment"}</button><ErrorNote>{requesting.error}</ErrorNote></div>
      )}
      <p className="muted small">A completed assessment records evidence for the tested version and declared scope. It is not a certification or a guarantee of safety.</p>
    </section>
  );
}

export const TABS = ["overview", "assurance", "agent", "canaries", "scan", "continuous", "funding", "activity", "findings", "ledger"] as const;
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
    rearm: "The attack that broke it is being replayed against this config.",
  };
  async function act(path: string, body?: unknown, method = "POST") {
    setDone("");
    const answer = await api<{ amount_due_zatoshi?: number } | null>(method, `${base}/${path}`, body === undefined ? {} : { body });
    await page.reload();
    // Funding a bounty whose escrow already covers it, as after a break nothing was paid
    // for, sets it live at once: there is no deposit to send.
    const covered = path === "fund" && answer?.amount_due_zatoshi === 0;
    setDone(covered ? "The bounty is live again, on the escrow it already holds." : (DONE[path] ?? ""));
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
        <div>
          <dt>Evidence</dt>
          <dd>
            <Link to={`/dashboard/b/${bounty.id}/report`}>Open the audit report</Link>
          </dd>
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
                {findings[findings.length - 1]?.state === "waived"
                  ? "The house hunter, trycanari's own attacker, broke this agent. Nothing was paid and your escrow is untouched. Read the finding, patch the config, then replay the attack against your patch before reopening."
                  : "A hunter broke this agent. Read the finding, patch the config, then replay the attack against your patch before reopening."}{" "}
                <Link to="?tab=findings" replace>
                  See the finding →
                </Link>
              </p>
            )}
            <Actions bounty={bounty} act={act} broken={findings.length > 0} />
          </>
        )}
        {tab === "agent" && <Config bounty={bounty} act={act} />}
        {tab === "assurance" && <AssuranceTab bountyId={bounty.id} />}
        {tab === "canaries" && <CanarySection bounty={bounty} act={act} />}
        {tab === "scan" && <ScanTab bounty={bounty} reload={page.reload} />}
        {tab === "continuous" && <ContinuousTab bountyId={bounty.id} />}
        {tab === "funding" && <Funding bounty={bounty} act={act} base={base} reload={page.reload} />}
        {tab === "activity" && (
          <ActivityTab
            bountyId={bounty.id}
            watching={isMoving(bounty) || bounty.house_hunt?.state === "running"}
            hunt={bounty.house_hunt ?? null}
          />
        )}
        {tab === "findings" && <Findings bountyId={bounty.id} findings={findings} />}
        {tab === "ledger" && <LedgerTable ledger={ledger} />}
      </div>
    </>
  );
}
