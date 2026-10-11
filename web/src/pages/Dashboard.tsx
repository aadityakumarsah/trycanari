import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api.ts";
import { Badge, ErrorNote } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { formatZec, parseZec } from "../money.ts";
import type { SponsorBounty } from "../types.ts";

export function Dashboard() {
  const bounties = useLoad((signal) => api<SponsorBounty[]>("GET", "/v1/sponsor/bounties", { signal }), "mine");
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [reward, setReward] = useState("");
  const creating = useAction();

  async function create(event: FormEvent) {
    event.preventDefault();
    await creating.run(async () => {
      const body = { title: title.trim(), description: description.trim(), reward_zatoshi: parseZec(reward) };
      const created = await api<{ id: string }>("POST", "/v1/bounties", { body });
      navigate(`/dashboard/b/${created.id}`);
    });
  }

  return (
    <>
      <h1>Your bounties</h1>
      <ErrorNote>{bounties.error}</ErrorNote>
      {bounties.loading && <p className="muted">Loading…</p>}
      {bounties.data?.length === 0 && <p className="empty">You have no bounties yet.</p>}
      {bounties.data && bounties.data.length > 0 && (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>State</th>
                <th className="num">Reward (ZEC)</th>
                <th className="num">Escrow (ZEC)</th>
              </tr>
            </thead>
            <tbody>
              {bounties.data.map((bounty) => (
                <tr key={bounty.id}>
                  <td>
                    <Link to={`/dashboard/b/${bounty.id}`}>{bounty.title}</Link>
                  </td>
                  <td>
                    <Badge state={bounty.closing ? "closing" : bounty.state} />
                  </td>
                  <td className="num">{formatZec(bounty.reward_zatoshi)}</td>
                  <td className="num">
                    {formatZec(bounty.balance_zatoshi)} of {formatZec(bounty.required_zatoshi)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <section>
        <h2>New bounty</h2>
        <form onSubmit={(event) => void create(event)}>
          <label>
            Title
            <input required maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label>
            Public description
            <textarea rows={3} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} />
            <small>Hunters see this: what the agent does and what you want them to try.</small>
          </label>
          <label>
            Reward in ZEC
            <input required inputMode="decimal" value={reward} onChange={(e) => setReward(e.target.value)} />
          </label>
          <ErrorNote>{creating.error}</ErrorNote>
          <button disabled={creating.busy}>Create draft</button>
        </form>
      </section>
    </>
  );
}
