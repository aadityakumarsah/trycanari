# Design partner traction sprint

This is the operating plan for the next milestone. Do not count a logo, an email reply, a demo or an application as traction. Count a team only when it has connected a real agent and run a test.

## Four-week target

| Outcome | Target | Source of truth |
| --- | ---: | --- |
| Qualified founder or technical-owner conversations | 10 | Pilot CRM notes |
| Accepted design partners | 5–10 | Pilot CRM status = `pilot` |
| Real agents configured | 5 | Account has a bounty/config |
| Agents tested each week | 5 | Completed scans or bounty attempts |
| Paid pilots | 2–3 | Credited Watch-plan payment or funded bounty |
| Regressions caught | Report every one | Continuous-test result |
| Median patch time | Under 7 days | Finding time to successful re-test |
| Pilot-to-paid conversion | At least 30% | Real credited payments / active pilots |

The admin **Pilots** tab is the live funnel. Signup, listed agents, completed scans, launched bounties, verified breaks and plan payments are derived from product records. Only relationship status and private notes are entered manually.

## Offer

Use one offer for every first conversation:

> Bring one real tool-using agent and one consequential workflow. The $2,500 founding sprint includes a threat model, adversarial testing, reproducible evidence, one remediation review and one patch re-test. If we confirm a break, it becomes a permanent regression test. Private deployment is scoped separately.

Do not promise a certification, perfect security or immunity from prompt injection. The deliverable is reproducible evidence: what was tested, what broke, what held and whether a patch survived the same attack.

Qualify before offering unpaid work. A short scoping call and product walkthrough can be free; the delivery sprint is paid:

1. Confirm one consequential workflow, an owner who can patch it and safe mock tools.
2. Agree the tested version, exclusions, evidence handling and fixed price.
3. Deliver the sprint and patch re-test.
4. Offer continuous regression or a funded bounty only when the team changes the agent often enough to need it.

## Ideal first customer

Prioritize teams where all of these are true:

- An LLM can call a tool, act on private data, browse untrusted content or communicate externally.
- A failed instruction-boundary check has a clear business consequence.
- A founder or technical owner can change the prompt, tools or model this week.
- The real tools can be replaced with mocks for safe replay.
- The team needs customer-facing proof, not only an internal red-team score.

Best initial wedges:

1. Customer-support agents that refund, cancel, edit accounts or read customer data.
2. Voice agents for collections, support or regulated workflows.
3. Browser and MCP agents that consume untrusted pages and tool results.
4. Finance and compliance agents with approval limits or sensitive records.

## First account list

Use public company channels and founder introductions. Do not scrape or store private personal data. Start with ten highly tailored messages, then expand only after learning which risk gets replies.

| Priority | Company | Why the problem is concrete | Opening angle |
| --- | --- | --- | --- |
| 1 | [Hyperbrowser](https://www.ycombinator.com/companies/hyperbrowser) | Browser infrastructure executes agent workloads against untrusted pages. | Test page-to-agent prompt injection and outbound data sinks. |
| 1 | [Browser Use](https://www.ycombinator.com/companies/browser-use) | Browser agents cross web-content and tool boundaries. | Turn known browser attacks into deterministic regression probes. |
| 1 | [Fini](https://www.ycombinator.com/companies/fini) | Customer-support automation touches private account context and actions. | Prove refund/action limits and data isolation with mock tools. |
| 1 | [Diligent](https://www.ycombinator.com/companies/diligent) | Voice collections workflows combine authority, urgency and consequential actions. | Test authority pressure and action caps without calling real systems. |
| 1 | [Sphinx](https://www.ycombinator.com/companies/sphinx) | Financial-services agents operate in a high-consequence environment. | Produce replayable evidence for tool limits and policy rules. |
| 1 | [Socratix](https://www.ycombinator.com/companies/socratix-ai) | Compliance agents reason over sensitive and regulated material. | Test private-data exfiltration and policy-pattern canaries. |
| 1 | [Veritus](https://www.ycombinator.com/companies/veritus) | Collections agents need strict identity, disclosure and action boundaries. | Attack the exact workflow with all external calls mocked. |
| 1 | [Proximitty](https://www.ycombinator.com/companies/proximitty) | Regulated voice workflows need reproducible controls, not a one-time demo. | Re-run the same attacks whenever prompts or models change. |
| 1 | [Samora AI](https://www.ycombinator.com/companies/samora-ai) | Voice agents interact with customers and downstream business systems. | Baseline one production workflow and convert each break into a test. |
| 2 | [Grep AI](https://www.ycombinator.com/companies/grep-ai) | Agentic code and repository access creates sensitive-context boundaries. | Test untrusted content crossing into tools and outbound sinks. |
| 2 | [Agency](https://www.ycombinator.com/companies/agency) | Agent observability can pair production signals with adversarial replay. | Add a deterministic security evidence layer to observed agent behavior. |
| 2 | [Inkeep and other YC support-agent teams](https://www.ycombinator.com/companies/industry/customer-support) | Support is a dense category of tool-using agents with measurable consequences. | Lead with refund caps, account isolation and private-data leakage. |

Before each message, verify the company still operates the relevant product and rewrite the first two sentences for its exact workflow.

## Founder outreach

Subject: `Can I try to break one {company} agent?`

```text
Hi {name} — {one sentence showing the exact agent/tool boundary you understood}.

I’m building Canari: deterministic bug bounties and regression tests for AI agents. Instead of an LLM grading another LLM, we replay attacks five times and check exact canaries such as unauthorized tool calls, sensitive-data exfiltration and policy patterns.

I’m looking for 5 founding customers. The fixed $2,500 sprint covers one real workflow: threat model, attacks, reproducible evidence, remediation review and one patch re-test. I’ll help mock dangerous tools, and every verified break becomes a test your team can rerun after each prompt/model change.

Would a 25-minute setup call next week be useful? The goal is to show one real result, not give you a platform demo.
```

Follow-up after three business days:

```text
One concrete test I would run on {company}: {specific attack boundary and deterministic evidence}.

If it is irrelevant, tell me and I will close the loop. If it is real, I can set up the first scan with you in 25 minutes.
```

Final follow-up after seven business days:

```text
Closing the loop. I’m keeping this cohort to teams that can test and patch weekly. If agent security becomes urgent later, the assessment scope is here: {public_url}/assurance.
```

## Discovery call

Do not spend the call touring features. Leave with one agent and one feared failure.

1. What can the agent do that would require an incident review if a user caused it?
2. Which inputs are untrusted: user message, document, retrieval result, tool result or prior turn?
3. What must never appear in an output or outbound argument?
4. Which tool arguments have allowlists, caps or scopes?
5. How is this tested today, and what escaped those tests?
6. Who can patch the prompt/tools, and how quickly?
7. What evidence would a customer or auditor actually trust?

End by scheduling the setup session. A “good conversation” with no next test is not a pipeline stage.

## Weekly pilot cadence

| Week | Work | Required evidence |
| --- | --- | --- |
| 1 | Configure one agent, mock tools, run baseline | Completed scan and reviewed result |
| 2 | Patch the highest-value failure and re-run | Pass/fail of the exact saved attack |
| 3 | Change a prompt, tool or model and run continuously | A regression caught or a documented clean run |
| 4 | Review outcomes and choose paid next step | Credited plan, funded bounty or explicit no |

Record after every call: agent tested, attacks run, result, regression caught, finding time, patch time, next owner and next date. Avoid NPS and broad satisfaction questions until teams use the product weekly.

## Weekly founder review

Every Friday, answer from the product and CRM:

- How many real agents were tested this week?
- How many distinct attacks did they withstand?
- How many verified breaks and regressions were caught?
- What was median time from finding to a passing re-test?
- Which pilot converted to a real payment, and why?
- Which team stopped testing, and what blocked it?
- What single product change would make next week’s test easier?

This follows YC’s core operating advice: launch, talk to users, focus on a small number of customers with an acute problem and use real or paying usage as the proof point—not a growing feature list.

## Reference

- [YC’s Essential Startup Advice](https://www.ycombinator.com/blog/ycs-essential-startup-advice/)
- [Garry Tan’s tips for applying to YC](https://www.ycombinator.com/library/J7-garry-tan-s-tips-for-applying-to-yc)
- [OpenAI: Designing AI agents to resist prompt injection](https://openai.com/index/designing-agents-to-resist-prompt-injection/)
