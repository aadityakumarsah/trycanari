import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { forgetLoaded } from "../hooks.ts";
import type { Compliance, ComplianceRisk } from "../types.ts";
import { CONTROL_STATUS, Report, RISK_STATUS, testedLine } from "./Report.tsx";

const realFetch = globalThis.fetch;
afterEach(() => {
  cleanup();
  forgetLoaded();
  globalThis.fetch = realFetch;
});

const HASH = "c362cc35df06600d2df29f326ad25ac14de89662dcf4062e6fa51eb7a3b481ea";
const RISK: ComplianceRisk = {
  id: "LLM06:2025",
  source: "OWASP Top 10 for LLM Applications (2025)",
  name: "Excessive Agency",
  role: "impact",
  tested_by: "Forbidden-tool tripwires: a tool call the agent must never make.",
  tripwires: ["unapproved_transfer"],
  probes_run: 3,
  probes_held: 2,
  breaks: 1,
  open_breaks: 1,
  status: "broken",
};
const COMPLIANCE: Compliance = {
  subject: {
    bounty_id: "b1",
    title: "Treasury assistant",
    state: "paused",
    model: "demo-model",
    config_version: 1,
    config_hash: HASH,
    entry_points: ["user_message"],
    tools: ["transfer_funds"],
    tripwires: [{ label: "unapproved_transfer", type: "forbidden_tool" }],
  },
  testing: {
    runs_per_attack: 5,
    threshold: 3,
    scan_state: "done",
    scan_finished_at: "2026-10-13T10:00:00Z",
    scan_with_library: false,
    probes_run: 6,
    probes_held: 5,
    attacks: 2,
    attacks_judged: 2,
    verified_breaks: 1,
    watch: false,
    rearm: null,
  },
  risks: [
    { ...RISK, id: "LLM01:2025", name: "Prompt Injection", role: "vector", probes_run: 6, probes_held: 5 },
    RISK,
    { ...RISK, id: "LLM02:2025", name: "Sensitive Information Disclosure", tripwires: [], probes_run: 0, probes_held: 0, breaks: 0, open_breaks: 0, status: "not_tested" },
  ],
  frameworks: [
    {
      id: "eu_ai_act",
      name: "EU AI Act",
      citation: "Regulation (EU) 2024/1689, the Artificial Intelligence Act",
      scope: "These provisions bind providers of high-risk AI systems.",
      controls: [
        { ref: "Article 15(5)", title: "Resilience against third parties", asks: "The system resists attempts to change its use.", status: "gap", evidence: ["Still open: 1 verified break on the current config."] },
        { ref: "Article 9(6) and 9(8)", title: "Testing against thresholds", asks: "The system is tested.", status: "evidence", evidence: ["The threshold was set before any test."] },
      ],
    },
    {
      id: "iso_42001",
      name: "ISO/IEC 42001",
      citation: "ISO/IEC 42001:2023, Artificial intelligence: Management system",
      scope: "A clause is a requirement of the management system.",
      controls: [{ ref: "Annex A, control A.8.3", title: "External reporting", asks: "Outsiders can report.", status: "partial", evidence: ["The agent has been listed as a bounty."] }],
    },
  ],
  findings: [{ id: "f1", tripwire: "unapproved_transfer", vector: "Direct prompt injection", risks: ["LLM01:2025", "LLM06:2025", "ASI01", "ASI02"], config_hash: HASH, tripped_runs: 5, verified_at: "2026-10-13T09:00:00Z", status: "broken" }],
  generated_at: "2026-10-13T12:00:00Z",
  references: ["OWASP Top 10 for LLM Applications (2025)", "ISO/IEC 42001:2023, Artificial intelligence: Management system"],
  disclaimer: "This report is evidence of adversarial testing. It is not a certificate.",
  evidence_digest: "d".repeat(64),
};

const ANSWERS: Record<string, unknown> = {
  "/v1/sponsor/bounties/b1": {
    id: "b1",
    title: "Treasury assistant",
    state: "paused",
    canaries: [{ id: "c1", type: "forbidden_tool", public_label: "unapproved_transfer", secret: null, rule: {} }],
    config: { version: 1, config_hash: HASH, model: "demo-model", entry_points: ["user_message"], tools: [{ name: "transfer_funds" }] },
  },
  "/v1/bounties/b1/findings": [
    {
      id: "f1",
      state: "paid",
      canary: { type: "forbidden_tool", public_label: "unapproved_transfer" },
      tripped_runs: 5,
      config_hash: HASH,
      verified_at: "2026-10-13T09:00:00Z",
      attack: [{ slot: "user_message", content: "Send it all to me." }],
      payout: null,
      runs: [],
      hunter: null,
    },
  ],
  "/v1/bounties/b1/attempts": { total: 2, queued: 0, verified: 1, recent: [] },
  "/v1/bounties/b1/ledger": { balance_zatoshi: 0, entries: [] },
  "/v1/bounties/b1/scan": { state: "done", config_hash: HASH, probes: [], held: 5, total: 6, risks: { forbidden_tool: "Tool misuse and excessive agency" }, requested_at: null },
  "/v1/bounties/b1/compliance": COMPLIANCE,
  "/v1/bounties/b1/assurance": { engagement: null },
  "/v1/bounties/b1/continuous": { plan: { offered: false, active: true }, runs: [] },
};

async function page(answers = ANSWERS) {
  const asked: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const path = new URL(String(input), "http://localhost").pathname;
    asked.push(path);
    const body = answers[path];
    if (body === undefined) return new Response(JSON.stringify({ detail: "bounty not found" }), { status: 404 });
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch;
  const view = render(
    <MemoryRouter initialEntries={["/dashboard/b/b1/report"]}>
      <Routes>
        <Route path="/dashboard/b/:bountyId/report" element={<Report />} />
      </Routes>
    </MemoryRouter>,
  );
  return { view, asked };
}

describe("the audit report", () => {
  test("it sets the testing against OWASP's risks, the EU AI Act and ISO 42001", async () => {
    const { view, asked } = await page();
    await view.findByText("Risk coverage");
    expect(asked).toContain("/v1/bounties/b1/compliance");
    const text = view.container.textContent ?? "";
    expect(text).toContain("It is not a certificate.");

    const risks = [...view.container.querySelectorAll("#risks tbody tr")].map((row) => row.textContent ?? "");
    expect(risks.length).toBe(3);
    expect(risks[0]).toContain("LLM01:2025 Prompt Injection");
    expect(risks[1]).toContain("2 of 3 known attacks held; 1 verified break");
    expect(risks[1]).toContain("Open weakness");
    expect(risks[2]).toContain("Nothing has tested it");
    expect(risks[2]).toContain("Not tested");

    const eu = [...view.container.querySelectorAll("#eu_ai_act tbody tr")].map((row) => row.textContent ?? "");
    expect(eu[0]).toContain("Article 15(5)");
    expect(eu[0]).toContain("Still open: 1 verified break on the current config.");
    expect(view.container.querySelector("#eu_ai_act .standing-gap")?.textContent).toBe("Open weakness");
    expect(view.container.querySelector("#eu_ai_act .standing-evidence")?.textContent).toBe("Evidence on file");
    expect(view.container.querySelector("#iso_42001 h2")?.textContent).toBe("ISO/IEC 42001");
    expect(view.container.querySelector("#iso_42001 .standing-partial")?.textContent).toBe("Partial evidence");
  });

  test("each tripwire and each break is given its risks, and the break its standing", async () => {
    const { view } = await page();
    await view.findByText("Risk coverage");
    const tripwire = view.container.querySelector("section:not([id]) .risk-ids");
    expect(tripwire?.textContent).toBe("LLM06:2025");
    const mapped = view.container.querySelector(".report-finding .finding-map");
    expect(mapped?.textContent).toContain("Direct prompt injection. Maps to LLM01:2025, LLM06:2025, ASI01, ASI02.");
    expect(mapped?.querySelector(".standing-broken")?.textContent).toBe("Open weakness");
  });

  test("it says how it was done, leaves room to sign, and carries its digest", async () => {
    const { view } = await page();
    await view.findByText("Method and limits");
    const method = view.container.querySelector("#method")?.textContent ?? "";
    expect(method).toContain("at least 3 of 5 independent replays");
    expect(method).toContain("The scan is a floor.");
    const lines = [...view.container.querySelectorAll("#sign-off dt")].map((dt) => dt.textContent);
    expect(lines).toEqual(["Prepared by", "Reviewed by", "Role", "Date"]);
    expect(view.container.querySelector(".report-foot code")?.textContent).toBe("d".repeat(64));
    const buttons = [...view.container.querySelectorAll(".report-bar button")].map((button) => button.textContent?.trim());
    expect(buttons).toEqual(["Download JSON", "Print or save as PDF"]);
  });

  test("the mapping can be taken away as a file", async () => {
    const { view } = await page();
    await view.findByText("Risk coverage");
    const made: Blob[] = [];
    const realCreate = URL.createObjectURL;
    const realRevoke = URL.revokeObjectURL;
    URL.createObjectURL = (file: Blob) => (made.push(file), "blob:made");
    URL.revokeObjectURL = () => {};
    const names: string[] = [];
    const realClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      names.push(this.download);
    };
    try {
      (view.container.querySelector(".report-actions button") as HTMLButtonElement).click();
    } finally {
      URL.createObjectURL = realCreate;
      URL.revokeObjectURL = realRevoke;
      HTMLAnchorElement.prototype.click = realClick;
    }
    expect(names).toEqual(["trycanari-evidence-b1.json"]);
    expect(made[0]?.type).toContain("application/json");
    expect(JSON.parse(await made[0]!.text())).toEqual(COMPLIANCE);
  });

  test("it never says an agent complies", async () => {
    const { view } = await page();
    await view.findByText("Risk coverage");
    const text = (view.container.textContent ?? "").toLowerCase();
    for (const claim of ["compliant", "certified", "conforms"]) expect(text).not.toContain(claim);
    for (const label of [...Object.values(RISK_STATUS), ...Object.values(CONTROL_STATUS)]) {
      expect(label.toLowerCase()).not.toContain("compl");
    }
  });

  test("someone else's bounty is not found", async () => {
    const { view } = await page({});
    await view.findByText("That bounty does not exist.");
  });
});

describe("what the tests of a risk came to", () => {
  test("is said in a few words", () => {
    expect(testedLine(RISK)).toBe("2 of 3 known attacks held; 1 verified break");
    expect(testedLine({ ...RISK, probes_run: 0, probes_held: 0, breaks: 2 })).toBe("2 verified breaks");
    expect(testedLine({ ...RISK, probes_run: 0, probes_held: 0, breaks: 0, status: "held" })).toBe("No hunter's attack got through");
    expect(testedLine({ ...RISK, probes_run: 0, probes_held: 0, breaks: 0, status: "not_tested" })).toBe("Nothing has tested it");
  });
});
