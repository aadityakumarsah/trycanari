import { useState } from "react";

import { accountChanged } from "../account.tsx";
import { api } from "../api.ts";
import { useAction, useInterval, useLoad } from "../hooks.ts";
import { formatZec, sym } from "../money.ts";
import type { Plan } from "../types.ts";
import { Icon } from "./Brand.tsx";
import { Copyable, ErrorNote, when } from "./ui.tsx";

/**
 * The Watch plan, for a sponsor: what it gives, whether they hold it, and how to pay.
 * Shown only where a plan is sold; where none is, everyone has what it gives.
 */
export function WatchPlan() {
  const plan = useLoad((signal) => api<Plan>("GET", "/v1/account/plan", { signal }), "plan", "account:plan");
  const asking = useAction();
  const [paying, setPaying] = useState(false);
  // What the plan ran to when the sponsor asked to pay: a later date means the deposit landed.
  const [was, setWas] = useState<string | null>(null);
  const until = plan.data?.until ?? null;
  const landed = paying && until !== was;
  // While the sponsor is paying, watch for the deposit to land.
  useInterval(
    () =>
      void plan.reload().then(() => {
        accountChanged();
      }),
    5000,
    paying && !landed,
  );

  if (!plan.data?.offered) return null;
  const data = plan.data;
  const price = `${formatZec(data.price_zatoshi)} ${sym()}`;

  async function ask() {
    await asking.run(async () => {
      await api<Plan>("POST", "/v1/account/plan");
      await plan.reload();
      setWas(until);
      setPaying(true);
    });
  }

  return (
    <section className={data.active ? "panel plan on" : "panel plan"}>
      <div className="plan-head">
        <span className="gate-icon">
          <Icon name="shield" size={20} />
        </span>
        <div>
          <h2>
            Watch plan {data.active && <span className="badge badge-live">active</span>}
          </h2>
          <p className="muted">
            {data.active && until
              ? `Runs until ${when(until)}.`
              : `${price} for ${data.days} days, on top of the fee on a reward. A bounty still costs nothing until it is broken.`}
          </p>
        </div>
        <button type="button" className={data.active ? "secondary" : "accent"} disabled={asking.busy} onClick={() => void ask()}>
          {asking.busy ? "One moment…" : data.active ? `Add ${data.days} days` : "Get the plan"}
        </button>
      </div>
      <ul className="plan-gives">
        <li>
          <Icon name="check" size={14} /> A scan of every watched bounty, every day
        </li>
        <li>
          <Icon name="check" size={14} /> Your agent tested against attacks that broke other sponsors' agents
        </li>
        <li>
          <Icon name="check" size={14} /> Scan again whenever you like, from your build as well
        </li>
      </ul>
      <ErrorNote>{asking.error}</ErrorNote>
      {landed && <p className="done">Payment received. Your plan now runs until {until ? when(until) : "later"}.</p>}
      {paying && !landed && data.code && (
        <div className="plan-pay">
          <p>
            Send <b>{price}</b> to the address below, with the code as the memo. The plan starts after{" "}
            {data.confirmations_required} confirmations; this page will show it.
          </p>
          <Copyable label="Address" value={data.escrow_address} />
          <Copyable label="Memo" value={data.code} />
          <Copyable label={`Amount (${sym()})`} value={formatZec(data.price_zatoshi)} />
        </div>
      )}
    </section>
  );
}
