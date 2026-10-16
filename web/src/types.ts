import type { Currency } from "./money.ts";

// The shapes the JSON API returns. Money is an integer of the currency's smallest unit;
// the fields are named *_zatoshi whichever currency that is.

export type BountyState = "draft" | "awaiting_deposit" | "live" | "paused" | "closed";
export type CanaryType =
  | "secret_leak"
  | "system_prompt_leak"
  | "forbidden_tool"
  | "unauthorized_action"
  | "data_exfiltration"
  | "phishing_link_injection"
  | "malicious_code_injection"
  | "policy_violation";
export type EntryPoint = "user_message" | "document" | "tool_result";
export type Verdict = "verified" | "not_verified" | "not_counted";

export interface CanaryPublic {
  type: CanaryType;
  public_label: string;
}

export interface BountyPublic {
  id: string;
  title: string;
  description: string;
  /** Who lists it, as they describe themselves on their profile. */
  sponsor: SponsorPublic | null;
  state: BountyState;
  accepting_attempts: boolean;
  reward_zatoshi: number;
  entry_points: EntryPoint[];
  /** What the agent is asked to do when the hunter does not write the user's message. */
  task?: string;
  canaries: CanaryPublic[];
  attempts: number;
  breaks: number;
  /** The hunters who broke it, newest first. */
  broken_by: Breaker[];
  created_at: string;
}

export interface SponsorPublic {
  name: string | null;
  picture_url: string | null;
  github: string | null;
  company: string | null;
  website: string | null;
}

/** What an invite lets its account do: attack, or list agents and attack. */
export type CodeKind = "hunter" | "sponsor";

/** Who broke a bounty: what the hunter chose to show on their profile. Never the attack. */
export interface Breaker {
  /** The break's own page: /break/{id}. */
  id?: string | null;
  name: string | null;
  picture_url: string | null;
  github: string | null;
  canary: CanaryPublic;
  reward_zatoshi: number;
  tripped_runs: number;
  paid: boolean;
  /** The house hunter found it: nothing was paid. */
  house?: boolean;
  verified_at: string;
}

export interface Stats {
  live_bounties: number;
  reward_pool_zatoshi: number;
  attempts: number;
  verified_breaks: number;
  paid_zatoshi: number;
}

export interface Break {
  id?: string | null;
  bounty_id: string;
  bounty_title: string;
  canary: CanaryPublic;
  reward_zatoshi: number;
  tripped_runs: number;
  /** The house hunter found it: nothing was paid. */
  house?: boolean;
  verified_at: string;
}

export interface Activity {
  stats: Stats;
  recent_breaks: Break[];
}

export interface AttemptLine {
  id: string;
  state: "queued" | "running" | "done" | "error";
  verified: boolean;
  /** Made by the house hunter, the platform's own attacker. */
  house?: boolean;
  submitted_at: string;
}

export interface AttemptActivity {
  total: number;
  queued: number;
  verified: number;
  /** How many of the total the house hunter made. */
  house?: number;
  recent: AttemptLine[];
}

/** One of a sponsor's bounties on their sponsor page: what it is and how it has fared. */
export interface Sponsorship extends SponsorBounty {
  created_at: string;
  attempts: number;
  breaks: number;
  last_attack_at: string | null;
  paid_zatoshi: number;
  /** Of its working config. Null and empty before an agent is described. */
  model: string | null;
  entry_points: EntryPoint[];
  canaries: number;
}

/** A verified break of one of the sponsor's bounties, and who made it. */
export interface SponsorBreak {
  id: string;
  bounty_id: string;
  bounty_title: string;
  hunter: Hunter | null;
  /** The platform's own house hunter found it: nothing was paid. */
  house: boolean;
  canary: CanaryPublic;
  tripped_runs: number;
  total_runs: number;
  reward_zatoshi: number;
  payout_state: string | null;
  txid: string | null;
  verified_at: string;
}

export interface SponsorTotals {
  bounties: number;
  live: number;
  drafts: number;
  paused: number;
  past: number;
  escrow_zatoshi: number;
  attempts: number;
  breaks: number;
  paid_zatoshi: number;
}

/** `GET /v1/sponsor/overview`: everything a sponsor has listed, and who broke what. */
export interface SponsorOverview {
  member_since: string;
  totals: SponsorTotals;
  bounties: Sponsorship[];
  breaks: SponsorBreak[];
}

/** The house hunter's go at a bounty's current config. */
export interface HouseHunt {
  state: "running" | "done";
  outcome: "held" | "broke" | "stopped" | null;
  tries: number;
  detail: string | null;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
}

export interface Reply {
  text: string;
  tool_calls: ToolCall[];
}

export interface PayoutInfo {
  state: "pending" | "sending" | "sent" | "confirmed" | "failed";
  amount_zatoshi: number;
  txid: string | null;
}

/** One replay in brief: what it tripped and what the agent did. */
export interface RunSummary {
  run_index: number;
  /** Public labels of the canaries this run tripped. */
  tripped: string[];
  /** How many times the agent spoke, and the tools it called, in order. */
  replies: number;
  tool_calls: string[];
  tokens: number;
}

export interface CanaryTally {
  canary: CanaryPublic;
  tripped_runs: number;
}

export interface AttemptStatus {
  state: "queued" | "running" | "done" | "error";
  /** Worker tries so far. Provider-capacity deferrals do not increase this. */
  tries: number;
  /** ISO time when a provider-throttled attempt becomes eligible to retry. */
  retry_at: string | null;
  verdict: Verdict | null;
  tripped_runs: number | null;
  total_runs: number | null;
  canary: CanaryPublic | null;
  replies: Reply[];
  payout: PayoutInfo | null;
  /** While queued: attempts on this bounty replayed before this one. */
  queue_ahead: number | null;
  /** Replays finished so far, out of `runs_total`. */
  runs_done: number;
  runs_total: number;
  /** Runs one canary has to trip in for the break to be verified. */
  threshold: number;
  /** Once done: every replay in brief, and how each canary of the agent fared. */
  runs: RunSummary[];
  canaries: CanaryTally[];
}

export interface AttemptAccepted {
  attempt_id: string;
  status_token: string;
}

export interface Providers {
  google: boolean;
  invite_only: boolean;
  currency: Currency;
}

export interface CostStep {
  up_to_characters: number;
  credits: number;
}

export interface Credits {
  balance: number;
  cap: number;
  window_hours: number;
  tier: string;
  next_tier: string | null;
  next_cap: number | null;
  costs: CostStep[];
  /** Extra tokens the first attack on a bounty costs, on top of its length price. */
  new_bounty_premium: number;
}

export interface Account {
  email: string;
  name: string | null;
  picture_url: string | null;
  picture_uploaded: boolean;
  can_upload_picture: boolean;
  google_linked: boolean;
  github: string | null;
  company: string | null;
  role: string | null;
  location: string | null;
  website: string | null;
  bio: string | null;
  can_hunt: boolean;
  /** Whether the account may list an agent. */
  can_sponsor: boolean;
  /** Banned by an admin: it can neither attack nor list agents. */
  banned?: boolean;
  is_ambassador: boolean;
  is_admin: boolean;
  is_owner: boolean;
  /** Whether the account has what the Watch plan gives, and until when it has paid. */
  plan_active?: boolean;
  plan_until?: string | null;
  invite_only: boolean;
  verified_breaks: number;
  credits: Credits;
}

export interface Invite {
  code: string;
  kind: CodeKind;
  available: boolean;
  invited_by: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface Person {
  id: string;
  email: string;
  name: string | null;
  picture_url: string | null;
  github: string | null;
  company: string | null;
  is_admin: boolean;
  is_owner: boolean;
  is_ambassador: boolean;
  can_hunt: boolean;
  hunting_granted: boolean;
  can_sponsor: boolean;
  sponsoring_granted: boolean;
  referral_code: string | null;
  sponsor_code: string | null;
  banned: boolean;
  /** Tokens left of the cap for the last 24 hours. */
  credits_balance: number;
  credits_cap: number;
  /** The cap an admin set by hand in place of the tier's, if any. */
  credit_limit: number | null;
  verified_breaks: number;
  plan_until?: string | null;
  created_at: string;
}

export interface CodeUser {
  email: string;
  name: string | null;
  joined_at: string;
}

export interface AdminCode {
  code: string;
  kind: CodeKind;
  used_by: string | null;
  /** How many people it admits, how many it has, and who they are. */
  max_uses: number;
  uses: number;
  users: CodeUser[];
  owner_email: string | null;
  created_at: string;
}

export interface ReferralCode {
  code: string;
  used: boolean;
  max_uses: number;
  uses: number;
  users: { email: string; name: string | null; joined_at: string }[];
  created_at: string;
}

export interface ReferralCodesPage {
  items: ReferralCode[];
  total: number;
  seat_limit: number;
  available_seats: number;
}

export interface Session {
  signed_in: boolean;
  email: string | null;
}

export interface SponsorBounty {
  id: string;
  title: string;
  description: string;
  state: BountyState;
  closing: boolean;
  reward_zatoshi: number;
  balance_zatoshi: number;
  required_zatoshi: number;
}

export interface Tool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  result: string;
  /** It reads from somewhere a stranger can write; a hunter's planted text comes back from it. */
  untrusted?: boolean;
}

export interface ConfigDetail {
  version: number;
  config_hash: string;
  live: boolean;
  system_prompt: string;
  model: string;
  tools: Tool[];
  params: Record<string, unknown>;
  entry_points: EntryPoint[];
  /** What the agent is asked to do when the hunter does not write the user's message. */
  task?: string;
  /** The sponsor's own model endpoint, when the agent runs there. */
  endpoint_base: string | null;
  endpoint_key_set: boolean;
}

export interface ModelTest {
  ok: boolean;
  detail: string;
  milliseconds: number;
  models: string[];
}

export interface ProviderSetting {
  source: "admin" | "environment" | "none";
  api_base: string | null;
  key_hint: string | null;
  updated_at: string | null;
  models_in_use: string[];
}

export interface ProviderTest extends ModelTest {
  missing_models: string[];
}

export interface PlatformModels {
  provider: string | null;
  models: string[];
}

export interface CanaryDetail extends CanaryPublic {
  id: string;
  secret: string | null;
  rule: Record<string, unknown> | null;
}

export interface Rearm {
  state: "queued" | "running" | "passed" | "failed";
  config_hash: string;
  tripped_runs: number;
}

export interface SponsorBountyDetail extends SponsorBounty {
  escrow_address: string;
  deposit_code: string;
  confirmations_required: number;
  refund_address: string | null;
  rearm: Rearm | null;
  config: ConfigDetail | null;
  canaries: CanaryDetail[];
  /** Whether its breaks are replayed against other sponsors' agents, and whether it is scanned daily. */
  shares_breaks: boolean;
  watch: boolean;
  /** Null when the house hunter has not been at this config. */
  house_hunt?: HouseHunt | null;
}

export interface Plan {
  offered: boolean;
  active: boolean;
  until: string | null;
  price_zatoshi: number;
  days: number;
  escrow_address: string;
  code: string | null;
  confirmations_required: number;
}

export interface BreakPage {
  id: string;
  bounty_id: string;
  bounty_title: string;
  sponsor: SponsorPublic | null;
  hunter: Hunter | null;
  canary: CanaryPublic;
  tripped_runs: number;
  total_runs: number;
  reward_zatoshi: number;
  payout_state: string | null;
  txid: string | null;
  /** The house hunter found it: there is no payout. */
  house?: boolean;
  verified_at: string;
}

export interface Message {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  tool_calls: ToolCall[];
}

export interface Evidence {
  canary: CanaryPublic | null;
  message_index: number;
  location: string;
  representation: string | null;
  matched_text: string;
}

export interface Run {
  run_index: number;
  transcript: Message[];
  evidence: Evidence[];
}

export interface Probe {
  id: string;
  name: string;
  technique: string;
  aimed_at: string;
  /** Where it comes from: the playbook, a break this bounty paid for, or the shared library. */
  source?: "playbook" | "past_break" | "library";
  tripped: string[];
  attack: { slot: EntryPoint; content: string }[];
  reply: string;
  tool_calls: ToolCall[];
}

export interface Scan {
  state: "queued" | "running" | "done" | "error" | null;
  config_hash: string | null;
  probes: Probe[];
  held: number;
  total: number;
  risks: Record<string, string>;
  requested_at: string | null;
  finished_at?: string | null;
  with_library?: boolean;
  corpus_type?: CanaryType | null;
}

export interface ContinuousProbe {
  case_id: string;
  canary_type: CanaryType;
  source: "past_break" | "corpus";
  tripped: boolean;
}

export interface ContinuousRun {
  id: string;
  config_version: number;
  config_hash: string;
  trigger: "version" | "ci" | "schedule" | "model_update";
  state: "queued" | "running" | "done" | "error";
  passed: boolean | null;
  total: number;
  succeeded: number;
  success_rate: number | null;
  covered_canary_types: CanaryType[];
  regressed: ContinuousProbe[];
  created_at: string;
  finished_at: string | null;
}

export interface ContinuousTesting {
  plan: Plan;
  runs: ContinuousRun[];
}

/** An entry of one of OWASP's lists, and how the bounty's testing stands against it. */
export interface ComplianceRisk {
  id: string;
  source: string;
  name: string;
  role: "vector" | "impact";
  tested_by: string;
  tripwires: string[];
  probes_run: number;
  probes_held: number;
  breaks: number;
  open_breaks: number;
  status: "not_tested" | "held" | "broken" | "patched" | "fixed";
}

/** A provision of a framework, and whether there is evidence that bears on it. */
export interface ComplianceControl {
  ref: string;
  title: string;
  asks: string;
  status: "evidence" | "partial" | "gap" | "none" | "not_applicable";
  evidence: string[];
}

export interface ComplianceFramework {
  id: string;
  name: string;
  citation: string;
  scope: string;
  controls: ComplianceControl[];
}

export interface ComplianceFinding {
  id: string;
  tripwire: string;
  vector: string;
  risks: string[];
  config_hash: string;
  tripped_runs: number;
  verified_at: string;
  status: "broken" | "patched" | "fixed";
}

/** `GET /v1/bounties/{id}/compliance`: the testing set against the lists an auditor uses. */
export interface Compliance {
  subject: {
    bounty_id: string;
    title: string;
    state: string;
    model: string | null;
    config_version: number | null;
    config_hash: string | null;
    entry_points: string[];
    tools: string[];
    tripwires: { label: string; type: string }[];
  };
  testing: {
    runs_per_attack: number;
    threshold: number;
    scan_state: string | null;
    scan_finished_at: string | null;
    scan_with_library: boolean;
    probes_run: number;
    probes_held: number;
    attacks: number;
    attacks_judged: number;
    verified_breaks: number;
    watch: boolean;
    rearm: string | null;
  };
  risks: ComplianceRisk[];
  frameworks: ComplianceFramework[];
  findings: ComplianceFinding[];
  generated_at: string;
  references: string[];
  disclaimer: string;
  evidence_digest: string;
}

export interface Leader {
  name: string | null;
  picture_url: string | null;
  github: string | null;
  company: string | null;
  breaks: number;
  earned_zatoshi: number;
  last_break_at: string;
}

export interface Hunter {
  name: string | null;
  picture_url: string | null;
  github: string | null;
  company: string | null;
  role: string | null;
  website: string | null;
}

export interface AdminAction {
  admin_email: string;
  action: string;
  target_email: string | null;
  detail: string | null;
  created_at: string;
}

export type PilotStage = "prototype" | "beta" | "production";
export type PilotStatus = "new" | "contacted" | "qualified" | "pilot" | "paid" | "closed";
export type PilotGoal = "baseline" | "bounty" | "continuous" | "audit";

export interface PilotApplicationAccepted {
  id: string;
  message: string;
}

export interface AdminPilot {
  id: string;
  name: string;
  work_email: string;
  company: string;
  company_website: string | null;
  role: string | null;
  stage: PilotStage;
  use_case: string;
  goals: PilotGoal[];
  source: string;
  status: PilotStatus;
  notes: string;
  created_at: string;
  updated_at: string;
  account_id: string | null;
  bounties: number;
  launched_bounties: number;
  scans_completed: number;
  verified_breaks: number;
  paid_plan: boolean;
}

export interface PilotFunnel {
  applications: number;
  signed_up: number;
  listed_agent: number;
  ran_scan: number;
  launched_bounty: number;
  paid: number;
  status_counts: Record<string, number>;
}

export interface AdminPilotsPage {
  items: AdminPilot[];
  total: number;
  funnel: PilotFunnel;
  traction: {
    tested_agents_7d: number;
    regressions_caught: number;
    median_patch_hours: number | null;
    active_pilots: number;
    paid_conversion_percent: number;
  };
}

export interface Finding {
  id: string;
  /** "waived": the house hunter found it, and nothing is paid for it. */
  state: "verified" | "paid" | "waived";
  canary: CanaryPublic;
  tripped_runs: number;
  config_hash: string;
  verified_at: string;
  attack: { slot: EntryPoint; content: string }[];
  payout: PayoutInfo | null;
  runs: Run[];
  hunter: Hunter | null;
}

export interface Ledger {
  balance_zatoshi: number;
  entries: { kind: string; amount_zatoshi: number; txid: string | null; created_at: string }[];
}

export interface Funding {
  state: BountyState;
  escrow_address: string;
  deposit_code: string;
  amount_due_zatoshi: number;
  confirmations_required: number;
}

export interface HuntPayout extends PayoutInfo {
  to_address: string;
  created_at: string;
}

/** One attack the signed-in account made: what was sent, how it was judged, what it paid. */
export interface Hunt {
  id: string;
  bounty_id: string;
  bounty_title: string;
  bounty_state: BountyState;
  reward_zatoshi: number;
  state: "queued" | "running" | "done" | "error";
  verdict: Verdict | null;
  tripped_runs: number | null;
  total_runs: number | null;
  canary: CanaryPublic | null;
  attack: { slot: EntryPoint; content: string }[];
  payout_address: string;
  cost_credits: number;
  submitted_at: string;
  verified_at: string | null;
  payout: HuntPayout | null;
}

export interface Hunts {
  totals: {
    attempts: number;
    verified: number;
    earned_zatoshi: number;
    pending_zatoshi: number;
    credits_spent: number;
  };
  items: Hunt[];
}

export type NotificationKind = "bounty_broken" | "break_verified" | "payout_held" | "payout_sent" | "reward_paid";

export interface Notification {
  id: string;
  kind: NotificationKind;
  bounty_id: string;
  bounty_title: string;
  /** The hunter, on a sponsor's notification. */
  actor: { name: string | null; picture_url: string | null; github: string | null } | null;
  amount_zatoshi: number | null;
  txid: string | null;
  /** The break was the house hunter's: nothing was paid for it. */
  house?: boolean;
  read: boolean;
  created_at: string;
}

export interface Notifications {
  unread: number;
  items: Notification[];
}
