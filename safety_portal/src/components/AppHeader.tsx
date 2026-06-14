import type { ReactNode } from "react";

interface AppHeaderProps {
  /** Optional right-aligned action (e.g. a Sign out button). */
  action?: ReactNode;
}

/**
 * ITS Portal brand header (maximalist). The deep-green field carries a FIXED
 * brand lockup — so there is no `title` prop. Order: the Evergreen mark on a
 * gold-bordered white plate (Evergreen's brand green differs from BRG and would
 * clash directly on the green field), then the ITS crest + gold-gradient
 * "Portal" wordmark shipped as a single transparent PNG (alt "ITS Portal").
 * Gold is decorative, never text. Each page renders its own <AppHeader>; pages
 * self-identify via in-page headings (no shared layout shell). See
 * cc-brief_its-portal-rebrand Phase A3/A5.
 */
export function AppHeader({ action }: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <span className="app-header__logo-plate">
          <img src="/evergreen-logo.svg" alt="Evergreen Renewables" className="app-header__logo" />
        </span>
        <img src="/its-portal-header.png" alt="ITS Portal" className="app-header__lockup" />
      </div>
      {action ? <div className="app-header__action">{action}</div> : null}
    </header>
  );
}
