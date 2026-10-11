import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";

import { api } from "../api.ts";
import { ErrorNote } from "../components/ui.tsx";
import { useAction } from "../hooks.ts";
import { useSession } from "../session.tsx";

interface Created {
  sponsor_id: string;
  api_key: string;
}

export function Login() {
  const { session, signIn } = useSession();
  const location = useLocation();
  const [key, setKey] = useState("");
  const [email, setEmail] = useState("");
  const [issued, setIssued] = useState("");
  const signingIn = useAction();
  const signingUp = useAction();

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
          <Link className="button" to="/dashboard">
            Continue to the dashboard
          </Link>
        </p>
      </>
    );
  }
  if (session?.signed_in) {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from ?? "/dashboard"} replace />;
  }

  async function submitKey(event: FormEvent) {
    event.preventDefault();
    await signingIn.run(() => signIn(key.trim()));
  }

  async function submitEmail(event: FormEvent) {
    event.preventDefault();
    await signingUp.run(async () => {
      const created = await api<Created>("POST", "/v1/sponsors", { body: { email: email.trim() } });
      setIssued(created.api_key);
      await signIn(created.api_key);
    });
  }

  return (
    <>
      <h1>Sponsor sign in</h1>
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
