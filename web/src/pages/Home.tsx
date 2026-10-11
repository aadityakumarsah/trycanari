import { Link } from "react-router-dom";

import { api } from "../api.ts";
import { Canaries, ErrorNote } from "../components/ui.tsx";
import { useLoad } from "../hooks.ts";
import { formatZec } from "../money.ts";
import type { BountyPublic } from "../types.ts";

export function Home() {
  const bounties = useLoad((signal) => api<BountyPublic[]>("GET", "/v1/bounties", { signal }), "home");
  return (
    <>
      <h1>Live bounties</h1>
      <p className="lede">
        Each bounty is an AI agent whose sponsor pays for an attack that provably works. An attack is replayed
        five times against a sandboxed copy; trip the same canary in three and the reward is sent to you.
      </p>
      <ErrorNote>{bounties.error}</ErrorNote>
      {bounties.loading && <p className="muted">Loading…</p>}
      {bounties.data?.length === 0 && <p className="empty">No bounty is live right now.</p>}
      {bounties.data && bounties.data.length > 0 && (
        <ul className="cards">
          {bounties.data.map((bounty) => (
            <li key={bounty.id} className="card">
              <p className="reward">
                {formatZec(bounty.reward_zatoshi)} <span>ZEC</span>
              </p>
              <h2>
                <Link to={`/b/${bounty.id}`}>{bounty.title}</Link>
              </h2>
              {bounty.description && <p className="muted">{bounty.description}</p>}
              <Canaries items={bounty.canaries} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
