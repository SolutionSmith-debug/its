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

/** R7 — Home is grouped into three headed sections (it had grown into a 10-card flat wall under
 *  a single "Daily forms" heading, A4). Section membership is presentation only: every card keeps
 *  its exact capability gate and view key. */
type HomeSectionKey = "forms" | "field" | "admin";

interface HomeCard {
  key: HomeNav;
  /** Capability required to see this card (migration 0013). null = everyone. */
  cap: string | null;
  badge: string;
  title: string;
  desc: string;
  section: HomeSectionKey;
}

const HOME_SECTIONS: { key: HomeSectionKey; heading: string }[] = [
  { key: "forms", heading: "Daily forms" },
  { key: "field", heading: "Field operations" },
  { key: "admin", heading: "Administration" },
];

// Array order = display order within each section. The form actions (Submit / Form Request) are
// IDENTICAL for every account, so an admin's home leads with the same cards as a field PM's;
// field-operations cards follow, and the management cards (capability-gated) close the page.
const HOME_CARDS: HomeCard[] = [
  {
    key: "fill",
    cap: "cap.form.submit",
    badge: "New",
    title: "Submit a form",
    desc: "Pick a job and form — safety or progress (JHA, Toolbox Talk, Equipment Pre-Inspection, Daily Report, and more).",
    section: "forms",
  },
  {
    key: "request",
    cap: "cap.form.request",
    badge: "Browse",
    title: "Form Request",
    desc: "Find a job's filed forms and download them on the spot — last week's JHAs, a crane lift plan, and more.",
    section: "forms",
  },
  {
    key: "fieldops-tasks",
    cap: "cap.tasks.own",
    badge: "Field Ops",
    // R7 (R2 finding) + D2: the card copy names the Daily report — the tab lives here too.
    title: "My Tasks",
    desc: "Your assigned tasks and inspections, plus your Daily report — grouped by job, updated as you work.",
    section: "field",
  },
  {
    key: "fieldops-jobs",
    cap: "cap.jobtracker.read",
    badge: "Field Ops",
    title: "Job Tracker",
    desc: "Jobs, crew, open tasks, and equipment on site.",
    section: "field",
  },
  {
    key: "fieldops-equipment",
    cap: "cap.equipment.field",
    badge: "Field Ops",
    title: "Equipment",
    desc: "Fleet readiness, current location, inspections, and machine logs.",
    section: "field",
  },
  {
    key: "fieldops-personnel",
    cap: "cap.personnel.read",
    badge: "Admin",
    title: "Personnel",
    desc: "Who is where, and per-person hour history.",
    section: "field",
  },
  {
    key: "materials-catalog",
    cap: "cap.materials.manage",
    badge: "Admin",
    title: "Materials Catalog",
    desc: "The datasheet-backed material type catalog — add, edit, and retire types.",
    section: "field",
  },
  {
    key: "fieldops-inspections",
    cap: "cap.checklist.manage",
    badge: "Admin",
    // R7 (Open Q4) → D2: the card is inspections-only now (the daily content lives in the
    // Daily Field Report form definition; the default-checklist editor was retired). Key unchanged.
    title: "Checklists",
    desc: "Author reusable inspection checklists and assign them to a manager or subcontractor.",
    section: "admin",
  },
  {
    key: "accounts",
    cap: "cap.admin.accounts",
    badge: "Admin",
    title: "Accounts",
    desc: "Create, edit, disable, and set roles/capabilities on portal accounts.",
    section: "admin",
  },
  {
    key: "forms",
    cap: "cap.admin.formbuilder",
    badge: "Admin",
    title: "Forms",
    desc: "Manage the form catalog and publish new versions.",
    section: "admin",
  },
];

/**
 * The unified home (P1). Every account lands here; the action cards are capability-gated
 * (migration 0013), so an admin sees the same form cards as a field PM PLUS their
 * management cards. Admin submit-as is preserved downstream: opening "Submit a form" routes
 * to FormFillPage, which still shows the "filled out as" account selector for admins.
 *
 * R7 — cards render under three headed sections (Daily forms / Field operations /
 * Administration); a section with no visible cards renders nothing, so a submitter never
 * sees an empty "Administration" heading. Gating and view keys are untouched.
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
        {HOME_SECTIONS.map((s) => {
          const visible = cards.filter((c) => c.section === s.key);
          if (visible.length === 0) return null;
          return (
            <section key={s.key} aria-label={s.heading}>
              {/* --eyebrow is ADDITIVE (design refinement 2026-07): the section header
                  renders as a letterspaced signage eyebrow; .page__heading is unrenamed. */}
              <h2 className="page__heading page__heading--eyebrow">{s.heading}</h2>
              <div className="form-grid">
                {visible.map((c) => (
                  <button key={c.key} className="form-card" onClick={() => onNavigate(c.key)}>
                    <span className="form-card__badge">{c.badge}</span>
                    <span className="form-card__title">{c.title}</span>
                    <span className="form-card__desc">{c.desc}</span>
                  </button>
                ))}
              </div>
            </section>
          );
        })}
      </main>
    </div>
  );
}
