import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { ErrorNote } from "../components/ui.tsx";
import { useAction } from "../hooks.ts";
import type { PilotApplicationAccepted, PilotGoal, PilotStage } from "../types.ts";

const GOALS: { value: PilotGoal; title: string; detail: string }[] = [
  { value: "baseline", title: "Find the first holes", detail: "Run a free baseline scan before funding anything." },
  { value: "bounty", title: "Launch a bounty", detail: "Let vetted hunters test a sandboxed copy for a reward." },
  { value: "continuous", title: "Catch regressions", detail: "Replay known attacks when prompts, tools or models change." },
  { value: "audit", title: "Build customer evidence", detail: "Turn real tests and fixes into a report your team can share." },
];

export function Pilot() {
  const [params] = useSearchParams();
  const submitting = useAction();
  const [accepted, setAccepted] = useState<PilotApplicationAccepted | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const goals = data.getAll("goals") as PilotGoal[];
    await submitting.run(async () => {
      const result = await api<PilotApplicationAccepted>("POST", "/v1/pilot/applications", {
        body: {
          name: data.get("name"),
          work_email: data.get("work_email"),
          company: data.get("company"),
          company_website: data.get("company_website"),
          role: data.get("role"),
          stage: data.get("stage") as PilotStage,
          use_case: data.get("use_case"),
          goals,
          source: params.get("source")?.slice(0, 100) || "pilot-page",
          fax: data.get("fax"),
        },
      });
      setAccepted(result);
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  if (accepted) {
    return (
      <section className="pilot-success">
        <span className="pilot-success-mark"><Icon name="check" size={28} /></span>
        <p className="eyebrow">Application received</p>
        <h1>Let’s test something real.</h1>
        <p className="lede">{accepted.message}</p>
        <p>
          While you wait, create your account so the same work email can connect this application to your scans,
          bounties and results automatically.
        </p>
        <div className="pilot-actions">
          <Link className="button" to="/login">Create an account</Link>
          <Link className="secondary button" to="/docs">See how testing works</Link>
        </div>
      </section>
    );
  }

  return (
    <div className="pilot-page">
      <section className="pilot-hero">
        <div>
          <p className="eyebrow">Design partner pilot</p>
          <h1>Find the agent failures your evals miss.</h1>
          <p className="lede">
            Bring one real tool-using agent. We will help you run a baseline attack scan, review the evidence and
            turn every verified break into a regression test.
          </p>
          <div className="pilot-proof">
            <span><Icon name="target" size={17} /> One real agent</span>
            <span><Icon name="repeat" size={17} /> Weekly testing</span>
            <span><Icon name="lock" size={17} /> Secrets stay private</span>
          </div>
        </div>
        <aside className="pilot-offer">
          <p className="label">What the pilot includes</p>
          <ol>
            <li><b>Free baseline scan</b><span>See which known attacks already work before depositing a reward.</span></li>
            <li><b>Hands-on setup</b><span>Scope entry points, mock dangerous tools and choose deterministic canaries.</span></li>
            <li><b>Weekly evidence review</b><span>Track attacks held, regressions caught and time from finding to patch.</span></li>
          </ol>
          <p className="pilot-commitment">In return: test a real agent and give candid weekly product feedback.</p>
        </aside>
      </section>

      <section className="pilot-apply" aria-labelledby="pilot-form-title">
        <div className="pilot-apply-copy">
          <p className="eyebrow">Apply in two minutes</p>
          <h2 id="pilot-form-title">Tell us what the agent can do.</h2>
          <p>We are prioritizing production or near-production agents that call tools, send messages, move money, browse or answer from private data.</p>
          <ul className="pilot-fit">
            <li><Icon name="check" size={15} /> A founder or technical owner joins the review</li>
            <li><Icon name="check" size={15} /> Tools can be replaced with safe mock results</li>
            <li><Icon name="check" size={15} /> The team can patch and re-test weekly</li>
          </ul>
        </div>
        <form className="pilot-form" onSubmit={(event) => void submit(event)}>
          <div className="pilot-fields">
            <label>
              Your name
              <input name="name" required minLength={2} maxLength={80} autoComplete="name" />
            </label>
            <label>
              Work email
              <input name="work_email" required type="email" maxLength={254} autoComplete="email" />
            </label>
            <label>
              Company
              <input name="company" required minLength={2} maxLength={120} autoComplete="organization" />
            </label>
            <label>
              Your role <small>Optional</small>
              <input name="role" maxLength={100} autoComplete="organization-title" />
            </label>
            <label>
              Company website <small>Optional</small>
              <input name="company_website" type="url" maxLength={300} placeholder="https://" autoComplete="url" />
            </label>
            <label>
              Agent stage
              <select name="stage" defaultValue="beta" required>
                <option value="prototype">Prototype</option>
                <option value="beta">Beta / design partners</option>
                <option value="production">In production</option>
              </select>
            </label>
          </div>
          <label>
            What does the agent do, and what can go wrong?
            <textarea
              name="use_case"
              required
              minLength={30}
              maxLength={4000}
              rows={5}
              placeholder="Example: Our support agent reads account data and can issue refunds up to $100. We need to prove a customer cannot make it refund more or expose another customer's data."
            />
          </label>
          <fieldset className="pilot-goals">
            <legend>What would make this pilot valuable?</legend>
            {GOALS.map((goal, index) => (
              <label key={goal.value}>
                <input type="checkbox" name="goals" value={goal.value} defaultChecked={index === 0} />
                <span><b>{goal.title}</b><small>{goal.detail}</small></span>
              </label>
            ))}
          </fieldset>
          <label className="pilot-honeypot" aria-hidden="true">
            Fax
            <input name="fax" tabIndex={-1} autoComplete="off" />
          </label>
          <ErrorNote>{submitting.error}</ErrorNote>
          <div className="pilot-submit">
            <button disabled={submitting.busy}>{submitting.busy ? "Sending…" : "Apply for the pilot"}</button>
            <small>No sales list. Your application and agent details stay private.</small>
          </div>
        </form>
      </section>
    </div>
  );
}
