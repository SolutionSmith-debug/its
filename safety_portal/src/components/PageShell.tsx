import type { ReactNode } from "react";
import { AppHeader } from "./AppHeader";
import { useAuth } from "../lib/auth";

/**
 * The singular page shell. Every authenticated page renders through this so the
 * Evergreen header (with Sign out) and the back/home control land in the SAME place
 * on every page — fixing the field-ops pages that previously rendered no <AppHeader>
 * and invented their own back control. `onHome` adds the "← Home" nav strip (omit it
 * on the Home page itself). Page content goes in the established `.page__main` well
 * (max-width, centered) like the other pages.
 */
export function PageShell({ onHome, children }: { onHome?: () => void; children: ReactNode }) {
  const { logout } = useAuth();
  return (
    <div className="page">
      <AppHeader
        action={
          <button className="btn btn--ghost" onClick={() => void logout()}>
            Sign out
          </button>
        }
      />
      {onHome && (
        <nav className="admin-tabs" aria-label="Navigation">
          <button type="button" className="admin-tabs__tab" onClick={onHome}>
            ← Home
          </button>
        </nav>
      )}
      <main className="page__main">{children}</main>
    </div>
  );
}
