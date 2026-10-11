import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";

import { api } from "../api.ts";
import { ErrorNote } from "../components/ui.tsx";
import { rememberReferral, useProviders } from "../account.tsx";
import { useAction } from "../hooks.ts";
import { useSession } from "../session.tsx";

interface Created {
  sponsor_id: string;
  api_key: string;
}

/** Why Google sign-in did not work, for each reason the server gives in ?error=. */
export const GOOGLE_ERRORS: Record<string, string> = {
  google_denied: "Google sign-in was cancelled.",
  google_state: "That sign-in had expired or was started in another browser. Please try again.",
  google_failed: "Google sign-in did not complete. Please try again.",
  google_unverified: "That Google account has no verified email address.",
  google_email_taken:
    "An account with that email already exists. Sign in with its API key instead.",
};

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.500c3.540 0 6.710 1.220 9.210 3.600l6.850-6.850C35.900 2.380 30.470 0 24 0 14.620 0 6.510 5.380 2.560 13.220l7.980 6.190C12.430 13.720 17.740 9.500 24 9.500z" />
      <path fill="#4285F4" d="M46.980 24.550c0-1.570-.150-3.090-.380-4.550H24v9.020h12.940c-.580 2.960-2.260 5.480-4.780 7.180l7.730 6c4.510-4.180 7.090-10.360 7.090-17.650z" />
      <path fill="#FBBC05" d="M10.530 28.590c-.480-1.450-.760-2.990-.760-4.590s.270-3.140.760-4.590l-7.980-6.190C.920 16.460 0 20.120 0 24c0 3.880.920 7.540 2.560 10.780l7.970-6.190z" />
      <path fill="#34A853" d="M24 48c6.480 0 11.930-2.130 15.890-5.810l-7.730-6c-2.150 1.450-4.920 2.300-8.160 2.300-6.260 0-11.570-4.220-13.470-9.910l-7.980 6.190C6.510 42.620 14.620 48 24 48z" />
    </svg>
  );
}

export function Login() {
  const { session, signIn } = useSession();
  const location = useLocation();
  const [params] = useSearchParams();
  const providers = useProviders();
  // Someone who arrived through an invite link: the code is redeemed for them as they join.
  const code = (params.get("code") ?? "").trim();
  useEffect(() => {
    if (code) rememberReferral(code);
  }, [code]);
  const refusal = GOOGLE_ERRORS[params.get("error") ?? ""];
  const [key, setKey] = useState("");
  const [email, setEmail] = useState("");
  const [issued, setIssued] = useState("");
  const signingIn = useAction();
  const signingUp = useAction();

  const from = (location.state as { from?: string } | null)?.from;
  // A new sponsor is signed in straight away, but first sees their key: it is shown once.
  if (issued) {
    return (
      <>
        <h1>Your API key</h1>
        <p className="notice">
          This is the only time the key is shown. Copy it somewhere safe: it signs you in and authenticates
          scripts.
        </p>
        <pre className="secret">{issued}</pre>
        <p>
          <Link className="button" to={from ?? "/dashboard"}>
            {from ? "Continue where you left off" : "Continue to the dashboard"}
          </Link>
        </p>
      </>
    );
  }
  if (session?.signed_in) {
    return <Navigate to={from ?? "/dashboard"} replace />;
  }

  async function submitKey(event: FormEvent) {
    event.preventDefault();
    await signingIn.run(() => signIn(key.trim()));
  }

  async function submitEmail(event: FormEvent) {
    event.preventDefault();
    await signingUp.run(async () => {
      const body = { email: email.trim(), ...(code ? { referral_code: code } : {}) };
      const created = await api<Created>("POST", "/v1/sponsors", { body });
      setIssued(created.api_key);
      await signIn(created.api_key);
    });
  }

  return (
    <>
      <h1>Sign in</h1>
      {refusal && <p className="error">{refusal}</p>}
      {providers?.google && (
        <div className="sso">
          {/* A plain link: the server sends the browser on to Google. */}
          <a
            className="button google"
            href={`/v1/auth/google/start${code ? `?code=${encodeURIComponent(code)}` : ""}`}
          >
            <GoogleMark />
            Continue with Google
          </a>
          <p className="or">
            <span>or use an API key</span>
          </p>
        </div>
      )}
      <div className="columns">
        <section>
          <h2>I have an API key</h2>
          <form onSubmit={(event) => void submitKey(event)}>
            <label>
              API key
              <input
                type="password"
                required
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
            <ErrorNote>{signingIn.error}</ErrorNote>
            <button disabled={signingIn.busy}>Sign in</button>
          </form>
        </section>
        <section>
          <h2>I am new here</h2>
          <form onSubmit={(event) => void submitEmail(event)}>
            <label>
              Email
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <ErrorNote>{signingUp.error}</ErrorNote>
            <button disabled={signingUp.busy}>Create an account</button>
          </form>
          <p className="muted">You get an API key, shown once. It signs you in here and authenticates scripts.</p>
        </section>
      </div>
    </>
  );
}
