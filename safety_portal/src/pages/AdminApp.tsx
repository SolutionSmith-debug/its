import { useState } from "react";
import { AdminTabs, type AdminTab } from "../components/AdminTabs";
import { FormFillPage } from "./FormFillPage";
import { AccountsPage } from "./AccountsPage";
import { FormsPage } from "./FormsPage";
import { useAuth } from "../lib/auth";
import { useIdleLogout } from "../lib/useIdleLogout";

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
  const { logout } = useAuth();
  const [editing, setEditing] = useState(false);
  // Admin 30-minute idle timeout (slice 8b, C10): proactive logout + keep-alive ping while
  // active. While an editor holds unsaved work (`editing`, reported up by FormsPage/AccountsPage),
  // the hook adds a bounded wall-clock keep-alive so a dirty draft in a briefly-backgrounded tab
  // isn't bounced mid-edit (an abandoned editor still idles out at 30 min). Admin-scoped by
  // construction (App.tsx only routes admins here); the server-side sliding window in
  // requireSession is the real boundary.
  useIdleLogout(logout, editing);
  const [tab, setTab] = useState<AdminTab>("submit");
  const tabBar = <AdminTabs tab={tab} setTab={setTab} />;
  if (tab === "accounts") return <AccountsPage tabBar={tabBar} onEditingChange={setEditing} />;
  if (tab === "forms") return <FormsPage tabBar={tabBar} onEditingChange={setEditing} />;
  return <FormFillPage tabBar={tabBar} />;
}
