import { pictureOf } from "../account.tsx";
import { formatZec, sym } from "../money.ts";
import type { Breaker } from "../types.ts";
import { Ago, Avatar } from "./ui.tsx";

/** What to call a hunter: the name on their profile, their GitHub handle, or neither. */
export function hunterName(hunter: Pick<Breaker, "name" | "github">): string {
  return hunter.name ?? (hunter.github ? `@${hunter.github}` : "Anonymous hunter");
}

/** The hunters who broke a bounty: who they are, which canary fell and what it paid. */
export function BrokenBy({ hunters, compact = false }: { hunters: Breaker[]; compact?: boolean }) {
  return (
    <ul className={compact ? "breakers compact" : "breakers"}>
      {hunters.map((hunter) => (
        <li key={hunter.verified_at}>
          <Avatar name={hunter.name ?? hunter.github} picture={pictureOf(hunter)} size={compact ? 32 : 44} />
          <span className="breaker-who">
            <b>{hunterName(hunter)}</b>
            <small>
              {hunter.name && hunter.github && (
                <a href={`https://github.com/${hunter.github}`} target="_blank" rel="noreferrer">
                  @{hunter.github}
                </a>
              )}
              <span>
                tripped <span className="code">{hunter.canary.public_label}</span>
                {compact ? "" : ` in ${hunter.tripped_runs} of 5 runs`}
              </span>
              <Ago iso={hunter.verified_at} />
            </small>
          </span>
          <span className="breaker-won">
            <b>
              {formatZec(hunter.reward_zatoshi)} <span>{sym()}</span>
            </b>
            <small className={hunter.paid ? "stage paid" : "stage review"}>{hunter.paid ? "Paid" : "Payout under way"}</small>
          </span>
        </li>
      ))}
    </ul>
  );
}
