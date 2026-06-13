import { useState } from "react";
import { useAuth } from "./lib/auth";
import { LoginPage } from "./pages/LoginPage";
import { HomePage } from "./pages/HomePage";
import { FormFillPage } from "./pages/FormFillPage";
import { FormRequestPage } from "./pages/FormRequestPage";
import { AdminApp } from "./pages/AdminApp";

type View = "home" | "fill" | "request";

/**
 * Minimal in-memory view switch (login → home → form fill). Phase 4 PR 2 replaced
 * the hard-coded JHA stub with the definition-driven FormFillPage (renders any
 * catalog form from safety_portal/forms/*.json). A full client router is still
 * deferred; the SPA static-asset fallback serves index.html for any deep link.
 *
 * Admins (role from /api/session) get the tabbed AdminApp instead of the submitter
 * home→fill flow; submitters see exactly what they saw before the admin dashboard.
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
  if (user.role === "admin") {
    return <AdminApp />;
  }
  if (view === "fill") {
    return <FormFillPage onBack={() => setView("home")} />;
  }
  if (view === "request") {
    return <FormRequestPage onBack={() => setView("home")} />;
  }
  return (
    <HomePage onOpenForm={() => setView("fill")} onOpenFormRequest={() => setView("request")} />
  );
}
