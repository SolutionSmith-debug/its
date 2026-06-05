import { useAuth } from "../lib/auth";
import { AppHeader } from "../components/AppHeader";

export function HomePage({ onOpenJha }: { onOpenJha: () => void }) {
  const { user, logout } = useAuth();
  return (
    <div className="page">
      <AppHeader
        title="Safety Portal"
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
        <h1 className="page__heading">Daily safety forms</h1>
        <div className="form-grid">
          <button className="form-card" onClick={onOpenJha}>
            <span className="form-card__badge">JHA</span>
            <span className="form-card__title">Job Hazard Analysis</span>
            <span className="form-card__desc">
              Tasks, major hazards, mitigations, and crew sign-off.
            </span>
          </button>
          <div className="form-card form-card--disabled" aria-disabled="true">
            <span className="form-card__badge form-card__badge--muted">Soon</span>
            <span className="form-card__title">More forms</span>
            <span className="form-card__desc">
              Toolbox Talks, Equipment Pre-Inspection, and others arrive in Phase 4.
            </span>
          </div>
        </div>
      </main>
    </div>
  );
}
