/**
 * Canonical back/home control — the banner-extension nav strip (`.admin-tabs`) that sits
 * directly under <AppHeader>, matching the admin Accounts / Forms / Job-Tracker nav style.
 *
 * Single source of truth (design language: the back/home control is canonical + universal):
 * PageShell, App's `backNav`, and the standalone Submit-a-Form / Form-Request pages all
 * render THIS, so every page's "← Home" is byte-identical. Reuses the existing
 * `.admin-tabs__tab` style — no new CSS.
 */
export function BackHomeNav({ onHome }: { onHome: () => void }) {
  return (
    <nav className="admin-tabs" aria-label="Navigation">
      <button type="button" className="admin-tabs__tab" onClick={onHome}>
        ← Home
      </button>
    </nav>
  );
}
