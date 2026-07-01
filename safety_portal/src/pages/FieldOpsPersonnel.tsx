import { useState, useEffect } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_personnel";
import { fetchJobs, type Job } from "../lib/api";
import { PageShell } from "../components/PageShell";
import { PersonnelDetailView } from "./PersonnelDetailView";
import { useAuth } from "../lib/auth";

// Format helper: hours → 2dp; null/NaN → em-dash. (Date formatting lives in the detail view.)
function fmtHours(hours: number | null): string {
  if (hours == null || isNaN(hours)) return "—";
  return hours.toFixed(2);
}

/**
 * Field-Ops PERSONNEL surface — a URS-Marine-style refinement matching the just-ported Equipment
 * page: a card-grid ROSTER ("who's on the crew, who's placed where") → click a card → the
 * PersonnelDetailView sibling (full time history). App.tsx/HomePage.tsx routing + the `{ onBack }`
 * prop contract are unchanged. Every roster manage control keeps its exact capability gate and API
 * call: create (cap.personnel.manage, admin-only login sub-form on role==='admin'), edit, link /
 * unlink account, retire, and the P2.6 crew→job Assign control (cap.crew.assign). All cap gates
 * here are a CONVENIENCE — the Worker re-gates every write server-side (Invariant 2).
 */
export function FieldOpsPersonnel({ onBack }: { onBack: () => void }) {
  const [view, setView] = useState<"list" | "detail">("list");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const [personnel, setPersonnel] = useState<api.PersonnelRow[]>([]);
  const [latestEntries, setLatestEntries] = useState<Record<number, api.LatestEntry>>({});
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Manage state (cap.personnel.manage). The capability gate here is a CONVENIENCE — the Worker
  // re-gates every write route server-side (Invariant 2: never trust the client).
  const { user } = useAuth();
  const canManage = (user?.capabilities ?? []).includes("cap.personnel.manage");
  // P2.6: cap.crew.assign drives the crew→job placement control; role==='admin' gates the
  // login-account minting sub-form (the Worker 403s a non-admin there, so hide the dead control).
  // Both are CONVENIENCE gates — the Worker re-checks every write (Invariant 2).
  const canAssign = (user?.capabilities ?? []).includes("cap.crew.assign");
  const isAdmin = user?.role === "admin";
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // Create form
  const [newName, setNewName] = useState("");
  const [newTrade, setNewTrade] = useState("");
  const [withAccount, setWithAccount] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<api.AccountRole>("submitter");
  // Inline edit / link (keyed by personnel id; only one open at a time)
  const [editId, setEditId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editTrade, setEditTrade] = useState("");
  const [linkId, setLinkId] = useState<number | null>(null);
  const [linkUsername, setLinkUsername] = useState("");
  // P2.6 crew→job placement (only one assign control open at a time; jobs = the active-job dropdown)
  const [assignId, setAssignId] = useState<number | null>(null);
  const [assignJob, setAssignJob] = useState<string>("");
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    loadList();
  }, []);

  // Load the active-job set for the assign dropdown (only when the actor can assign crew).
  useEffect(() => {
    if (canAssign) fetchJobs().then(setJobs).catch(() => setJobs([]));
  }, [canAssign]);

  // Replace-style refresh after a mutation (loadList APPENDS; this resets to page 1).
  async function reloadList() {
    const data = await api.fetchPersonnelList(undefined);
    setPersonnel(data.personnel);
    const entries: Record<number, api.LatestEntry> = {};
    for (const e of data.latest_entries) entries[e.personnel_id] = e;
    setLatestEntries(entries);
    setCursor(data.next_cursor);
  }

  async function submitCreate(e: FormEvent) {
    e.preventDefault();
    if (actionBusy || newName.trim() === "") return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      const body: { name: string; trade?: string; account?: api.NewAccount } = {
        name: newName.trim(),
        trade: newTrade.trim() || undefined,
      };
      if (withAccount) {
        body.account = { username: newUsername.trim(), password: newPassword, role: newRole };
      }
      await api.createPersonnel(body);
      setNewName("");
      setNewTrade("");
      setWithAccount(false);
      setNewUsername("");
      setNewPassword("");
      setNewRole("submitter");
      await reloadList();
      setActionMsg({ ok: true, text: "Personnel added." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Add failed." });
    } finally {
      setActionBusy(false);
    }
  }

  function openEdit(p: api.PersonnelRow) {
    setLinkId(null);
    setEditId(p.id);
    setEditName(p.name);
    setEditTrade(p.trade ?? "");
  }

  async function submitEdit(e: FormEvent) {
    e.preventDefault();
    if (editId === null || actionBusy || editName.trim() === "") return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.updatePersonnel(editId, { name: editName.trim(), trade: editTrade.trim() || undefined });
      setEditId(null);
      await reloadList();
      setActionMsg({ ok: true, text: "Personnel updated." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setActionBusy(false);
    }
  }

  function openLink(p: api.PersonnelRow) {
    setEditId(null);
    setLinkId(p.id);
    setLinkUsername("");
  }

  async function submitLink(e: FormEvent) {
    e.preventDefault();
    if (linkId === null || actionBusy || linkUsername.trim() === "") return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.linkPersonnelAccount(linkId, linkUsername.trim());
      setLinkId(null);
      setLinkUsername("");
      await reloadList();
      setActionMsg({ ok: true, text: "Account linked." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Link failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function doUnlink(p: api.PersonnelRow) {
    if (actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.unlinkPersonnelAccount(p.id);
      await reloadList();
      setActionMsg({ ok: true, text: "Account unlinked." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Unlink failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function doRetire(p: api.PersonnelRow) {
    if (actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.retirePersonnel(p.id);
      await reloadList();
      setActionMsg({ ok: true, text: "Personnel retired." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Retire failed." });
    } finally {
      setActionBusy(false);
    }
  }

  function openAssign(p: api.PersonnelRow) {
    setEditId(null);
    setLinkId(null);
    setAssignId(p.id);
    setAssignJob(p.current_job ?? "");
  }

  async function submitAssign(e: FormEvent) {
    e.preventDefault();
    if (assignId === null || actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      // "" (the Unassign option) → null clears the placement; a job_id sets it.
      await api.assignPersonnel(assignId, assignJob || null);
      setAssignId(null);
      await reloadList();
      setActionMsg({ ok: true, text: assignJob ? "Crew placement updated." : "Crew unassigned." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Assign failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function loadList() {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchPersonnelList(cursor || undefined);
      setPersonnel((prev) => [...prev, ...data.personnel]);
      // Merge latest_entries by id
      const entries: Record<number, api.LatestEntry> = {};
      for (const e of data.latest_entries) {
        entries[e.personnel_id] = e;
      }
      setLatestEntries((prev) => ({ ...prev, ...entries }));
      setCursor(data.next_cursor);
    } catch (e) {
      setError("Failed to load personnel.");
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    if (!cursor || loading) return;
    await loadList();
  }

  function openDetail(id: number) {
    setSelectedId(id);
    setError(null);
    setView("detail");
  }

  function backToList() {
    setView("list");
    setSelectedId(null);
    setError(null);
  }

  if (view === "detail" && selectedId != null) {
    return <PersonnelDetailView id={selectedId} onBack={backToList} onHome={onBack} />;
  }

  // ── ROSTER (card grid) ──
  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">Personnel</h2>
      <p className="dash__intro">
        The active crew roster — who's on the team, their trade, standing job placement, and most
        recent logged job. Select a card for a person's full time history.
      </p>

      {actionMsg && (
        <div className={`banner ${actionMsg.ok ? "banner--ok" : "banner--err"}`}>{actionMsg.text}</div>
      )}
      {error && <div className="banner banner--err">{error}</div>}

      {canManage && (
        <section className="card dash-section">
          <h3 className="dash-detail__h2">Add personnel</h3>
          <form onSubmit={submitCreate} className="dash-row" aria-label="Add personnel">
            <input name="name" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Full name" maxLength={128} />{" "}
            <input name="trade" value={newTrade} onChange={(e) => setNewTrade(e.target.value)} placeholder="Trade (optional)" maxLength={64} />{" "}
            {/* Login-account minting is admin-only (the Worker 403s a non-admin, e.g. a manager);
                hide the whole sub-form for non-admins so there's no dead control. */}
            {isAdmin && (
              <>
                <label>
                  <input name="withAccount" type="checkbox" checked={withAccount} onChange={(e) => setWithAccount(e.target.checked)} />{" "}
                  Also create a login account
                </label>{" "}
                {withAccount && (
                  <>
                    <input name="username" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} placeholder="username (lastname.firstname)" maxLength={64} />{" "}
                    <input name="password" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="temp password" maxLength={256} />{" "}
                    <select name="role" value={newRole} onChange={(e) => setNewRole(e.target.value as api.AccountRole)}>
                      <option value="submitter">Field PM (submitter)</option>
                      <option value="manager">Manager (crew lead)</option>
                      <option value="admin">Admin</option>
                    </select>{" "}
                  </>
                )}
              </>
            )}
            <button type="submit" disabled={actionBusy} className="btn--primary">Add personnel</button>
          </form>
        </section>
      )}

      {personnel.length === 0 ? (
        loading ? (
          <div className="muted">Loading personnel…</div>
        ) : (
          <div className="dash-unavail">No active personnel.</div>
        )
      ) : (
        <>
          <div className="dash-grid">
            {personnel.map((p) => {
              const entry = latestEntries[p.id];
              const placedLabel = p.current_job_name ?? p.current_job ?? null;
              return (
                <section
                  key={p.id}
                  className="card dash-card--click"
                  role="button"
                  tabIndex={0}
                  onClick={() => openDetail(p.id)}
                  onKeyDown={(e) => {
                    // Only the card ITSELF (not a focused manage button/field inside it) toggles the
                    // detail view — so Space typed in an inline manage field never navigates away.
                    if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
                      e.preventDefault();
                      openDetail(p.id);
                    }
                  }}
                >
                  <div className="dash-card__head">
                    <h3 className="dash-card__title">{p.name}</h3>
                    <span className={placedLabel ? "dash-pill dash-pill--ok" : "dash-pill"}>
                      {placedLabel ? "Placed" : "Unassigned"}
                    </span>
                  </div>
                  <div className="dash-card__sub">{p.username ? `@${p.username}` : "No account linked"}</div>

                  <div className="dash-card__row dash-chips">
                    <span className="dash-chip">{p.trade || "No trade"}</span>
                    {placedLabel && <span className="dash-chip">Placed on {placedLabel}</span>}
                  </div>

                  <div className="dash-card__row">
                    <span className="dash-card__label">Latest job</span>
                    {entry ? (
                      <span>
                        {entry.project_name ?? entry.job_id}
                        <span className="dash-card__sub"> · {fmtHours(entry.hours)} h</span>
                      </span>
                    ) : (
                      <span className="dash-unavail">No time logged</span>
                    )}
                  </div>

                  {(canManage || canAssign) && (
                    // stopPropagation on the whole manage well so clicking a control / typing in an
                    // inline form never bubbles to the card's open-detail handler.
                    <div className="dash-card__row" onClick={(e) => e.stopPropagation()}>
                      <div className="dash-chips">
                        {canManage && (
                          <button className="btn--edit" onClick={(e) => { e.stopPropagation(); openEdit(p); }}>Edit</button>
                        )}
                        {canManage && (p.username ? (
                          <button className="btn--secondary" onClick={(e) => { e.stopPropagation(); doUnlink(p); }} disabled={actionBusy}>Unlink account</button>
                        ) : (
                          <button className="btn--secondary" onClick={(e) => { e.stopPropagation(); openLink(p); }}>Link account</button>
                        ))}
                        {canAssign && (
                          <button className="btn--edit" onClick={(e) => { e.stopPropagation(); openAssign(p); }}>Assign</button>
                        )}
                        {canManage && (
                          <button className="btn--retire" onClick={(e) => { e.stopPropagation(); doRetire(p); }} disabled={actionBusy}>Retire</button>
                        )}
                      </div>

                      {canManage && editId === p.id && (
                        <form onSubmit={submitEdit} className="dash-row" aria-label="Edit personnel">
                          <input name="name" value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="Full name" maxLength={128} />{" "}
                          <input name="trade" value={editTrade} onChange={(e) => setEditTrade(e.target.value)} placeholder="Trade (optional)" maxLength={64} />{" "}
                          <button type="submit" disabled={actionBusy} className="btn--primary">Save</button>{" "}
                          <button type="button" onClick={() => setEditId(null)} className="btn--secondary">Cancel</button>
                        </form>
                      )}

                      {canManage && linkId === p.id && (
                        <form onSubmit={submitLink} className="dash-row" aria-label="Link personnel account">
                          <input name="username" value={linkUsername} onChange={(e) => setLinkUsername(e.target.value)} placeholder="account username" maxLength={64} />{" "}
                          <button type="submit" disabled={actionBusy} className="btn--primary">Link</button>{" "}
                          <button type="button" onClick={() => setLinkId(null)} className="btn--secondary">Cancel</button>
                        </form>
                      )}

                      {canAssign && assignId === p.id && (
                        <form onSubmit={submitAssign} className="dash-row" aria-label="Assign crew to job">
                          <select value={assignJob} onChange={(e) => setAssignJob(e.target.value)} aria-label="Job placement">
                            <option value="">— Unassign —</option>
                            {jobs.map((j) => (
                              <option key={j.job_id} value={j.job_id}>{j.project_name} ({j.job_id})</option>
                            ))}
                          </select>{" "}
                          <button type="submit" disabled={actionBusy} className="btn--primary">Save placement</button>{" "}
                          <button type="button" onClick={() => setAssignId(null)} className="btn--secondary">Cancel</button>
                        </form>
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>

          {cursor && (
            <div className="dash-row dash-load-more">
              <button onClick={loadMore} disabled={loading} className="btn--secondary">
                {loading ? "Loading..." : "Load more"}
              </button>
            </div>
          )}
        </>
      )}
    </PageShell>
  );
}
