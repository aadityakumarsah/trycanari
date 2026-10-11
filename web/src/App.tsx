import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { useAccount } from "./account.tsx";
import { messageOf } from "./api.ts";
import { Icon, Wordmark } from "./components/Brand.tsx";
import { ErrorNote, Skeleton } from "./components/ui.tsx";
import { BountyPage } from "./pages/BountyPage.tsx";
import { Dashboard } from "./pages/Dashboard.tsx";
import { Docs } from "./pages/Docs.tsx";
import { DashboardBounty } from "./pages/DashboardBounty.tsx";
import { Home } from "./pages/Home.tsx";
import { Login } from "./pages/Login.tsx";
import { MyAttempts, useSavedAttempts } from "./pages/MyAttempts.tsx";
import { useSession } from "./session.tsx";

type Mode = "human" | "agent";

const REPOSITORY = "https://github.com/aadityakumarsah/trycanari";

// In-page sections of the landing page. Each is a real destination.
const SECTIONS: [string, string][] = [
  ["Bounties", "/#bounties"],
  ["How it works", "/#how"],
  ["Sponsors", "/#sponsors"],
  ["FAQ", "/#faq"],
];

/** Closes a menu on Escape or on a click anywhere outside it. */
function useDismiss(open: boolean, close: () => void, inside: string): void {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close();
    const onClick = (event: MouseEvent) => {
      if (!(event.target as Element).closest(inside)) close();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("click", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("click", onClick);
    };
  }, [open, close, inside]);
}

/** The signed-in sponsor: who they are, with the way to the dashboard and out. */
function Account({ email, onNavigate }: { email: string; onNavigate: () => void }) {
  const { signOut } = useSession();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, ".user");
  const name = email.split("@")[0] ?? email;

  return (
    <div className="user">
      <button
        type="button"
        className="user-chip"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-controls="account-menu"
        onClick={() => setOpen((shown) => !shown)}
      >
        <span className="avatar" aria-hidden="true">
          {name.slice(0, 1).toUpperCase()}
        </span>
        <span className="user-name">{name}</span>
        <Icon name="down" size={14} />
      </button>
      {open && (
        <div id="account-menu" className="drop-menu user-menu" role="menu">
          <p className="user-email">{email}</p>
          <Link
            role="menuitem"
            to="/dashboard"
            onClick={() => {
              onNavigate();
              close();
            }}
          >
            <Icon name="grid" /> Dashboard
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              onNavigate();
              close();
              void signOut().then(() => navigate("/"));
            }}
          >
            <Icon name="logout" /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function Header({ onNavigate }: { onNavigate: () => void }) {
  const { session } = useSession();
  const location = useLocation();
  const [menu, setMenu] = useState(false);
  const [product, setProduct] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const closeProduct = useCallback(() => setProduct(false), []);
  const running = useSavedAttempts().filter((attempt) => !attempt.outcome).length;
  const account = useAccount().data;
  useDismiss(product, closeProduct, ".drop");

  // Any navigation closes whatever was open.
  useEffect(() => {
    setMenu(false);
    setProduct(false);
  }, [location]);
  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 8);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);

  // The section link for where the reader is, so the bar shows it.
  const current = location.pathname === "/" ? `/${location.hash}` : location.pathname;
  const link = (to: string) => (current === to ? "nav-link current" : "nav-link");

  return (
    <header className={scrolled ? "nav scrolled" : "nav"}>
      <Link className="brand" to="/" onClick={onNavigate} aria-label="trycanari, home">
        <Wordmark />
      </Link>
      <nav id="menu" className={menu ? "nav-links open" : "nav-links"} aria-label="Main">
        <div className="drop">
          <button
            type="button"
            className="nav-link"
            aria-expanded={product}
            aria-controls="product-menu"
            onClick={() => setProduct((open) => !open)}
          >
            Product <Icon name="down" />
          </button>
          {product && (
            <div id="product-menu" className="drop-menu">
              <Link to="/#bounties" onClick={onNavigate}>
                <Icon name="target" size={18} />
                <span>
                  <strong>For hunters</strong>
                  <small>Attack live agents and get paid</small>
                </span>
              </Link>
              <Link to="/dashboard" onClick={onNavigate}>
                <Icon name="lock" size={18} />
                <span>
                  <strong>For sponsors</strong>
                  <small>List an agent and lock a reward</small>
                </span>
              </Link>
              <a href="/agent.md">
                <Icon name="doc" size={18} />
                <span>
                  <strong>For agents</strong>
                  <small>The whole platform as one markdown file</small>
                </span>
              </a>
              <Link to="/docs#api" onClick={onNavigate}>
                <Icon name="box" size={18} />
                <span>
                  <strong>API reference</strong>
                  <small>Every endpoint, with examples</small>
                </span>
              </Link>
            </div>
          )}
        </div>
        {SECTIONS.map(([label, to]) => (
          <Link key={to} className={link(to)} to={to} onClick={onNavigate}>
            {label}
          </Link>
        ))}
        <Link className={link("/docs")} to="/docs" onClick={onNavigate}>
          Docs
        </Link>
        <Link className={link("/attempts")} to="/attempts" onClick={onNavigate}>
          My attempts
          {running > 0 && <span className="nav-count">{running}</span>}
        </Link>
      </nav>
      <div className="nav-right">
        <a className="nav-icon" href={REPOSITORY} target="_blank" rel="noreferrer" aria-label="trycanari on GitHub">
          <Icon name="github" size={20} />
          <span>GitHub</span>
        </a>
        {session?.signed_in ? (
          <>
            {account?.invite_only && (
              <Link
                className={account.credits.balance === 0 ? "credits empty" : "credits"}
                to="/dashboard"
                onClick={onNavigate}
                title={`${account.credits.balance} of ${account.credits.cap} credits left. ${account.credits.tier} tier.`}
              >
                <Icon name="zap" size={14} />
                <b>{account.credits.balance}</b>
                <span>/ {account.credits.cap}</span>
              </Link>
            )}
            <Link className="btn-nav mint" to="/dashboard" onClick={onNavigate}>
              Dashboard
            </Link>
            <Account email={session.email ?? "sponsor"} onNavigate={onNavigate} />
          </>
        ) : (
          <span className="account">
            <Link className="btn-nav" to="/login" onClick={onNavigate}>
              Login
            </Link>
            <Link className="btn-nav mint" to="/login" onClick={onNavigate}>
              Sign Up
            </Link>
          </span>
        )}
        <button
          type="button"
          className="menu-button"
          aria-label="Menu"
          aria-expanded={menu}
          aria-controls="menu"
          onClick={() => setMenu((open) => !open)}
        >
          <Icon name="menu" size={22} />
        </button>
      </div>
    </header>
  );
}

function Footer({ onNavigate }: { onNavigate: () => void }) {
  return (
    <footer className="frame footer">
      <Link className="brand" to="/" onClick={onNavigate} aria-label="trycanari, home">
        <Wordmark height={26} />
      </Link>
      <nav aria-label="Footer">
        {SECTIONS.map(([label, to]) => (
          <Link key={to} to={to} onClick={onNavigate}>
            {label}
          </Link>
        ))}
        <Link to="/docs" onClick={onNavigate}>
          Docs
        </Link>
        <a href="/api-docs">API explorer</a>
        <a href="/agent.md">agent.md</a>
        <a href={REPOSITORY} target="_blank" rel="noreferrer">
          GitHub
        </a>
      </nav>
      <p>
        Every attack runs against a sandboxed copy of the agent with mock tools. Rewards are paid in shielded
        Zcash.
      </p>
    </footer>
  );
}

/** The platform as an agent reads it: the same markdown that /agent.md serves. */
function AgentView() {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    fetch("/agent.md")
      .then((response) => (response.ok ? response.text() : Promise.reject(new Error("unavailable"))))
      .then(setText)
      .catch((caught) => setError(messageOf(caught) || "The agent view could not be loaded."));
  }, []);
  return (
    <section className="agent">
      <div className="agent-head">
        <div>
          <p className="eyebrow">Agent view</p>
          <h2>agent.md</h2>
        </div>
        <div className="agent-actions">
          <button
            type="button"
            className="btn-nav"
            onClick={() => {
              void navigator.clipboard.writeText(text).then(() => {
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              });
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <a className="btn-nav" href="/agent.md">
            Open raw
          </a>
        </div>
      </div>
      <p className="lede">
        The same platform as plain markdown, with the bounties that are live right now. An agent can fetch it in
        one request.
      </p>
      <ErrorNote>{error}</ErrorNote>
      {text && <pre>{text}</pre>}
    </section>
  );
}

/** Sends a visitor who is not signed in to the sign-in page, and back afterwards. */
function Private({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const location = useLocation();
  if (session === null) return <Skeleton block lines={3} />;
  if (!session.signed_in) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

function Page({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return <div className={wide ? "page wide" : "page"}>{children}</div>;
}

export function NotFound({ message = "That page does not exist." }: { message?: string }) {
  return (
    <>
      <h1>Not found</h1>
      <p>{message}</p>
      <p>
        <Link to="/">Back to the bounties</Link>
      </p>
    </>
  );
}

function ToTop() {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const update = () => setShown(window.scrollY > 600);
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);
  if (!shown) return null;
  return (
    <button type="button" className="to-top" aria-label="Back to top" onClick={() => window.scrollTo({ top: 0 })}>
      <Icon name="up" size={18} />
    </button>
  );
}

export function App() {
  const [mode, setMode] = useState<Mode>("human");
  // Following any link in the page chrome goes back to the pages people read.
  const human = () => setMode("human");

  return (
    <>
      <Header onNavigate={human} />
      <main className="frame">
        {mode === "agent" ? (
          <AgentView />
        ) : (
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/docs" element={<Docs />} />
            <Route
              path="/b/:bountyId"
              element={
                <Page wide>
                  <BountyPage />
                </Page>
              }
            />
            <Route
              path="/attempts"
              element={
                <Page>
                  <MyAttempts />
                </Page>
              }
            />
            <Route
              path="/login"
              element={
                <Page>
                  <Login />
                </Page>
              }
            />
            <Route
              path="/dashboard"
              element={
                <Page>
                  <Private>
                    <Dashboard />
                  </Private>
                </Page>
              }
            />
            <Route
              path="/dashboard/b/:bountyId"
              element={
                <Page wide>
                  <Private>
                    <DashboardBounty />
                  </Private>
                </Page>
              }
            />
            <Route
              path="*"
              element={
                <Page>
                  <NotFound />
                </Page>
              }
            />
          </Routes>
        )}
      </main>
      <Footer onNavigate={human} />
      <div className="mode" role="group" aria-label="Who is reading">
        {(["human", "agent"] as const).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={mode === option}
            onClick={() => {
              setMode(option);
              window.scrollTo({ top: 0 });
            }}
          >
            {option}
          </button>
        ))}
      </div>
      <ToTop />
    </>
  );
}
