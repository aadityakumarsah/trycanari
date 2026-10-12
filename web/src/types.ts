import type { Currency } from "./money.ts";

// The shapes the JSON API returns. Money is an integer of the currency's smallest unit;
// the fields are named *_zatoshi whichever currency that is.

export type BountyState = "draft" | "awaiting_deposit" | "live" | "paused" | "closed";
export type CanaryType = "secret_leak" | "forbidden_tool";
export type EntryPoint = "user_message" | "document";
export type Verdict = "verified" | "not_verified" | "not_counted";

export interface CanaryPublic {
  type: CanaryType;
  public_label: string;
}

export interface BountyPublic {
  id: string;
  title: string;
  description: string;
  state: BountyState;
  accepting_attempts: boolean;
  reward_zatoshi: number;
  entry_points: EntryPoint[];
  canaries: CanaryPublic[];
  attempts: number;
  breaks: number;
  created_at: string;
}

export interface Stats {
  live_bounties: number;
  reward_pool_zatoshi: number;
  attempts: number;
  verified_breaks: number;
  paid_zatoshi: number;
}

export interface Break {
  bounty_id: string;
  bounty_title: string;
  canary: CanaryPublic;
  reward_zatoshi: number;
  tripped_runs: number;
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
  submitted_at: string;
}

export interface AttemptActivity {
  total: number;
  queued: number;
  verified: number;
  recent: AttemptLine[];
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

export interface AttemptStatus {
  state: "queued" | "running" | "done" | "error";
  verdict: Verdict | null;
  tripped_runs: number | null;
  total_runs: number | null;
  canary: CanaryPublic | null;
  replies: Reply[];
  payout: PayoutInfo | null;
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
  is_ambassador: boolean;
  is_admin: boolean;
  is_owner: boolean;
  invite_only: boolean;
  verified_breaks: number;
  credits: Credits;
}

export interface Invite {
  code: string;
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
  referral_code: string | null;
  created_at: string;
}

export interface AdminCode {
  code: string;
  used_by: string | null;
  owner_email: string | null;
  created_at: string;
}

export interface ReferralCode {
  code: string;
  used: boolean;
  created_at: string;
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

export interface Finding {
  id: string;
  state: "verified" | "paid";
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
