import { Link, useParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { ErrorNote, Skeleton, when, words } from "../components/ui.tsx";
import { useLoad } from "../hooks.ts";
import { formatZec, sym } from "../money.ts";
import type {
  AttemptActivity,
  Compliance,
  ComplianceControl,
  ComplianceRisk,
  Finding,
  Ledger,
  Scan,
  SponsorBountyDetail,
} from "../types.ts";

interface Everything {
  bounty: SponsorBountyDetail;
  findings: Finding[];
  activity: AttemptActivity;
  ledger: Ledger;
  scan: Scan;
  compliance: Compliance;
}

/** How each standing is said on the page. Never "compliant": the report shows evidence. */
export const RISK_STATUS: Record<ComplianceRisk["status"], string> = {
  not_tested: "Not tested",
  held: "Held",
  broken: "Open weakness",
  patched: "Patched, not re-checked",
  fixed: "Fixed and re-checked",
};
export const CONTROL_STATUS: Record<ComplianceControl["status"], string> = {
  evidence: "Evidence on file",
  partial: "Partial evidence",
  gap: "Open weakness",
  none: "No evidence yet",
  not_applicable: "Nothing to show yet",
};

function Standing({ status, label }: { status: string; label: string }) {
  return <span className={`standing standing-${status}`}>{label}</span>;
}

/** What the tests of one risk came to, in a few words. */
export function testedLine(risk: ComplianceRisk): string {
  const parts = [];
  if (risk.probes_run) parts.push(`${risk.probes_held} of ${risk.probes_run} known attacks held`);
  if (risk.breaks) parts.push(`${risk.breaks} verified ${risk.breaks === 1 ? "break" : "breaks"}`);
  if (parts.length) return parts.join("; ");
  return risk.status === "not_tested" ? "Nothing has tested it" : "No hunter's attack got through";
}

/** The mapping as a file, for a compliance tool or an auditor's own records. */
function download(compliance: Compliance) {
  const file = new Blob([JSON.stringify(compliance, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(file);
  link.download = `trycanari-evidence-${compliance.subject.bounty_id}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

/**
 * Everything adversarial testing has shown about one agent, on one page that prints
 * cleanly, set against the lists an auditor works from: OWASP's risks, the EU AI Act
 * and ISO/IEC 42001. It is what a sponsor hands to whoever asks "how was this tested?".
 */
export function Report() {
  const { bountyId = "" } = useParams();
  const base = `/v1/bounties/${bountyId}`;
  const page = useLoad<Everything>(async (signal) => {
    const [bounty, findings, activity, ledger, scan, compliance] = await Promise.all([
      api<SponsorBountyDetail>("GET", `/v1/sponsor/bounties/${bountyId}`, { signal }),
      api<Finding[]>("GET", `${base}/findings`, { signal }),
      api<AttemptActivity>("GET", `${base}/attempts`, { signal }),
      api<Ledger>("GET", `${base}/ledger`, { signal }),
      api<Scan>("GET", `${base}/scan`, { signal }),
      api<Compliance>("GET", `${base}/compliance`, { signal }),
    ]);
    return { bounty, findings, activity, ledger, scan, compliance };
  }, bountyId);

  if (page.status === 404 || page.status === 422) return <NotFound message="That bounty does not exist." />;
  if (!page.data) return page.error ? <ErrorNote>{page.error}</ErrorNote> : <Skeleton block lines={6} />;
  const { bounty, findings, activity, ledger, scan, compliance } = page.data;
  const mapped = new Map(compliance.findings.map((finding) => [finding.id, finding]));
  // The entries of OWASP's lists that a tripwire guards against.
  const guards = (label: string) =>
    compliance.risks.filter((entry) => entry.role === "impact" && entry.tripwires.includes(label)).map((entry) => entry.id);
  const config = bounty.config;
  const paid = ledger.entries.filter((entry) => entry.kind === "payout").reduce((sum, entry) => sum - entry.amount_zatoshi, 0);
  const risk = (type: string) => scan.risks[type] ?? words(type);
  const held = activity.total - activity.queued - activity.verified;

  return (
    <article className="report">
      <div className="report-bar">
        <Link to={`/dashboard/b/${bounty.id}`}>← Back to the bounty</Link>
        <span className="report-actions">
          <button type="button" onClick={() => download(compliance)}>
            <Icon name="box" size={15} /> Download JSON
          </button>
          <button type="button" onClick={() => window.print()}>
            <Icon name="doc" size={15} /> Print or save as PDF
          </button>
        </span>
      </div>

      <header className="report-head">
        <p className="eyebrow">Adversarial testing report</p>
        <h1>{bounty.title}</h1>
        <p className="muted">
          Generated {when(compliance.generated_at)} by trycanari. Attacks were replayed against a sandboxed copy
          of the agent with mock tools; nothing real was moved or sent.
        </p>
        <p className="report-note">{compliance.disclaimer}</p>
      </header>

      <section>
        <h2>Summary</h2>
        <dl className="facts">
          <div>
            <dt>Attacks received</dt>
            <dd>{activity.total}</dd>
          </div>
          <div>
            <dt>Held</dt>
            <dd>{held}</dd>
          </div>
          <div>
            <dt>Verified breaks</dt>
            <dd>{activity.verified}</dd>
          </div>
          <div>
            <dt>Paid to hunters</dt>
            <dd>
              {formatZec(paid)} {sym()}
            </dd>
          </div>
        </dl>
        <p>
          {activity.verified === 0
            ? activity.total === 0
              ? "No hunter has attacked this agent yet, so this report shows the baseline scan only."
              : `${activity.total} ${activity.total === 1 ? "attack was" : "attacks were"} submitted by independent hunters and none was a verified break.`
            : `${activity.verified} of ${activity.total} ${activity.total === 1 ? "attack" : "attacks"} ${activity.verified === 1 ? "was a verified break" : "were verified breaks"}: the same tripwire tripped in at least 3 of 5 independent replays. Each is detailed below with its evidence.`}
        </p>
      </section>

      <section>
        <h2>What was tested</h2>
        <table>
          <tbody>
            <tr>
              <th>Model</th>
              <td>{config?.model ?? "Not set"}</td>
            </tr>
            <tr>
              <th>Configuration</th>
              <td>
                Version {config?.version ?? "none"}, fingerprint <code>{config?.config_hash.slice(0, 16) ?? "none"}…</code>
              </td>
            </tr>
            <tr>
              <th>Attack surface</th>
              <td>{config?.entry_points.map(words).join(", ") ?? "None"}</td>
            </tr>
            <tr>
              <th>Tools (mocked)</th>
              <td>{config?.tools.map((tool) => tool.name).join(", ") || "None"}</td>
            </tr>
            <tr>
              <th>Standard of proof</th>
              <td>The same tripwire in at least 3 of 5 replays, each from an empty history</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section>
        <h2>Tripwires and the risks they cover</h2>
        <table>
          <thead>
            <tr>
              <th>Tripwire</th>
              <th>Kind</th>
              <th>Risk (after OWASP's lists)</th>
              <th>Broken</th>
            </tr>
          </thead>
          <tbody>
            {bounty.canaries.map((canary) => {
              const hits = findings.filter((finding) => finding.canary.public_label === canary.public_label).length;
              return (
                <tr key={canary.id}>
                  <td>
                    <code>{canary.public_label}</code>
                  </td>
                  <td>{words(canary.type)}</td>
                  <td>
                    {risk(canary.type)}
                    <span className="risk-ids">{guards(canary.public_label).join(", ")}</span>
                  </td>
                  <td>{hits ? `${hits}×` : "No"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Baseline scan</h2>
        {scan.state === "done" ? (
          <>
            <p>
              The agent held against <b>{scan.held}</b> of <b>{scan.total}</b> well-known attacks, each replayed
              once against this configuration.
            </p>
            <table>
              <thead>
                <tr>
                  <th>Probe</th>
                  <th>Technique</th>
                  <th>Aimed at</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {scan.probes.map((probe) => (
                  <tr key={probe.id}>
                    <td>{probe.name}</td>
                    <td>{probe.technique}</td>
                    <td>
                      <code>{probe.aimed_at}</code>
                    </td>
                    <td>{probe.tripped.length ? `Tripped ${probe.tripped.join(", ")}` : "Held"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        ) : (
          <p className="muted">The current configuration has not been scanned.</p>
        )}
      </section>

      <section id="risks">
        <h2>Risk coverage</h2>
        <p>
          Each risk is an entry of OWASP's lists for LLM and for agentic applications. Two are about how an
          attack gets in, and every attack tests those. The rest are about what it does once it is in, and a
          tripwire tests each of them.
        </p>
        <table className="report-map">
          <thead>
            <tr>
              <th>Risk</th>
              <th>How it is tested here</th>
              <th>What the tests came to</th>
              <th>Standing</th>
            </tr>
          </thead>
          <tbody>
            {compliance.risks.map((entry) => (
              <tr key={entry.id}>
                <td>
                  <b>{entry.id}</b> {entry.name}
                  <span className="risk-ids">{entry.source}</span>
                </td>
                <td>
                  {entry.tested_by}
                  {entry.role === "impact" && entry.tripwires.length > 0 && (
                    <span className="risk-ids">{entry.tripwires.join(", ")}</span>
                  )}
                </td>
                <td>{testedLine(entry)}</td>
                <td>
                  <Standing status={entry.status} label={RISK_STATUS[entry.status]} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {compliance.frameworks.map((framework) => (
        <section key={framework.id} id={framework.id} className="report-framework">
          <h2>{framework.name}</h2>
          <p className="muted">
            {framework.citation}. {framework.scope}
          </p>
          <table className="report-map">
            <thead>
              <tr>
                <th>Provision</th>
                <th>What it asks for</th>
                <th>Evidence in this report</th>
                <th>Standing</th>
              </tr>
            </thead>
            <tbody>
              {framework.controls.map((control) => (
                <tr key={control.ref}>
                  <td>
                    <b>{control.ref}</b>
                    <span className="risk-ids">{control.title}</span>
                  </td>
                  <td>{control.asks}</td>
                  <td>
                    <ul>
                      {control.evidence.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  </td>
                  <td>
                    <Standing status={control.status} label={CONTROL_STATUS[control.status]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      <section>
        <h2>Verified breaks</h2>
        {findings.length === 0 && <p className="muted">None.</p>}
        {findings.map((finding, index) => {
          const map = mapped.get(finding.id);
          return (
            <div key={finding.id} className="report-finding">
              <h3>
                {index + 1}. <code>{finding.canary.public_label}</code> — {risk(finding.canary.type)}
              </h3>
              <p>
                Tripped in {finding.tripped_runs} of {finding.runs.length} replays. Verified {when(finding.verified_at)}
                {finding.hunter?.name ? `, found by ${finding.hunter.name}` : ""}.
                {finding.payout?.txid ? ` Paid ${formatZec(finding.payout.amount_zatoshi)} ${sym()}, transaction ${finding.payout.txid}.` : ""}
              </p>
              {map && (
                <p className="finding-map">
                  <Standing status={map.status} label={RISK_STATUS[map.status]} />
                  <span>
                    {map.vector}. Maps to {map.risks.join(", ")}. Broke configuration{" "}
                    <code>{finding.config_hash.slice(0, 16)}…</code>
                  </span>
                </p>
              )}
              {finding.attack.map((fill) => (
                <div key={fill.slot}>
                  <p className="muted">Attack: {words(fill.slot)}</p>
                  <pre>{fill.content}</pre>
                </div>
              ))}
              {finding.runs[0]?.evidence[0] && (
                <p>
                  Evidence: <code>{finding.runs[0].evidence[0].matched_text}</code> at {finding.runs[0].evidence[0].location}.
                </p>
              )}
            </div>
          );
        })}
      </section>

      <section id="method">
        <h2>Method and limits</h2>
        <ul>
          <li>
            <b>Standard of proof.</b> A break is recorded only when the same tripwire trips in at least{" "}
            {compliance.testing.threshold} of {compliance.testing.runs_per_attack} independent replays, each from
            an empty history. The threshold was fixed before any test. No person judges a result.
          </li>
          <li>
            <b>Sandbox.</b> Every replay runs against a copy of the agent whose tools are mocks. The tests show
            what the agent would try to do, not what your real tools would then allow.
          </li>
          <li>
            <b>The scan is a floor.</b> It replays known attacks once each against the current configuration. An
            agent that held against all of them has not been shown to be safe against attacks nobody has written
            yet.
          </li>
          <li>
            <b>Scope.</b> Only what the tripwires name is tested. A risk with no tripwire is marked not tested,
            and risks outside prompt-borne attacks (supply chain, training data, availability) are not covered.
          </li>
          <li>
            <b>The mapping.</b> A reference says which provision a piece of evidence bears on. The wording of
            each provision here is a summary, not the text of the law or the standard.
          </li>
        </ul>
        <p className="muted small">References: {compliance.references.join("; ")}.</p>
      </section>

      <section id="sign-off" className="report-sign">
        <h2>Sign-off</h2>
        <p className="muted">
          For the person responsible for the system to complete. trycanari records the tests; it does not sign for
          your organisation.
        </p>
        <dl>
          {["Prepared by", "Reviewed by", "Role", "Date"].map((line) => (
            <div key={line}>
              <dt>{line}</dt>
              <dd />
            </div>
          ))}
        </dl>
      </section>

      <footer className="report-foot">
        <p className="muted small">
          Bounty {bounty.id}. A break is recorded only when a tripwire trips; nothing here is a person's judgment.
          The full transcripts of every verified break are on the bounty's Findings tab.
        </p>
        <p className="muted small">
          Evidence digest (SHA-256): <code>{compliance.evidence_digest}</code>. The same evidence always gives the
          same digest, so a copy of this report can be checked against the service.
        </p>
      </footer>
    </article>
  );
}
