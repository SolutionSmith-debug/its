import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import * as personnel from "../lib/fieldops_personnel";
import { InlineRowMsg, SectionLoading, SectionRefreshWarn, errMsg, type RowFeedback } from "./myTasksShared";

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
 *
 * G2.3 — scoped crew EDIT/RETIRE: crew rows the actor CREATED (created_by_me from /crew/mine —
 * the actor's own linked row shows no controls) gain Edit (inline name/trade mini-form; the typo
 * fix) and Retire (soft, confirm-gated; the typo'd-DUPLICATE escape hatch). The Worker re-gates
 * everything (created_by ownership folded into the UPDATE) and REFUSES retiring a person someone
 * else logged time on (409 crew_has_foreign_time) or one placed on a different job (409
 * crew_on_other_job) — that copy routes real workers to the office. Per-row busy + inline
 * feedback (never-silent, Mandatory B).
 */
export function AddCrewSection({
  placementJob,
  placementProject,
}: {
  /** Placement hint (job id) from the Daily tab's placement resolve (D2), when available. */
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
  // G2.3 — per-row edit/retire state (one edit open at a time; per-row busy + inline feedback).
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editTrade, setEditTrade] = useState("");
  const [rowBusy, setRowBusy] = useState<number | null>(null);
  const [rowMsgs, setRowMsgs] = useState<Record<number, RowFeedback>>({});

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

  // ── G2.3 — scoped edit/retire handlers ───────────────────────────────────────────────────────────
  function setRowMsg(id: number, m: RowFeedback | null) {
    setRowMsgs((prev) => {
      const next = { ...prev };
      if (m === null) delete next[id];
      else next[id] = m;
      return next;
    });
  }

  function startEdit(m: personnel.MyCrewMember) {
    setEditingId(m.id);
    setEditName(m.name);
    setEditTrade(m.trade ?? "");
    setRowMsg(m.id, null);
  }

  async function saveEdit(id: number) {
    const n = editName.trim();
    if (n.length < 1) {
      setRowMsg(id, { ok: false, text: "Enter a name." });
      return;
    }
    if (rowBusy !== null) return;
    setRowBusy(id);
    setRowMsg(id, null);
    try {
      await personnel.updateCrew(id, { name: n, trade: editTrade.trim() || undefined });
      setEditingId(null);
      setRowMsg(id, { ok: true, text: "Saved." });
      void loadCrew();
    } catch (err) {
      setRowMsg(id, { ok: false, text: errMsg(err, "Could not save the change.") });
    } finally {
      setRowBusy(null);
    }
  }

  async function retire(m: personnel.MyCrewMember) {
    if (rowBusy !== null) return;
    if (!window.confirm(`Retire ${m.name}? They come off the active roster (their history is kept).`)) return;
    setRowBusy(m.id);
    setRowMsg(m.id, null);
    try {
      await personnel.retireCrew(m.id);
      setRowMsg(m.id, { ok: true, text: `${m.name} retired.` });
      void loadCrew();
    } catch (err) {
      // 409 copy (crew_has_foreign_time / crew_on_other_job) routes real workers to the office.
      setRowMsg(m.id, { ok: false, text: errMsg(err, "Could not retire them.") });
    } finally {
      setRowBusy(null);
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
        <div className="dash-card__sub" aria-label="Your crew">
          Your crew:
          <ul style={{ listStyle: "none", paddingLeft: 0, margin: "0.25em 0 0" }}>
            {/* G2.3 — Edit/Retire only on members the actor CREATED (created_by_me; their own linked
                row gets none). The Worker re-gates ownership + the retire guards either way. */}
            {crew.map((m) =>
            editingId === m.id ? (
              <li key={m.id} className="dash-row">
                <input
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  aria-label={`Edit name for ${m.name}`}
                  maxLength={128}
                />{" "}
                <input
                  value={editTrade}
                  onChange={(e) => setEditTrade(e.target.value)}
                  placeholder="Trade (optional)"
                  aria-label={`Edit trade for ${m.name}`}
                  maxLength={64}
                />{" "}
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={rowBusy !== null}
                  onClick={() => void saveEdit(m.id)}
                >
                  {rowBusy === m.id ? "Saving…" : "Save"}
                </button>{" "}
                <button type="button" className="btn btn--secondary" disabled={rowBusy !== null} onClick={() => setEditingId(null)}>
                  Cancel
                </button>
                {rowMsgs[m.id] && (
                  <>
                    {" "}
                    <InlineRowMsg msg={rowMsgs[m.id]} />
                  </>
                )}
              </li>
            ) : (
              <li key={m.id} className="dash-row">
                {m.trade ? `${m.name} (${m.trade})` : m.name}
                {m.created_by_me === 1 && (
                  <>
                    {" "}
                    <button
                      type="button"
                      className="btn btn--secondary"
                      disabled={rowBusy !== null}
                      aria-label={`Edit ${m.name}`}
                      onClick={() => startEdit(m)}
                    >
                      Edit
                    </button>{" "}
                    <button
                      type="button"
                      className="btn btn--retire"
                      disabled={rowBusy !== null}
                      aria-label={`Retire ${m.name}`}
                      onClick={() => void retire(m)}
                    >
                      {rowBusy === m.id ? "Retiring…" : "Retire"}
                    </button>
                  </>
                )}
                {rowMsgs[m.id] && (
                  <>
                    {" "}
                    <InlineRowMsg msg={rowMsgs[m.id]} />
                  </>
                )}
              </li>
            ),
          )}
          </ul>
        </div>
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
