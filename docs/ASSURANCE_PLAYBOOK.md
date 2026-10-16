# Managed agent assurance delivery playbook

This is the operator procedure for the paid service. The product records scope, status, price, limitations and version-bound test evidence; this document defines what the human delivery team must do.

## Offer and acceptance criteria

The founding sprint is **$2,500 fixed** for one agent, one consequential workflow and one tested configuration. It includes threat modelling, a baseline and tailored adversarial test pass, one evidence review, one remediation review and one re-test of the agreed patch. Private-cloud or on-premises execution, extra workflows and production integrations require a separate written scope.

Accept an engagement only when:

- a technical owner can change the agent during the sprint;
- the workflow has a concrete failure consequence;
- tools can be replaced with safe mocks or a controlled test environment;
- untrusted inputs, sensitive data and consequential actions can be named;
- the customer agrees that the result is evidence for a version and scope, not certification.

Reject or re-scope requests for live destructive testing, uncontrolled production credentials, employee surveillance, malware delivery or a guarantee that the system is safe.

## Delivery workflow

1. **Scope.** Record the workflow, feared failure, entry paths, data, actions, identity model, authorization boundaries, human approvals, emergency stop, environment and exclusions in the Assurance tab. Confirm the exact config fingerprint and fixed price.
2. **Threat model.** Draw the trust boundaries between user, retrieved content, tools, memory, sub-agents, identity provider and human approver. For each action ask: who is the principal, what resource is in scope, what policy is enforced outside the model, and what evidence proves the decision?
3. **Instrument.** Replace consequential tools with deterministic mocks. Add canaries for prohibited disclosure, calls and arguments. Never make the model itself the final judge of success.
4. **Test.** Run the six baseline techniques, replay relevant retained cases and add tailored cases for identity confusion, authorization bypass, cross-tenant data, chained tools, memory poisoning, approval spoofing and unsafe recovery. Use five independent replays and the fixed three-of-five threshold for a confirmed break.
5. **Triage.** Record reproducible input, tested fingerprint, tripwire, transcripts, business impact, preconditions and limitations. Separate confirmed failures from observations and out-of-scope risks.
6. **Remediate.** Prefer controls outside the model: scoped identities, server-side authorization, argument validation, allowlists, transaction limits, data minimisation, approval checks and revocation. Prompt changes may add defence in depth but are not an authorization boundary.
7. **Re-test.** Save every confirmed failure as a durable case. Run it and the baseline against the patched fingerprint. Mark complete only when the report states what held, what still failed and what was not tested.
8. **Continue.** Queue the regression gate on prompt, model, tool, policy or retrieval changes. A failed gate blocks release until reviewed; an infrastructure error is not a pass.

## Evidence package

The customer receives:

- scope and tested configuration fingerprint;
- threat boundaries and declared controls;
- methods, number of replays and decision threshold;
- confirmed findings with reproducible evidence;
- retained regression cases and re-test status;
- continuous-run history;
- explicit exclusions, mocked behavior and residual risk;
- a printable report and JSON evidence export.

Never describe a clean run as “safe,” “certified,” or proof of compliance. The report can support an audit, but applicability and conformity remain the customer’s and auditor’s decisions.

## Data handling and access

- Free-form scope, operator notes and limitations are encrypted at rest.
- Use test credentials and synthetic data by default. Do not request production secrets in scope text.
- Limit operator access to assigned delivery staff. Do not place private notes in customer-visible limitations.
- Put customer-visible assumptions and exclusions in **limitations**; keep coordination details in **operator notes**.
- Delete exported working files after delivery according to the agreed retention period. Product evidence follows the platform retention policy.
- Escalate any suspected production exposure immediately; stop testing until the customer confirms containment and authority to resume.

## Status definitions

| Status | Exit condition |
| --- | --- |
| `draft` | Customer has saved enough scope for a scoping call. |
| `requested` | Customer has requested delivery. |
| `scoping` | Price, environment, fingerprint, exclusions and owner are agreed. |
| `testing` | Mocks and tripwires are verified; execution is authorized. |
| `remediation` | Findings were reviewed and have named owners. |
| `retest` | A patch fingerprint is fixed and retained cases are queued. |
| `complete` | Evidence, limitations and re-test result were delivered. |
| `cancelled` | Reason and any data-handling follow-up are recorded. |

The customer cannot silently change scope while evidence is being produced. Operators can move the lifecycle, set the quoted price, keep private notes and publish limitations. Every operator change is audit logged.

## Market rationale and references

Commodity scanners already offer inexpensive probes. The paid product is the managed work around them: consequential-workflow scoping, tool and identity analysis, private deployment, deterministic evidence, remediation and a maintained regression corpus.

The method is informed by:

- [NIST AI Agent Standards Initiative](https://www.nist.gov/artificial-intelligence/ai-agent-standards-initiative)
- [NIST concept paper on software and AI agent identity and authorization](https://www.nccoe.nist.gov/sites/default/files/2026-02/accelerating-the-adoption-of-software-and-ai-agent-identity-and-authorization-concept-paper.pdf)
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [OWASP Agentic AI Security Initiative: Agent Control Standard](https://genai.owasp.org/resource/agent-control-standard-acs/)
- [Promptfoo pricing and deployment model](https://www.promptfoo.dev/pricing/)
- [Giskard open-source and enterprise offering](https://www.giskard.ai/pricing)

