import { Link, useParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { ErrorNote, Skeleton, when, words } from "../components/ui.tsx";
import { useLoad } from "../hooks.ts";
import { formatZec, sym } from "../money.ts";
import type { AttemptActivity, Finding, Ledger, Scan, SponsorBountyDetail } from "../types.ts";

interface Everything {
  bounty: SponsorBountyDetail;
  findings: Finding[];
  activity: AttemptActivity;
  ledger: Ledger;
  scan: Scan;
}

/**
 * Everything adversarial testing has shown about one agent, on one page that prints
 * cleanly. It is what a sponsor hands to whoever asks "how was this tested?".
 */
export function Report() {
  const { bountyId = "" } = useParams();
  const base = `/v1/bounties/${bountyId}`;
  const page = useLoad<Everything>(async (signal) => {
    const [bounty, findings, activity, ledger, scan] = await Promise.all([
      api<SponsorBountyDetail>("GET", `/v1/sponsor/bounties/${bountyId}`, { signal }),
      api<Finding[]>("GET", `${base}/findings`, { signal }),
      api<AttemptActivity>("GET", `${base}/attempts`, { signal }),
      api<Ledger>("GET", `${base}/ledger`, { signal }),
      api<Scan>("GET", `${base}/scan`, { signal }),
    ]);
    return { bounty, findings, activity, ledger, scan };
  }, bountyId);

  if (page.status === 404 || page.status === 422) return <NotFound message="That bounty does not exist." />;
  if (!page.data) return page.error ? <ErrorNote>{page.error}</ErrorNote> : <Skeleton block lines={6} />;
  const { bounty, findings, activity, ledger, scan } = page.data;
  const config = bounty.config;
  const paid = ledger.entries.filter((entry) => entry.kind === "payout").reduce((sum, entry) => sum - entry.amount_zatoshi, 0);
  const risk = (type: string) => scan.risks[type] ?? words(type);
  const held = activity.total - activity.queued - activity.verified;

  return (
    <article className="report">
      <div className="report-bar">
        <Link to={`/dashboard/b/${bounty.id}`}>← Back to the bounty</Link>
        <button type="button" onClick={() => window.print()}>
          <Icon name="doc" size={15} /> Print or save as PDF
        </button>
      </div>

      <header className="report-head">
        <p className="eyebrow">Adversarial testing report</p>
        <h1>{bounty.title}</h1>
        <p className="muted">
          Generated {when(new Date().toISOString())} by trycanari. Attacks were replayed against a sandboxed copy
          of the agent with mock tools; nothing real was moved or sent.
        </p>
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
            : `${activity.verified} of ${activity.total} attacks ${activity.verified === 1 ? "was a verified break" : "were verified breaks"}: the same tripwire tripped in at least 3 of 5 independent replays. Each is detailed below with its evidence.`}
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
                  <td>{risk(canary.type)}</td>
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

      <section>
        <h2>Verified breaks</h2>
        {findings.length === 0 && <p className="muted">None.</p>}
        {findings.map((finding, index) => (
          <div key={finding.id} className="report-finding">
            <h3>
              {index + 1}. <code>{finding.canary.public_label}</code> — {risk(finding.canary.type)}
            </h3>
            <p>
              Tripped in {finding.tripped_runs} of {finding.runs.length} replays. Verified {when(finding.verified_at)}
              {finding.hunter?.name ? `, found by ${finding.hunter.name}` : ""}.
              {finding.payout?.txid ? ` Paid ${formatZec(finding.payout.amount_zatoshi)} ${sym()}, transaction ${finding.payout.txid}.` : ""}
            </p>
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
        ))}
      </section>

      <footer className="report-foot">
        <p className="muted small">
          Bounty {bounty.id}. A break is recorded only when a tripwire trips; nothing here is a person's judgment.
          The full transcripts of every verified break are on the bounty's Findings tab.
        </p>
      </footer>
    </article>
  );
}
