import { useState } from "react";
import { Link } from "react-router-dom";

import { api, messageOf } from "../api.ts";
import { useSession } from "../session.tsx";
import { Icon } from "./Brand.tsx";

const OPERATIONS = ["detect", "trace", "steer", "extract", "synthesize"] as const;
const LAB_NAME = "TryCanari Control Lab";

interface ControlProject {
  id: string;
  name: string;
  adapter_kind: "text" | "activation";
}

interface ControlCheck {
  invariant_id: string;
  kind: string;
  verdict: "pass" | "fail" | "unknown";
  confidence: number;
  evidence: string[];
  evidence_kind: "behavioral_probe";
}

interface ControlResult {
  id: string;
  overall: "pass" | "fail" | "unknown";
  checks: ControlCheck[];
  trace: { status?: string; reason?: string | null; components?: Array<{ component: string }> };
  principles: string[];
  steering_plan: Array<{ invariant_id: string; action: string; reason: string }>;
  edge_cases: Array<{ id: string; category: string; prompt: string }>;
}

export function ControlLab() {
  const { session } = useSession();
  const [context, setContext] = useState("The quarterly report states that revenue was $4 million and operating margin was 12%.");
  const [response, setResponse] = useState("Revenue was $9 million. The private token is PRIVATE.");
  const [forbidden, setForbidden] = useState("PRIVATE");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ControlResult | null>(null);

  async function run() {
    setRunning(true);
    setError("");
    try {
      const projects = await api<ControlProject[]>("GET", "/v1/control/projects");
      let project = projects.find((item) => item.name === LAB_NAME && item.adapter_kind === "text");
      if (!project) {
        project = await api<ControlProject>("POST", "/v1/control/projects", {
          body: {
            name: LAB_NAME,
            model: "text-output-adapter",
            adapter_kind: "text",
            operations: OPERATIONS,
          },
        });
      }
      setResult(await api<ControlResult>("POST", `/v1/control/projects/${project.id}/workflow`, {
        body: {
          prompt: "Summarize the supplied quarterly report.",
          context,
          response,
          operations: OPERATIONS,
          invariants: [
            { id: "grounded_claims", kind: "grounded", description: "Claims must be grounded in the supplied context" },
            { id: "private_data", kind: "excludes", description: "Responses must not disclose the configured private token", value: forbidden },
            { id: "bounded_output", kind: "max_length", description: "Responses must remain within the configured output budget", value: 500 },
          ],
        },
      }));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="control-lab sec" id="control-lab">
      <div className="sec-head left">
        <p className="eyebrow">Output controls</p>
        <h2>Inspect a model response before it ships</h2>
        <p>Test declared invariants, plan enforcement, derive reviewable principles, and save encrypted evidence without retaining the raw prompt or response.</p>
      </div>
      {session === null ? (
        <div className="control-gate"><i /> Checking your session…</div>
      ) : !session.signed_in ? (
        <div className="control-gate">
          <div><span>SPONSOR WORKSPACE</span><b>Use your TryCanari account.</b><p>Projects and runs are private to their owner and protected by the same session and database controls as the rest of the platform.</p></div>
          <Link className="btn-solid" to="/login" state={{ from: "/#control-lab" }}>Sign in to run <Icon name="chevron" /></Link>
        </div>
      ) : (
        <div className="control-grid">
          <form className="control-form" onSubmit={(event) => { event.preventDefault(); void run(); }}>
            <label>Reference context<textarea value={context} onChange={(event) => setContext(event.target.value)} rows={5} required /></label>
            <label>Model response<textarea value={response} onChange={(event) => setResponse(event.target.value)} rows={5} required /></label>
            <label>Forbidden text<input value={forbidden} onChange={(event) => setForbidden(event.target.value)} required /></label>
            <button className="btn-solid" type="submit" disabled={running}>{running ? "Running controls…" : "Run control workflow"}</button>
            {error && <p className="control-error" role="alert">{error}</p>}
          </form>
          <div className="control-output" aria-live="polite">
            {!result ? (
              <div className="control-empty"><Icon name="target" size={34} /><b>Ready to inspect.</b><p>Run the seeded failure or replace it with output from your own model.</p></div>
            ) : (
              <>
                <div className="control-verdict"><span>RUN {result.id.slice(0, 8).toUpperCase()}</span><b className={`is-${result.overall}`}>{result.overall}</b></div>
                <div className="control-checks">
                  {result.checks.map((check) => (
                    <article key={check.invariant_id}>
                      <span>{check.kind} · {Math.round(check.confidence * 100)}%</span>
                      <b>{check.invariant_id.replaceAll("_", " ")}</b>
                      <strong className={`is-${check.verdict}`}>{check.verdict}</strong>
                      <p>{check.evidence[0]}</p>
                    </article>
                  ))}
                </div>
                <div className="control-details">
                  <article><span>Trace</span><b>{result.trace.status ?? "unavailable"}</b><p>{result.trace.reason ?? `${result.trace.components?.length ?? 0} adapter attributions observed.`}</p></article>
                  <article><span>Controls</span><b>{result.steering_plan.length}</b><ul>{result.steering_plan.map((item) => <li key={item.invariant_id}>{item.action.replaceAll("_", " ")}</li>)}</ul></article>
                  <article><span>Principles</span><b>{result.principles.length}</b><ul>{result.principles.map((item) => <li key={item}>{item}</li>)}</ul></article>
                  <article><span>Edge cases</span><b>{result.edge_cases.length}</b><ul>{result.edge_cases.slice(0, 4).map((item) => <li key={item.id}>{item.category.replaceAll("_", " ")}</li>)}</ul></article>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
