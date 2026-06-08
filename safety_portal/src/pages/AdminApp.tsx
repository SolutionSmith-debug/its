import { useState } from "react";
import { AdminTabs, type AdminTab } from "../components/AdminTabs";
import { FormFillPage } from "./FormFillPage";
import { AccountsPage } from "./AccountsPage";

/**
 * The admin (CEO / head PM) shell: a two-tab view over the existing submission form
 * and account management. Submitters never reach this — App.tsx routes by role, and
 * every admin API call is re-gated server-side regardless of what the SPA renders.
 *
 * "Submit a form" reuses FormFillPage (no onBack — the tab bar is the navigation).
 * Phase 1 leaves that tab as the normal form; the "filled out as" selector lands in
 * the submit-as slice.
 */
export function AdminApp() {
  const [tab, setTab] = useState<AdminTab>("submit");
  const tabBar = <AdminTabs tab={tab} setTab={setTab} />;
  return tab === "submit" ? <FormFillPage tabBar={tabBar} /> : <AccountsPage tabBar={tabBar} />;
}
