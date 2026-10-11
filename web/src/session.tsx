import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "./api.ts";
import { forgetLoaded } from "./hooks.ts";
import type { Session } from "./types.ts";

interface SessionContext {
  /** null while the first check is in flight. */
  session: Session | null;
  signIn: (apiKey: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const Context = createContext<SessionContext | null>(null);

const SIGNED_OUT: Session = { signed_in: false, email: null };

/** Knows whether this browser is signed in. The cookie itself is not readable by script. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    api<Session>("GET", "/v1/session")
      .then(setSession)
      .catch(() => setSession(SIGNED_OUT));
  }, []);

  const signIn = useCallback(async (apiKey: string) => {
    setSession(await api<Session>("POST", "/v1/session", { body: { api_key: apiKey } }));
  }, []);
  const signOut = useCallback(async () => {
    await api<Session>("DELETE", "/v1/session").catch(() => undefined);
    forgetLoaded();
    setSession(SIGNED_OUT);
  }, []);

  const value = useMemo(() => ({ session, signIn, signOut }), [session, signIn, signOut]);
  return <Context value={value}>{children}</Context>;
}

export function useSession(): SessionContext {
  const context = useContext(Context);
  if (!context) throw new Error("useSession needs a SessionProvider above it");
  return context;
}
