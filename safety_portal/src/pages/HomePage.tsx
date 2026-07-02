import { useAuth } from "../lib/auth";
import { AppHeader } from "../components/AppHeader";

/** Home navigation targets — the views a card can open. Kept in sync with App's view switch. */
export type HomeNav =
  | "fill"
  | "request"
  | "accounts"
  | "forms"
  | "fieldops-jobs"
  | "fieldops-tasks"
  | "fieldops-inspections"
  | "fieldops-equipment"
  | "fieldops-personnel"
  | "materials-catalog";

interface HomeCard {
  key: HomeNav;
  /** Capability required to see this card (migration 0013). null = everyone. */
  cap: string | null;
  badge: string;
  title: string;
  desc: string;
}

// The form actions (Submit / Form Request) come first and are IDENTICAL for every
// account, so an admin's home reads the same as a field PM's. Admin-only management
// cards (Accounts / Forms) append below, capability-gated. Job Tracker / Equipment /
// Personnel / Materials cards land here as their phases ship.
const HOME_CARDS: HomeCard[] = [
  {
    key: "fill",
    cap: "cap.form.submit",
    badge: "New",
    title: "Submit a form",
    desc: "Pick a job and form — safety or progress (JHA, Toolbox Talk, Equipment Pre-Inspection, Daily Report, and more).",
  },
  {
    key: "request",
    cap: "cap.form.request",
    badge: "Browse",
    title: "Form Request",
    desc: "Find a job's filed forms and download them on the spot — last week's JHAs, a crane lift plan, and more.",
  },
  {
    key: "accounts",
    cap: "cap.admin.accounts",
    badge: "Admin",
    title: "Accounts",
    desc: "Create, edit, disable, and set roles/capabilities on portal accounts.",
  },
  {
    key: "forms",
    cap: "cap.admin.formbuilder",
    badge: "Admin",
    title: "Forms",
    desc: "Manage the form catalog and publish new versions.",
  },
  {
    key: "fieldops-jobs",
    cap: "cap.jobtracker.read",
    badge: "Field Ops",
    title: "Job Tracker",
    desc: "Jobs, crew, open tasks, and equipment on site.",
  },
  {
    key: "fieldops-tasks",
    cap: "cap.tasks.own",
    badge: "Field Ops",
    title: "My Tasks",
    desc: "The tasks assigned to you, grouped by job — update each as you work it.",
  },
  {
    key: "fieldops-inspections",
    cap: "cap.checklist.manage",
    badge: "Admin",
    title: "Inspection checklists",
    desc: "Author reusable inspection checklists and assign them to a manager or subcontractor.",
  },
  {
    key: "fieldops-equipment",
    cap: "cap.equipment.field",
    badge: "Field Ops",
    title: "Equipment",
    desc: "Fleet readiness, current location, inspections, and machine logs.",
  },
  {
    key: "fieldops-personnel",
    cap: "cap.personnel.read",
    badge: "Admin",
    title: "Personnel",
    desc: "Who is where, and per-person hour history.",
  },
  {
    key: "materials-catalog",
    cap: "cap.materials.manage",
    badge: "Admin",
    title: "Materials Catalog",
    desc: "The datasheet-backed material type catalog — add, edit, and retire types.",
  },
];

/**
 * The unified home (P1). Every account lands here; the action cards are capability-gated
 * (migration 0013), so an admin sees the same form cards as a field PM PLUS their
 * management cards. Admin submit-as is preserved downstream: opening "Submit a form" routes
 * to FormFillPage, which still shows the "filled out as" account selector for admins.
 */
export function HomePage({ onNavigate }: { onNavigate: (v: HomeNav) => void }) {
  const { user, logout } = useAuth();
  const caps = user?.capabilities ?? [];
  const cards = HOME_CARDS.filter((c) => c.cap === null || caps.includes(c.cap));
  return (
    <div className="page">
      <AppHeader
        action={
          <button className="btn btn--ghost" onClick={() => void logout()}>
            Sign out
          </button>
        }
      />
      <main className="page__main">
        <p className="welcome">
          Signed in as <strong>{user?.username}</strong>
        </p>
        <h1 className="page__heading">Daily forms</h1>
        <div className="form-grid">
          {cards.map((c) => (
            <button key={c.key} className="form-card" onClick={() => onNavigate(c.key)}>
              <span className="form-card__badge">{c.badge}</span>
              <span className="form-card__title">{c.title}</span>
              <span className="form-card__desc">{c.desc}</span>
            </button>
          ))}
        </div>
      </main>
    </div>
  );
}
