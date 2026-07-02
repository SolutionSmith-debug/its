import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import * as personnel from "../lib/fieldops_personnel";
import { SectionLoading, SectionRefreshWarn, errMsg } from "./myTasksShared";

/**
 * Slice T — "Add crew" for a SUBCONTRACTOR (cap.crew.create; extracted from FieldOpsMyTasks in R2).
 * Creates a NON-LOGIN roster person auto-placed on the subcontractor's OWN current job
 * (POST /api/fieldops/crew). The Worker resolves the job from the actor's placement and refuses
 * (422 not_placed) if the subcontractor isn't placed. The cap gate is a CONVENIENCE; the Worker
 * re-gates + enforces the non-login + auto-place rules (Invariant 2).
 *
 * R2 refinements:
 *   • Collapsed <details> disclosure on the Assigned-tasks tab (secondary action, out of the way).
 *   • Shows the actor's CURRENT PLACEMENT before submit ("You're placed on <job> — new crew will be
 *     placed there too") — placement precondition surfaced BEFORE the 422, not only after.
 *   • Fetches + shows the current crew list (fetchMyCrew) and refreshes it after each create; the
 *     crew fetch has its own loading / error+Retry states (Mandatory B) without blocking the form.
 *   • Client-side duplicate-name warn against the fetched crew list (warn, not block — the office
 *     resolves true duplicates).
 */
export function AddCrewSection({
  placementJob,
  placementProject,
}: {
  /** Placement hint from the actor's own /checklist/mine (job id), when available. */
  placementJob?: string | null;
  /** Resolved project name for the placement hint, when available. */
  placementProject?: string | null;
}) {
  const [name, setName] = useState("");
  const [trade, setTrade] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [crew, setCrew] = useState<personnel.MyCrewMember[] | null>(null);
  const [crewLoading, setCrewLoading] = useState(true);
  const [crewError, setCrewError] = useState<string | null>(null);

  async function loadCrew() {
    setCrewLoading(true);
    setCrewError(null);
    try {
      setCrew((await personnel.fetchMyCrew()) ?? []);
    } catch (err) {
      // Never silent (Mandatory B) — but the crew list is auxiliary (placement line + duplicate
      // warn), so its failure warns with a Retry and the add form stays usable.
      setCrewError(errMsg(err, "Could not load your crew."));
    } finally {
      setCrewLoading(false);
    }
  }

  useEffect(() => {
    void loadCrew();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Placement, best available source: the daily-checklist hint (managers), else any crew member's
  // current_job (fetchMyCrew includes the actor's own linked personnel row, so a placed sub sees
  // their job here). The Worker remains the authority — this is a pre-submit courtesy.
  const crewJob = crew?.find((m) => m.current_job)?.current_job ?? null;
  const placedOn = placementProject ?? placementJob ?? crewJob;

  const trimmedName = name.trim();
  const duplicate =
    trimmedName.length > 0 && crew ? crew.find((m) => m.name.trim().toLowerCase() === trimmedName.toLowerCase()) : undefined;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const n = trimmedName;
    if (n.length < 1) {
      setMsg({ ok: false, text: "Enter a name." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const res = await personnel.createCrew({ name: n, trade: trade.trim() || undefined });
      setName("");
      setTrade("");
      setMsg({ ok: true, text: `Added ${n} to your crew on ${res.current_job}.` });
      // Refresh the crew list so the new person shows (and future duplicate warns see them). Its
      // own error state covers a failure — the successful create is already reported above.
      void loadCrew();
    } catch (err) {
      // R1: user copy comes from errorCopy.ts via err.message — never duplicated in pages.
      setMsg({ ok: false, text: errMsg(err, "Could not add crew.") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="card dash-section" aria-label="Add crew">
      <summary className="dash-detail__h2" style={{ cursor: "pointer" }}>
        Add crew
      </summary>
      {placedOn ? (
        <p className="dash-card__sub">
          You&apos;re placed on {placedOn} — new crew will be placed there too.
        </p>
      ) : crewLoading || crewError || crew === null ? (
        <p className="dash-card__sub">
          Add a field-only crew member — they&apos;re placed on your current job automatically.
        </p>
      ) : (
        <p className="dash-card__sub">
          You don&apos;t appear to be placed on a job yet — ask your crew lead or the office to place you before
          adding crew.
        </p>
      )}

      {crewLoading ? (
        <SectionLoading label="Loading your crew…" />
      ) : crewError ? (
        <SectionRefreshWarn message={crewError} onRetry={() => void loadCrew()} what="loading your crew" />
      ) : crew && crew.length > 0 ? (
        <p className="dash-card__sub" aria-label="Your crew">
          Your crew: {crew.map((m) => (m.trade ? `${m.name} (${m.trade})` : m.name)).join(", ")}
        </p>
      ) : null}

      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}
      {duplicate && (
        <div className="dash-unavail" role="status">
          You already have a crew member named &quot;{duplicate.name}&quot; — adding again creates a second person.
        </div>
      )}
      <form onSubmit={submit} className="dash-row" aria-label="Add crew form">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" maxLength={128} />{" "}
        <input value={trade} onChange={(e) => setTrade(e.target.value)} placeholder="Trade (optional)" maxLength={64} />{" "}
        <button type="submit" disabled={busy} className="btn btn--primary">
          {busy ? "Adding…" : "Add crew"}
        </button>
      </form>
    </details>
  );
}
