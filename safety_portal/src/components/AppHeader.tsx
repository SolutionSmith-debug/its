import type { ReactNode } from "react";

interface AppHeaderProps {
  title: string;
  /** Optional right-aligned action (e.g. a Sign out button). */
  action?: ReactNode;
}

/**
 * BRG brand header. The Evergreen logo sits on a white plate because Evergreen's
 * brand green differs from BRG and would clash directly on the green bar (per
 * brief §7). The title is white-on-BRG (12.3:1, AA). The gold accent is the thin
 * bottom rule only — decorative, never text.
 */
export function AppHeader({ title, action }: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <span className="app-header__logo-plate">
          <img src="/evergreen-logo.svg" alt="Evergreen Renewables" className="app-header__logo" />
        </span>
        <span className="app-header__title">{title}</span>
      </div>
      {action ? <div className="app-header__action">{action}</div> : null}
    </header>
  );
}
