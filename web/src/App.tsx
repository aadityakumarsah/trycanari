import { useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { messageOf } from "./api.ts";
import { Icon, Mark } from "./components/Brand.tsx";
import { ErrorNote } from "./components/ui.tsx";
import { BountyPage } from "./pages/BountyPage.tsx";
import { Dashboard } from "./pages/Dashboard.tsx";
import { DashboardBounty } from "./pages/DashboardBounty.tsx";
import { Home } from "./pages/Home.tsx";
import { Login } from "./pages/Login.tsx";
import { useSession } from "./session.tsx";

type Mode = "human" | "agent";

const SECTIONS: [string, string][] = [
  ["Bounties", "/#bounties"],
  ["How it works", "/#how"],
  ["Sponsors", "/#sponsors"],
  ["FAQ", "/#faq"],
];

function Header({ onNavigate }: { onNavigate: () => void }) {
  const { session, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [menu, setMenu] = useState(false);
  const [product, setProduct] = useState(false);

  // Any navigation closes whatever was open.
  useEffect(() => {
    setMenu(false);
    setProduct(false);
  }, [location]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenu(false);
        setProduct(false);
      }
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, []);

  return (
    <header className="nav">
      <Link className="brand" to="/" onClick={onNavigate} aria-label="AgentBounty, home">
        <Mark />
        <span>AgentBounty</span>
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
                <strong>For hunters</strong>
                <span>Attack live agents and get paid</span>
              </Link>
              <Link to="/dashboard" onClick={onNavigate}>
                <strong>For sponsors</strong>
                <span>List an agent and lock a reward</span>
              </Link>
              <a href="/agent.md">
                <strong>For agents</strong>
                <span>The whole platform as one markdown file</span>
              </a>
              <a href="/docs">
                <strong>API reference</strong>
                <span>Every endpoint, with schemas</span>
              </a>
            </div>
          )}
        </div>
        {SECTIONS.map(([label, to]) => (
          <Link key={to} className="nav-link" to={to} onClick={onNavigate}>
            {label}
          </Link>
        ))}
        <a className="nav-link" href="/docs">
          API
        </a>
      </nav>
      <div className="nav-right">
        <a className="nav-link nav-file" href="/agent.md">
          <Icon name="doc" size={18} /> agent.md
        </a>
        <span className="account">
          {session?.signed_in ? (
            <>
              <button
                type="button"
                className="btn-nav"
                onClick={() => {
                  onNavigate();
                  void signOut().then(() => navigate("/"));
                }}
              >
                Sign out
              </button>
              <Link className="btn-nav mint" to="/dashboard" onClick={onNavigate}>
                Dashboard
              </Link>
            </>
          ) : (
            <>
              <Link className="btn-nav" to="/login" onClick={onNavigate}>
                Login
              </Link>
              <Link className="btn-nav mint" to="/login" onClick={onNavigate}>
                Sign Up
              </Link>
            </>
          )}
        </span>
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
      <Link className="brand" to="/" onClick={onNavigate}>
        <Mark size={20} />
        <span>AgentBounty</span>
      </Link>
      <nav aria-label="Footer">
        {SECTIONS.map(([label, to]) => (
          <Link key={to} to={to} onClick={onNavigate}>
            {label}
          </Link>
        ))}
        <a href="/docs">API reference</a>
        <a href="/agent.md">agent.md</a>
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
  if (session === null) return <p className="muted">Loading…</p>;
  if (!session.signed_in) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}

function Page({ children }: { children: ReactNode }) {
  return <div className="page">{children}</div>;
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
            <Route
              path="/b/:bountyId"
              element={
                <Page>
                  <BountyPage />
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
                <Page>
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
