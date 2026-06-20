import type { ReactNode } from "react";

interface AppHeaderProps {
  /** Optional right-aligned action (e.g. a Sign out button). */
  action?: ReactNode;
}

/**
 * Brand header (maximalist). The deep-green field carries a FIXED brand lockup —
 * so there is no `title` prop. Order: the Evergreen mark on a gold-bordered white
 * plate (Evergreen's brand green differs from BRG and would clash directly on the
 * green field), then the gold-gradient "Integrated Technical System" wordmark.
 *
 * The wordmark is LIVE TEXT (not a baked PNG): a logotype set in the self-hosted
 * "Great Vibes" roundhand — the same formal gold-script the retired ITS-crest +
 * "Portal" lockup used — with the gold rendered as a gradient text-fill in CSS
 * (.app-header__wordmark). Live text makes it selectable, screen-reader-readable,
 * responsive (wraps/scales on field phones), and recolourable without a re-render.
 * As a brand logotype the gold fill is WCAG-1.4.3-exempt (same as the old PNG).
 * Gold is otherwise decorative-only, never UI text. Each page renders its own
 * <AppHeader>; pages self-identify via in-page headings (no shared layout shell).
 */
export function AppHeader({ action }: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__brand">
        <span className="app-header__logo-plate">
          <img src="/evergreen-logo.svg" alt="Evergreen Renewables" className="app-header__logo" />
        </span>
        <span className="app-header__wordmark">Integrated Technical System</span>
      </div>
      {action ? <div className="app-header__action">{action}</div> : null}
    </header>
  );
}
