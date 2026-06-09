import { useState } from "react";
import { AdminTabs, type AdminTab } from "../components/AdminTabs";
import { FormFillPage } from "./FormFillPage";
import { AccountsPage } from "./AccountsPage";
import { FormsPage } from "./FormsPage";

/**
 * The admin (CEO / head PM) shell: a tabbed view over the submission form, account
 * management, and the form catalog manager. Submitters never reach this — App.tsx
 * routes by role, and every admin API call is re-gated server-side regardless of
 * what the SPA renders.
 *
 * "Submit a form" reuses FormFillPage (no onBack — the tab bar is the navigation).
 * "Forms" is the read-only catalog manager + live preview (Phase-2 slice 2);
 * authoring lands in later slices.
 */
export function AdminApp() {
  const [tab, setTab] = useState<AdminTab>("submit");
  const tabBar = <AdminTabs tab={tab} setTab={setTab} />;
  if (tab === "accounts") return <AccountsPage tabBar={tabBar} />;
  if (tab === "forms") return <FormsPage tabBar={tabBar} />;
  return <FormFillPage tabBar={tabBar} />;
}
