import { paidIn, setCurrency } from "./money.ts";
import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { pictureOf, useAccount, useProviders } from "./account.tsx";
import { messageOf } from "./api.ts";
import { Icon, Wordmark } from "./components/Brand.tsx";
import { NotificationBell } from "./components/Notifications.tsx";
import { Avatar, ErrorNote, firstName, Skeleton } from "./components/ui.tsx";
import { BountyPage } from "./pages/BountyPage.tsx";
import { BreakPage } from "./pages/Break.tsx";
import { Dashboard } from "./pages/Dashboard.tsx";
import { Docs } from "./pages/Docs.tsx";
import { DashboardBounty } from "./pages/DashboardBounty.tsx";
import { Home } from "./pages/Home.tsx";
import { Login } from "./pages/Login.tsx";
import { MyAttempts, useSavedAttempts } from "./pages/MyAttempts.tsx";
import { InvitePage } from "./pages/Invite.tsx";
import { Profile } from "./pages/Profile.tsx";
import { Report } from "./pages/Report.tsx";
import { useSession } from "./session.tsx";
import { dismissSplash } from "./splash.ts";
import { useTheme } from "./theme.ts";

type Mode = "human" | "agent";

// Fetched only when an admin opens it, so the admin area's code is never sent to anyone else.
const Admin = lazy(() => import("./pages/Admin.tsx"));

/** The admin area for an admin, and "Not found" for everyone else, without loading it. */
function AdminGate() {
  const account = useAccount();
  if (!account.data) return account.error ? <NotFound /> : <Skeleton block lines={4} />;
  if (!account.data.is_admin) return <NotFound />;
  return (
    <Suspense fallback={<Skeleton block lines={4} />}>
      <Admin />
    </Suspense>
  );
}

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
  const profile = useAccount().data;
  const { signOut } = useSession();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, close, ".user");
  const name = profile?.name ?? null;

  return (
    <div className="user">
      <button
        type="button"
        className="user-chip"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-controls="account-menu"
        aria-label={name ? `Account: ${name}` : "Your account"}
        onClick={() => setOpen((shown) => !shown)}
      >
        <Avatar name={name} picture={profile ? pictureOf(profile) : null} />
        {name && <span className="user-name">{firstName(name)}</span>}
        <Icon name="down" size={14} />
      </button>
      {open && (
        <div id="account-menu" className="drop-menu user-menu" role="menu">
          <div className="user-card">
            <Avatar name={name} picture={profile ? pictureOf(profile) : null} size={44} />
            <p>
              {name && <b>{name}</b>}
              <small>{email}</small>
            </p>
          </div>
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
          <Link
            role="menuitem"
            to="/profile"
            onClick={() => {
              onNavigate();
              close();
            }}
          >
            <Icon name="user" /> Profile
          </Link>
          {profile?.is_admin && (
            <Link
              role="menuitem"
              to="/admin"
              onClick={() => {
                onNavigate();
                close();
              }}
            >
              <Icon name="shield" /> Admin
            </Link>
          )}
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

/** Switches the page between its dark and light themes. */
function ThemeSwitch() {
  const [theme, flip] = useTheme();
  const next = theme === "light" ? "dark" : "light";
  return (
    <button type="button" className="icon-btn theme-toggle" aria-label={`Switch to the ${next} theme`} title={`Switch to the ${next} theme`} onClick={flip}>
      <Icon name={theme === "light" ? "moon" : "sun"} size={19} />
    </button>
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
              <Link to="/docs#editor" onClick={onNavigate}>
                <Icon name="code" size={18} />
                <span>
                  <strong>For your editor</strong>
                  <small>Your coding agent scans your agent, over MCP</small>
                </span>
              </Link>
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
        <ThemeSwitch />
        {session?.signed_in ? (
          <>
            <NotificationBell />
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
        <a href="/skill.md">skill.md</a>
        <a href={REPOSITORY} target="_blank" rel="noreferrer">
          GitHub
        </a>
      </nav>
      <p>
        Every attack runs against a sandboxed copy of the agent with mock tools. Rewards are paid in{" "}
        {paidIn()}.
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

/** Signed-in pages: places to work, where the human / agent switch has no use. */
const WORKSPACE = ["/dashboard", "/profile", "/admin", "/attempts", "/login", "/invite", "/b"];

export function App() {
  const [mode, setMode] = useState<Mode>("human");
  const { session } = useSession();
  // What the server pays in. Set before anything below is drawn, so every amount and
  // every sentence about money is right from the first paint.
  const providers = useProviders();
  if (providers) setCurrency(providers.currency);
  // The app is ready once it knows whether someone is signed in: until then the bar
  // cannot be drawn correctly, and a signed-in page would flash its signed-out form.
  useEffect(() => {
    if (session !== null && providers !== null) dismissSplash();
  }, [session, providers]);
  // Following any link in the page chrome goes back to the pages people read.
  const human = () => setMode("human");
  const { pathname } = useLocation();
  const workspace = WORKSPACE.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  // Further down a page the switch would float over cards and buttons, so it steps
  // aside there and comes back near the top.
  const [deep, setDeep] = useState(false);
  useEffect(() => {
    const measure = () => setDeep(window.scrollY > 420);
    measure();
    window.addEventListener("scroll", measure, { passive: true });
    return () => window.removeEventListener("scroll", measure);
  }, []);

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
              path="/break/:breakId"
              element={
                <Page>
                  <BreakPage />
                </Page>
              }
            />
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
              path="/invite/:code"
              element={
                <Page>
                  <InvitePage />
                </Page>
              }
            />
            <Route
              path="/admin"
              element={
                <Page wide>
                  <Private>
                    <AdminGate />
                  </Private>
                </Page>
              }
            />
            <Route
              path="/profile"
              element={
                <Page wide>
                  <Private>
                    <Profile />
                  </Private>
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
              path="/dashboard/b/:bountyId/report"
              element={
                <Page wide>
                  <Private>
                    <Report />
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
      {/* The switch is for the public pages. On a signed-in page it would only sit on
          top of forms and buttons. */}
      {!workspace && (
        <div className={deep ? "mode tucked" : "mode"} role="group" aria-label="Who is reading">
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
      )}
      <ToTop />
    </>
  );
}
