import { useAuth } from "../lib/auth";
import { AppHeader } from "../components/AppHeader";

export function HomePage({
  onOpenForm,
  onOpenFormRequest,
}: {
  onOpenForm: () => void;
  onOpenFormRequest: () => void;
}) {
  const { user, logout } = useAuth();
  return (
    <div className="page">
      <AppHeader
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
          <button className="form-card" onClick={onOpenFormRequest}>
            <span className="form-card__badge">Browse</span>
            <span className="form-card__title">Form Request</span>
            <span className="form-card__desc">
              Find a job's filed safety forms and download them on the spot — last week's
              JHAs, a crane lift plan, and more.
            </span>
          </button>
        </div>
      </main>
    </div>
  );
}
