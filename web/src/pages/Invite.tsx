import { paidIn } from "../money.ts";
import { useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { accountChanged, rememberReferral, useAccount, useProviders } from "../account.tsx";
import { api } from "../api.ts";
import { Icon } from "../components/Brand.tsx";
import { ErrorNote, Skeleton } from "../components/ui.tsx";
import { useAction, useLoad } from "../hooks.ts";
import { useSession } from "../session.tsx";
import { useToast } from "../toast.tsx";
import type { Account, Invite } from "../types.ts";

/** Where an invite link lands: one click, or one sign-in, and the bounties are open. */
export function InvitePage() {
  const { code = "" } = useParams();
  const invite = useLoad((signal) => api<Invite>("GET", `/v1/invites/${encodeURIComponent(code)}`, { signal }), code);
  const { session } = useSession();
  const account = useAccount().data;
  const providers = useProviders();
  const accepting = useAction();
  const navigate = useNavigate();
  const toast = useToast();

  // Kept, so that signing in from here or anywhere else in this tab still redeems it.
  useEffect(() => {
    if (invite.data) rememberReferral(invite.data.code);
  }, [invite.data]);

  async function accept() {
    const done = await accepting.run(async () => {
      await api<Account>("POST", "/v1/account/referral", { body: { code } });
      accountChanged();
    });
    if (done) {
      const sponsor = invite.data?.kind === "sponsor";
      toast(sponsor ? "You are in. List your first agent." : "You are in. Pick a bounty.");
      navigate(sponsor ? "/dashboard" : "/#bounties");
    }
  }

  if (invite.status === 404) {
    return (
      <div className="invite-card">
        <span className="invite-seal bad">
          <Icon name="close" size={26} />
        </span>
        <h1>This invite does not exist</h1>
        <p className="muted">Check the link with whoever sent it. Codes look like XXXX-XXXX.</p>
        <Link className="button secondary" to="/">
          Back to trycanari
        </Link>
      </div>
    );
  }
  if (!invite.data) return invite.error ? <ErrorNote>{invite.error}</ErrorNote> : <Skeleton block lines={3} />;

  const { available, invited_by: from, kind } = invite.data;
  const sponsoring = kind === "sponsor";
  // What this invite would add is already theirs.
  const has = sponsoring ? account?.can_sponsor : account?.can_hunt;
  const signedIn = session?.signed_in === true;
  const start = `/v1/auth/google/start?code=${encodeURIComponent(invite.data.code)}`;

  return (
    <div className="invite-card">
      <span className="invite-seal">
        <img src="/app/brand/mascot.png" alt="" width={58} height={50} />
      </span>
      <p className="eyebrow">{from ? `${from} invited you` : "You are invited"}</p>
      <p className="invite-kind">{sponsoring ? "Sponsor invite" : "Hunter invite"}</p>
      <h1>{sponsoring ? "List your agent on trycanari" : "Hunt on trycanari"}</h1>
      <p className="lede">
        {sponsoring
          ? `Plant canaries in your AI agent, lock a reward in ${paidIn()}, and pay only for attacks that provably work. This invite lets you list agents, read every break of them, and hunt as well.`
          : `Break AI agents, trip their canaries, and get paid automatically in ${paidIn()}. This invite opens the bounties to you.`}
      </p>
      <p className="invite-code">
        <code>{invite.data.code}</code>
        <span>{available ? "Good for one person" : "Already used"}</span>
      </p>

      {!available && !has ? (
        <p className="notice">
          This invite has already been used. Each one works once: ask whoever sent it for a fresh one.
        </p>
      ) : signedIn && has ? (
        <>
          <p className="done">{sponsoring ? "You can already list agents." : "You already have access to the bounties."}</p>
          <Link className="button accent" to={sponsoring ? "/dashboard" : "/#bounties"}>
            {sponsoring ? "Go to your dashboard" : "See the live bounties"} <Icon name="chevron" size={15} />
          </Link>
        </>
      ) : signedIn ? (
        <>
          <ErrorNote>{accepting.error}</ErrorNote>
          <button type="button" className="accent big" disabled={accepting.busy} onClick={() => void accept()}>
            {accepting.busy ? "One moment…" : "Accept the invite"}
          </button>
        </>
      ) : (
        <div className="invite-actions">
          {providers?.google && (
            <a className="button google" href={start}>
              Continue with Google
            </a>
          )}
          <Link className="button secondary" to={`/login?code=${encodeURIComponent(invite.data.code)}`}>
            {providers?.google ? "Use an email or an API key" : "Sign in or create an account"}
          </Link>
          <small className="muted">The invite is applied as you sign in. Nothing else to type.</small>
        </div>
      )}
    </div>
  );
}
