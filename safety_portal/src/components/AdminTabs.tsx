export type AdminTab = "submit" | "accounts" | "forms";

/**
 * Admin section switcher, rendered just below the header on every admin page. The
 * tabs are a convenience for the two admins (CEO + head PM) — they are NOT a
 * security control: each admin API route re-checks the session role server-side.
 */
export function AdminTabs({ tab, setTab }: { tab: AdminTab; setTab: (t: AdminTab) => void }) {
  const item = (key: AdminTab, label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === key}
      className={`admin-tabs__tab${tab === key ? " admin-tabs__tab--active" : ""}`}
      onClick={() => setTab(key)}
    >
      {label}
    </button>
  );
  return (
    <nav className="admin-tabs" role="tablist" aria-label="Admin sections">
      {item("submit", "Submit a form")}
      {item("accounts", "Accounts")}
      {item("forms", "Forms")}
    </nav>
  );
}
