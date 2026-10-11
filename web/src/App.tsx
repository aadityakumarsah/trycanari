import type { ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { BountyPage } from "./pages/BountyPage.tsx";
import { Dashboard } from "./pages/Dashboard.tsx";
import { DashboardBounty } from "./pages/DashboardBounty.tsx";
import { Home } from "./pages/Home.tsx";
import { Login } from "./pages/Login.tsx";
import { useSession } from "./session.tsx";

function Header() {
  const { session, signOut } = useSession();
  const navigate = useNavigate();
  return (
    <header className="top">
      <Link className="brand" to="/">
        AgentBounty
      </Link>
      <nav aria-label="Main">
        <Link to="/">Bounties</Link>
        {session?.signed_in ? (
          <>
            <Link to="/dashboard">Dashboard</Link>
            <button
              type="button"
              className="link"
              onClick={() => {
                void signOut().then(() => navigate("/"));
              }}
            >
              Sign out
            </button>
          </>
        ) : (
          <Link to="/login">Sponsor sign in</Link>
        )}
      </nav>
    </header>
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

export function App() {
  return (
    <>
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/b/:bountyId" element={<BountyPage />} />
          <Route path="/login" element={<Login />} />
          <Route
            path="/dashboard"
            element={
              <Private>
                <Dashboard />
              </Private>
            }
          />
          <Route
            path="/dashboard/b/:bountyId"
            element={
              <Private>
                <DashboardBounty />
              </Private>
            }
          />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </>
  );
}
