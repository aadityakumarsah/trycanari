import { Link } from "react-router-dom";

import { Icon } from "../components/Brand.tsx";

const DELIVERABLES = [
  ["Threat model", "One consequential workflow, its identities, tools, sensitive data and untrusted inputs."],
  ["Adversarial testing", "Direct and indirect injection, authorization, data leakage and business-rule attacks."],
  ["Reproducible evidence", "Version-bound transcripts, deterministic tripwires and a clear statement of test limitations."],
  ["Patch and re-test", "Every confirmed failure becomes a regression case and is replayed against the fix."],
  ["Continuous gate", "Known attacks run after prompt, model or tool changes and can block a release."],
] as const;

export function AssurancePage() {
  return (
    <main className="assurance-page">
      <section className="assurance-hero grid-bg">
        <div>
          <p className="eyebrow">Managed agent assurance</p>
          <h1>Prove what your agent will not do.</h1>
          <p className="lede">
            We threat-model one real tool-using workflow, attack its business boundaries, help verify the patch and
            turn every confirmed failure into a permanent regression test.
          </p>
          <div className="pilot-actions">
            <Link className="button accent" to="/pilot?source=assurance">Start an assessment</Link>
            <Link className="button secondary" to="/docs#assurance">Read the methodology</Link>
          </div>
        </div>
        <aside className="assurance-price">
          <p className="label">Founding assessment sprint</p>
          <p className="assurance-amount"><sup>$</sup>2,500</p>
          <p>Fixed scope · one agent · one consequential workflow · patch re-test included</p>
          <ul>
            <li><Icon name="check" size={14} /> Two-week delivery target after scope approval</li>
            <li><Icon name="lock" size={14} /> Encrypted scope and evidence</li>
            <li><Icon name="doc" size={14} /> Customer-ready evidence report</li>
          </ul>
        </aside>
      </section>

      <section className="sec assurance-fit">
        <div className="sec-head">
          <h2>Built for agents with consequences</h2>
          <p>Not a generic chatbot score. We test actions a security or engineering owner would have to explain.</p>
        </div>
        <div className="assurance-fit-grid">
          <article><Icon name="coin" /><h3>Money and approvals</h3><p>Refunds, transfers, credits, limits and human approval boundaries.</p></article>
          <article><Icon name="lock" /><h3>Private data</h3><p>Cross-customer access, prompt leakage, credentials and outbound data sinks.</p></article>
          <article><Icon name="globe" /><h3>Untrusted content</h3><p>Documents, pages, tool results, memory and sub-agent instructions.</p></article>
          <article><Icon name="code" /><h3>Tool execution</h3><p>Argument-level authorization, least privilege and dangerous action chains.</p></article>
        </div>
      </section>

      <section className="sec assurance-delivery">
        <div className="sec-head"><h2>What you receive</h2><p>Evidence your developers can reproduce, not a certificate or a vague safety score.</p></div>
        <ol>
          {DELIVERABLES.map(([title, detail], index) => (
            <li key={title}><span>{index + 1}</span><div><h3>{title}</h3><p>{detail}</p></div></li>
          ))}
        </ol>
      </section>

      <section className="sec assurance-truth">
        <div>
          <p className="eyebrow">What we will not claim</p>
          <h2>A passed assessment is not proof that an agent is safe.</h2>
        </div>
        <p>
          Results apply to the tested configuration, declared surface and sandbox behavior. Mock tools can differ
          from production. Unknown attacks remain possible. The report records what was tested, what broke, what
          held and whether the patch survived the same attack.
        </p>
      </section>
    </main>
  );
}
