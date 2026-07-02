import { useEffect, useRef, useState } from "react";
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

const DISCARD_PROMPT = "Discard this form? Your entries haven't been submitted.";

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
 *
 * R3 — minimal history integration (deliberately NOT a router, deferred item #9): every
 * top-level view change pushes a `{ view }` history entry via navigate(); popstate restores
 * the popped view (closing an open form view in the process), so the phone back button walks
 * the in-app view stack instead of exiting the site. A dirty form (FormFillPage reports
 * unsaved input up through onDirtyChange → formDirtyRef) gets a confirm before the popped
 * discard; declining re-pushes the fill entry. R3 — returnTo: openForm captures the view it
 * fired from (no caller changes needed), and FormFillPage gets a { label, onReturn } so its
 * Submitted screen and pre-submit back land on the origin (My Tasks), not Home.
 */
export function App() {
  const { user, loading } = useAuth();
  const [view, setView] = useState<View>("home");
  // A dirty editor (Accounts/Forms) is open — drives the admin keep-alive (see AdminSessionGuard).
  const [editing, setEditing] = useState(false);
  // Deep-link prefill for FormFillPage (P4 S4): set when a form_linked/inspection checklist item is
  // opened from My Tasks, cleared on navigating home. Passed through to FormFillPage as initial state.
  const [formPrefill, setFormPrefill] = useState<FormPrefill | null>(null);
  // R3: the view a form deep-link fired FROM (captured inside openForm — callers pass nothing).
  // null = the fill wasn't deep-linked (home-card flow) → FormFillPage keeps its default screens.
  const [returnTo, setReturnTo] = useState<View | null>(null);
  // Synchronous mirror of `view` for the (stable) popstate listener + push-dedupe in navigate().
  const viewRef = useRef<View>("home");
  // R3: FormFillPage's unsaved-input flag, consulted by the popstate guard before discarding.
  const formDirtyRef = useRef(false);

  // R3 history integration: tag the initial entry, then restore views on popstate. Registered once;
  // reads state through refs so the listener never goes stale.
  useEffect(() => {
    window.history.replaceState({ view: viewRef.current }, "");
    const onPop = (ev: PopStateEvent) => {
      const s = ev.state as { view?: unknown } | null;
      const target = (s && typeof s.view === "string" ? s.view : "home") as View;
      if (viewRef.current === "fill" && formDirtyRef.current) {
        if (!window.confirm(DISCARD_PROMPT)) {
          // Stay on the form: restore the history entry the pop consumed.
          window.history.pushState({ view: "fill" }, "");
          return;
        }
        formDirtyRef.current = false;
      }
      viewRef.current = target;
      if (target !== "fill") {
        // Leaving the form view closes it: drop the deep-link state.
        setFormPrefill(null);
        setReturnTo(null);
      }
      setEditing(false);
      setView(target);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  if (loading) {
    return <div className="centered muted">Loading…</div>;
  }
  if (!user) {
    return <LoginPage />;
  }

  const caps = user.capabilities;
  const has = (c: string) => caps.includes(c);
  // R3: every top-level view change goes through here so each gets its own history entry.
  const navigate = (next: View) => {
    if (next !== viewRef.current) window.history.pushState({ view: next }, "");
    viewRef.current = next;
    setView(next);
  };
  const home = () => {
    setEditing(false);
    setFormPrefill(null);
    setReturnTo(null);
    navigate("home");
  };
  const backNav = <BackHomeNav onHome={home} />;
  // Open FormFillPage pre-filled from a checklist deep-link (P4 S4). The keyed remount (see `key`
  // below) guarantees the prefill seeds initial state even if FormFillPage was already mounted.
  // R3: capture the view this fired from — the round-trip return target (no caller changes needed).
  const openForm = (p: FormPrefill) => {
    setEditing(false);
    setFormPrefill(p);
    if (viewRef.current !== "fill") setReturnTo(viewRef.current);
    navigate("fill");
  };
  // R3: the deep-link round trip back to the captured origin view.
  const returnLabel = returnTo === "fieldops-tasks" ? "Back to My Tasks" : "Back";
  const returnToOrigin = () => {
    const dest = returnTo ?? "home";
    setEditing(false);
    setFormPrefill(null);
    setReturnTo(null);
    navigate(dest);
  };

  let page: ReactNode;
  if (view === "fill") {
    page = (
      <FormFillPage
        key={formPrefill ? `${formPrefill.jobId ?? ""}|${formPrefill.parentCode ?? ""}|${formPrefill.variantCode ?? ""}|${formPrefill.workDate ?? ""}` : "blank"}
        onBack={home}
        tabBar={backNav}
        prefill={formPrefill ?? undefined}
        returnTo={returnTo !== null ? { label: returnLabel, onReturn: returnToOrigin } : undefined}
        onDirtyChange={(d) => {
          formDirtyRef.current = d;
        }}
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
          navigate(v);
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
