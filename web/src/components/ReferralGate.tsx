import { useState, type FormEvent } from "react";

import { accountChanged, rememberedReferral } from "../account.tsx";
import { api } from "../api.ts";
import { useAction } from "../hooks.ts";
import { useToast } from "../toast.tsx";
import type { Account } from "../types.ts";
import { Icon } from "./Brand.tsx";
import { ErrorNote } from "./ui.tsx";

/** Where someone with an account, but no referral code yet, enters one to start hunting. */
export function ReferralGate({ compact = false }: { compact?: boolean }) {
  const [code, setCode] = useState(rememberedReferral);
  const redeeming = useAction();
  const toast = useToast();

  async function redeem(event: FormEvent) {
    event.preventDefault();
    const done = await redeeming.run(async () => {
      await api<Account>("POST", "/v1/account/referral", { body: { code: code.trim() } });
      accountChanged();
    });
    if (done) toast("Referral code accepted. You can hunt now.");
  }

  return (
    <section className={compact ? "panel gate referral compact" : "panel gate referral"}>
      <span className="gate-icon">
        <Icon name="key" size={20} />
      </span>
      <h2>Enter your referral code to start hunting</h2>
      <p className="muted">
        Your account and profile are ready. Taking part in bounties is by invitation, because every attack is
        replayed five times against a real model. Got a code? Enter it once and you are in.
      </p>
      <form className="referral-form" onSubmit={(event) => void redeem(event)}>
        <input
          className="code"
          required
          autoComplete="off"
          spellCheck={false}
          aria-label="Referral code"
          placeholder="XXXX-XXXX"
          value={code}
          onChange={(event) => setCode(event.target.value.toUpperCase())}
        />
        <button className="accent" disabled={redeeming.busy || !code.trim()}>
          {redeeming.busy ? "Checking…" : "Unlock hunting"}
        </button>
      </form>
      <ErrorNote>{redeeming.error}</ErrorNote>
    </section>
  );
}
