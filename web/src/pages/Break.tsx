import { Link, useParams } from "react-router-dom";

import { NotFound } from "../App.tsx";
import { pictureOf } from "../account.tsx";
import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { hunterName } from "../components/BrokenBy.tsx";
import { Ago, Avatar, Copyable, ErrorNote, Skeleton, when, words } from "../components/ui.tsx";
import { useLoad } from "../hooks.ts";
import { currency, formatZec, sym } from "../money.ts";
import { useToast } from "../toast.tsx";
import type { BreakPage as Break } from "../types.ts";

/** Where to look a payment up, for a currency whose payments anyone can look up. */
function explorer(txid: string): string | null {
  const money = currency();
  if (money.private || money.network === "demo") return null;
  const cluster = money.network ? `?cluster=${money.network}` : "";
  return `https://explorer.solana.com/tx/${txid}${cluster}`;
}

/**
 * One verified break, on a page that can be sent to anyone: who broke which agent, in how
 * many of the replays, and the payment. The attack itself stays with the sponsor.
 */
export function BreakPage() {
  const { breakId = "" } = useParams();
  const found = useLoad((signal) => api<Break>("GET", `/v1/breaks/${breakId}`, { signal }), breakId, `break:${breakId}`);
  const toast = useToast();

  if (found.status === 404 || found.status === 422) return <NotFound message="That break does not exist." />;
  if (!found.data) return found.error ? <ErrorNote>{found.error}</ErrorNote> : <Skeleton block lines={5} />;

  const item = found.data;
  const who = item.hunter ? hunterName(item.hunter) : "An anonymous hunter";
  const paid = item.payout_state === "sent" || item.payout_state === "confirmed";
  const link = item.txid ? explorer(item.txid) : null;

  return (
    <article className="break-card">
      <p className="eyebrow">
        <Icon name="shield" size={14} /> Verified break
      </p>
      <h1>
        {who} broke <Link to={`/b/${item.bounty_id}`}>{item.bounty_title}</Link>
      </h1>
      <p className="lede">
        The same canary tripped in {item.tripped_runs} of {item.total_runs} sandboxed replays of one attack. The
        attack belongs to the sponsor and is not shown.
      </p>

      <div className="break-people">
        <div>
          <span className="label">Hunter</span>
          <span className="person">
            <Avatar
              name={item.hunter?.name ?? item.hunter?.github ?? null}
              picture={item.hunter ? pictureOf(item.hunter) : null}
              size={44}
            />
            <span>
              <b>{who}</b>
              {item.hunter?.github && (
                <small>
                  <a href={`https://github.com/${item.hunter.github}`} target="_blank" rel="noreferrer">
                    @{item.hunter.github}
                  </a>
                </small>
              )}
            </span>
          </span>
        </div>
        <div>
          <span className="label">Agent listed by</span>
          <span className="person">
            <Avatar name={item.sponsor?.name ?? null} picture={item.sponsor ? pictureOf(item.sponsor) : null} size={44} />
            <span>
              <b>{item.sponsor?.name ?? item.sponsor?.company ?? "A sponsor"}</b>
              {item.sponsor?.company && item.sponsor.name && <small>{item.sponsor.company}</small>}
            </span>
          </span>
        </div>
      </div>

      <dl className="facts">
        <div>
          <dt>Canary tripped</dt>
          <dd>
            <code>{item.canary.public_label}</code> <span className="muted">({words(item.canary.type)})</span>
          </dd>
        </div>
        <div>
          <dt>Replays</dt>
          <dd>
            <span className="runs" aria-label={`${item.tripped_runs} of ${item.total_runs} runs tripped`}>
              {Array.from({ length: item.total_runs }, (_, index) => (
                <i key={index} className={index < item.tripped_runs ? "on" : undefined} />
              ))}
            </span>{" "}
            {item.tripped_runs} of {item.total_runs}
          </dd>
        </div>
        <div>
          <dt>Reward</dt>
          <dd>
            <b>
              {formatZec(item.reward_zatoshi)} {sym()}
            </b>{" "}
            <span className={paid ? "stage paid" : "stage review"}>{paid ? "Paid" : "Payout under way"}</span>
          </dd>
        </div>
        <div>
          <dt>Verified</dt>
          <dd title={when(item.verified_at)}>
            <Ago iso={item.verified_at} />
          </dd>
        </div>
      </dl>

      {item.txid && (
        <div className="break-proof">
          <Copyable label="Payout transaction" value={item.txid} />
          {link && (
            <a className="button secondary" href={link} target="_blank" rel="noreferrer">
              <Icon name="link" size={14} /> See it on Solana Explorer
            </a>
          )}
        </div>
      )}

      <div className="break-actions">
        <button
          type="button"
          className="secondary"
          onClick={() => void navigator.clipboard.writeText(window.location.href).then(() => toast("Link copied"))}
        >
          <Icon name="link" size={15} /> Copy link to this page
        </button>
        <Link className="button" to={`/b/${item.bounty_id}`}>
          See the bounty
        </Link>
      </div>
    </article>
  );
}
