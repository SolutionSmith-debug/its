import { useState } from "react";
import type { ReactNode } from "react";
import { useAuth } from "./lib/auth";
import { LoginPage } from "./pages/LoginPage";
import { HomePage, type HomeNav } from "./pages/HomePage";
import { FormFillPage, type FormPrefill } from "./pages/FormFillPage";
import { FormRequestPage } from "./pages/FormRequestPage";
import { AccountsPage } from "./pages/AccountsPage";
import { FormsPage } from "./pages/FormsPage";
import { FieldOpsJobTracker } from "./pages/FieldOpsJobTracker";
import { FieldOpsMyTasks } from "./pages/FieldOpsMyTasks";
import { FieldOpsInspections } from "./pages/FieldOpsInspections";
import { FieldOpsEquipment } from "./pages/FieldOpsEquipment";
import { FieldOpsPersonnel } from "./pages/FieldOpsPersonnel";
import { MaterialsCatalogPage } from "./pages/MaterialsCatalogPage";
import { BackHomeNav } from "./components/BackHomeNav";
import { useIdleLogout } from "./lib/useIdleLogout";

type View = "home" | HomeNav;

/**
 * Admin-scoped session guard. Mounts the 30-min idle-logout + keep-alive (useIdleLogout)
 * ONLY for admins — rendered conditionally so the hook never runs for a submitter (who
 * keeps the 90-day session). `editing` (a dirty editor draft is open, reported up by
 * Accounts/Forms) adds the bounded wall-clock keep-alive. Renders nothing.
 */
function AdminSessionGuard({ editing }: { editing: boolean }) {
  const { logout } = useAuth();
  useIdleLogout(logout, editing);
  return null;
}

/**
 * Unified shell (P1). Every account — submitter or admin — lands on the SAME HomePage;
 * the action cards and the views they open are capability-gated (migration 0013). An
 * admin sees the same form cards as a field PM PLUS their management cards (Accounts /
 * Forms), and KEEPS submit-as: "Submit a form" routes to FormFillPage, whose "filled
 * out as" account selector still renders for admins. Every action is independently
 * re-gated server-side (requireRole / requireCapability); the SPA gating is convenience,
 * never the boundary (Invariant 2). Job Tracker / Equipment / Personnel / Materials views
 * mount here as their phases ship.
 */
export function App() {
  const { user, loading } = useAuth();
  const [view, setView] = useState<View>("home");
  // A dirty editor (Accounts/Forms) is open — drives the admin keep-alive (see AdminSessionGuard).
  const [editing, setEditing] = useState(false);
  // Deep-link prefill for FormFillPage (P4 S4): set when a form_linked/inspection checklist item is
  // opened from My Tasks, cleared on navigating home. Passed through to FormFillPage as initial state.
  const [formPrefill, setFormPrefill] = useState<FormPrefill | null>(null);

  if (loading) {
    return <div className="centered muted">Loading…</div>;
  }
  if (!user) {
    return <LoginPage />;
  }

  const caps = user.capabilities;
  const has = (c: string) => caps.includes(c);
  const home = () => {
    setEditing(false);
    setFormPrefill(null);
    setView("home");
  };
  const backNav = <BackHomeNav onHome={home} />;
  // Open FormFillPage pre-filled from a checklist deep-link (P4 S4). The keyed remount (see `key`
  // below) guarantees the prefill seeds initial state even if FormFillPage was already mounted.
  const openForm = (p: FormPrefill) => {
    setEditing(false);
    setFormPrefill(p);
    setView("fill");
  };

  let page: ReactNode;
  if (view === "fill") {
    page = (
      <FormFillPage
        key={formPrefill ? `${formPrefill.jobId ?? ""}|${formPrefill.parentCode ?? ""}|${formPrefill.variantCode ?? ""}|${formPrefill.workDate ?? ""}` : "blank"}
        onBack={home}
        tabBar={backNav}
        prefill={formPrefill ?? undefined}
      />
    );
  } else if (view === "request") {
    page = <FormRequestPage onBack={home} />;
  } else if (view === "accounts" && has("cap.admin.accounts")) {
    page = <AccountsPage tabBar={backNav} onEditingChange={setEditing} />;
  } else if (view === "forms" && has("cap.admin.formbuilder")) {
    page = <FormsPage tabBar={backNav} onEditingChange={setEditing} />;
  } else if (view === "fieldops-jobs" && has("cap.jobtracker.read")) {
    page = <FieldOpsJobTracker onBack={home} />;
  } else if (view === "fieldops-tasks" && has("cap.tasks.own")) {
    page = <FieldOpsMyTasks onBack={home} onOpenForm={openForm} />;
  } else if (view === "fieldops-inspections" && has("cap.checklist.manage")) {
    page = <FieldOpsInspections onBack={home} />;
  } else if (view === "fieldops-equipment" && has("cap.equipment.field")) {
    page = <FieldOpsEquipment onBack={home} />;
  } else if (view === "fieldops-personnel" && has("cap.personnel.read")) {
    page = <FieldOpsPersonnel onBack={home} />;
  } else if (view === "materials-catalog" && has("cap.materials.manage")) {
    page = <MaterialsCatalogPage onBack={home} />;
  } else {
    page = (
      <HomePage
        onNavigate={(v) => {
          setEditing(false);
          setView(v);
        }}
      />
    );
  }

  return (
    <>
      {user.role === "admin" && <AdminSessionGuard editing={editing} />}
      {page}
    </>
  );
}
