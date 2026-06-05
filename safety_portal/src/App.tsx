import { useState } from "react";
import { useAuth } from "./lib/auth";
import { LoginPage } from "./pages/LoginPage";
import { HomePage } from "./pages/HomePage";
import { JhaStubPage } from "./pages/JhaStubPage";

type View = "home" | "jha";

/**
 * Minimal in-memory view switch (login → home → JHA stub). Phase 2 has a single
 * authed surface, so a full client router is deferred; the SPA static-asset
 * fallback (not_found_handling) still serves index.html for any deep link.
 */
export function App() {
  const { user, loading } = useAuth();
  const [view, setView] = useState<View>("home");

  if (loading) {
    return <div className="centered muted">Loading…</div>;
  }
  if (!user) {
    return <LoginPage />;
  }
  if (view === "jha") {
    return <JhaStubPage onBack={() => setView("home")} />;
  }
  return <HomePage onOpenJha={() => setView("jha")} />;
}
