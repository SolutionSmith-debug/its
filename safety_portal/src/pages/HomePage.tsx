import { useAuth } from "../lib/auth";
import { AppHeader } from "../components/AppHeader";

export function HomePage({ onOpenForm }: { onOpenForm: () => void }) {
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
          <button className="form-card" onClick={onOpenForm}>
            <span className="form-card__badge">New</span>
            <span className="form-card__title">Submit a safety form</span>
            <span className="form-card__desc">
              Pick a job and form — JHA, Toolbox Talk, Equipment Pre-Inspection, Visitor
              Sign-In, or HSS&amp;E Work Observation.
            </span>
          </button>
        </div>
      </main>
    </div>
  );
}
