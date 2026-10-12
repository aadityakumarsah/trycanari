import { useState, type FormEvent } from "react";

import { accountChanged, rememberedReferral } from "../account.tsx";
import { api } from "../api.ts";
import { useAction } from "../hooks.ts";
import { useToast } from "../toast.tsx";
import type { Account, CodeKind } from "../types.ts";
import { Icon } from "./Brand.tsx";
import { ErrorNote } from "./ui.tsx";

const COPY = {
  hunter: {
    title: "Enter your referral code to start hunting",
    body: "Your account and profile are ready. Taking part in bounties is by invitation, because every attack is replayed five times against a real model. Got a code? Enter it once and you are in.",
    button: "Unlock hunting",
  },
  sponsor: {
    title: "Enter a sponsor code to list an agent",
    body: "Listing an agent is by invitation. A sponsor code lets you list agents, lock rewards and read every break of them, and it lets you hunt too. Got one? Enter it once.",
    button: "Unlock sponsoring",
  },
} as const;

/** Where someone with an account, but no code of the kind yet, enters one: a referral
 * code to start hunting, or a sponsor code to list agents. */
export function ReferralGate({ compact = false, kind = "hunter" }: { compact?: boolean; kind?: CodeKind }) {
  const [code, setCode] = useState(rememberedReferral);
  const redeeming = useAction();
  const toast = useToast();

  async function redeem(event: FormEvent) {
    event.preventDefault();
    const done = await redeeming.run(async () => {
      const now = await api<Account>("POST", "/v1/account/referral", { body: { code: code.trim() } });
      accountChanged();
      setCode("");
      toast(now.can_sponsor ? "Code accepted. You can list agents and hunt now." : "Code accepted. You can hunt now.");
    });
    void done;
  }

  return (
    <section className={compact ? "panel gate referral compact" : "panel gate referral"}>
      <span className="gate-icon">
        <Icon name="key" size={20} />
      </span>
      <h2>{COPY[kind].title}</h2>
      <p className="muted">{COPY[kind].body}</p>
      <form className="referral-form" onSubmit={(event) => void redeem(event)}>
        <input
          className="code"
          required
          autoComplete="off"
          spellCheck={false}
          aria-label={kind === "sponsor" ? "Sponsor code" : "Referral code"}
          placeholder="XXXX-XXXX"
          value={code}
          onChange={(event) => setCode(event.target.value.toUpperCase())}
        />
        <button className="accent" disabled={redeeming.busy || !code.trim()}>
          {redeeming.busy ? "Checking…" : COPY[kind].button}
        </button>
      </form>
      <ErrorNote>{redeeming.error}</ErrorNote>
    </section>
  );
}
