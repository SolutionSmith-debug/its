import { useState, useEffect } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_personnel";
import { PageShell } from "../components/PageShell";
import { useAuth } from "../lib/auth";

// Format helpers: epoch SECONDS (as stored in D1) → ×1000 for JS Date
function fmtDateTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "—";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString();
}

function fmtHours(hours: number | null): string {
  if (hours == null || isNaN(hours)) return "—";
  return hours.toFixed(2);
}

export function FieldOpsPersonnel({ onBack }: { onBack: () => void }) {
  const [view, setView] = useState<"list" | "detail">("list");
  const [personnel, setPersonnel] = useState<api.PersonnelRow[]>([]);
  const [latestEntries, setLatestEntries] = useState<Record<number, api.LatestEntry>>({});
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Detail state
  const [selectedPersonnel, setSelectedPersonnel] = useState<api.PersonnelDetail | null>(null);
  const [detailCursor, setDetailCursor] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Manage state (task #22; cap.personnel.manage). The capability gate here is a CONVENIENCE — the
  // Worker re-gates every write route server-side (Invariant 2: never trust the client).
  const { user } = useAuth();
  const canManage = (user?.capabilities ?? []).includes("cap.personnel.manage");
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

  useEffect(() => {
    loadList();
  }, []);

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

  function handleRowClick(p: api.PersonnelRow) {
    setView("detail");
    setSelectedPersonnel(null);
    setDetailLoading(true);
    api
      .fetchPersonnelDetail(p.id, undefined)
      .then((res) => {
        setSelectedPersonnel(res.personnel);
        setDetailCursor(res.next_cursor);
        setDetailLoading(false);
      })
      .catch(() => setError("Failed to load personnel details."));
  }

  function handleBack() {
    if (view === "detail") {
      setView("list");
      setDetailCursor(null);
      setSelectedPersonnel(null);
      setError(null);
    } else {
      onBack();
    }
  }

  // Detail loading more entries
  async function loadMoreEntries() {
    if (!selectedPersonnel || !detailCursor) return;
    setDetailLoading(true);
    try {
      const res = await api.fetchPersonnelDetail(selectedPersonnel.id, detailCursor);
      setSelectedPersonnel((prev) => {
        if (!prev) return res.personnel;
        // Prepend new entries (detail uses DESC order, so newer results come first in next page)
        const mergedEntries = [...res.personnel.time_entries, ...prev.time_entries];
        return { ...prev, time_entries: mergedEntries };
      });
      setDetailCursor(res.next_cursor);
    } catch {
      setError("Failed to load more entries.");
    } finally {
      setDetailLoading(false);
    }
  }

  if (view === "detail" && selectedPersonnel) {
    return (
      <PageShell onHome={onBack}>
        <div className="dash-back-btn">
          <button onClick={handleBack} className="btn--secondary">
            ← Back to personnel
          </button>
        </div>

        <h2 className="page__heading">{selectedPersonnel.name}</h2>
        <p className="muted">
          {selectedPersonnel.username ? `@${selectedPersonnel.username}` : "No account linked"}
          {" • "}
          {selectedPersonnel.trade}
        </p>

        <div className="dash-section">
          <h3>Time history</h3>
          {selectedPersonnel.time_entries.length === 0 ? (
            <div className="dash-unavail">No time logged.</div>
          ) : (
            <>
              <table className="dash-table">
                <thead>
                  <tr>
                    <th className="dash-header">Job</th>
                    <th className="dash-header">Work started</th>
                    <th className="dash-header">Work ended</th>
                    <th className="dash-header">Hours</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedPersonnel.time_entries.map((e) => (
                    <tr key={e.uuid} className="dash-row">
                      <td className="dash-cell">{e.project_name ?? e.job_id}</td>
                      <td className="dash-cell">{fmtDateTime(e.work_started_at)}</td>
                      <td className="dash-cell">{fmtDateTime(e.work_ended_at)}</td>
                      <td className="dash-cell">{fmtHours(e.hours)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {detailCursor && (
                <div className="dash-row dash-load-more">
                  <button
                    onClick={loadMoreEntries}
                    disabled={detailLoading}
                    className="btn--secondary"
                  >
                    {detailLoading ? "Loading..." : "Load more"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </PageShell>
    );
  }

  // List view
  return (
    <PageShell onHome={onBack}>

      <h2 className="page__heading">Personnel</h2>

      {canManage && (
        <form onSubmit={submitCreate} className="dash-row" aria-label="Add personnel">
          {actionMsg && <p className="muted" style={{ color: actionMsg.ok ? "green" : "red" }}>{actionMsg.text}</p>}
          <input name="name" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Full name" maxLength={128} />{" "}
          <input name="trade" value={newTrade} onChange={(e) => setNewTrade(e.target.value)} placeholder="Trade (optional)" maxLength={64} />{" "}
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
                <option value="admin">Admin</option>
              </select>{" "}
            </>
          )}
          <button type="submit" disabled={actionBusy} className="btn--secondary">Add personnel</button>
        </form>
      )}

      {canManage && editId !== null && (
        <form onSubmit={submitEdit} className="dash-row" aria-label="Edit personnel">
          <input name="name" value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="Full name" maxLength={128} />{" "}
          <input name="trade" value={editTrade} onChange={(e) => setEditTrade(e.target.value)} placeholder="Trade (optional)" maxLength={64} />{" "}
          <button type="submit" disabled={actionBusy} className="btn--secondary">Save</button>{" "}
          <button type="button" onClick={() => setEditId(null)} className="btn--secondary">Cancel</button>
        </form>
      )}

      {canManage && linkId !== null && (
        <form onSubmit={submitLink} className="dash-row" aria-label="Link personnel account">
          <input name="username" value={linkUsername} onChange={(e) => setLinkUsername(e.target.value)} placeholder="account username" maxLength={64} />{" "}
          <button type="submit" disabled={actionBusy} className="btn--secondary">Link</button>{" "}
          <button type="button" onClick={() => setLinkId(null)} className="btn--secondary">Cancel</button>
        </form>
      )}

      {error && <p className="muted" style={{ color: "red" }}>{error}</p>}

      {personnel.length === 0 ? (
        loading ? (
          <div className="muted">Loading personnel…</div>
        ) : (
          <div className="dash-unavail">No active personnel.</div>
        )
      ) : (
        <>
          <table className="dash-table">
            <thead>
              <tr>
                <th className="dash-header">Name</th>
                <th className="dash-header">Trade</th>
                <th className="dash-header">Latest job</th>
                <th className="dash-header">Hours</th>
                {canManage && <th className="dash-header">Manage</th>}
              </tr>
            </thead>
            <tbody>
              {personnel.map((p) => {
                const entry = latestEntries[p.id];
                return (
                  <tr
                    key={p.id}
                    onClick={() => handleRowClick(p)}
                    className="dash-row dash-row--click"
                  >
                    <td className="dash-cell">
                      <div>{p.name}</div>
                      {p.username && <span className="muted">@{p.username}</span>}
                    </td>
                    <td className="dash-cell">{p.trade}</td>
                    <td className="dash-cell">{entry ? entry.project_name ?? entry.job_id : "—"}</td>
                    <td className="dash-cell">{fmtHours(entry?.hours ?? null)}</td>
                    {canManage && (
                      <td className="dash-cell">
                        <button className="btn--secondary" onClick={(e) => { e.stopPropagation(); openEdit(p); }}>Edit</button>{" "}
                        {p.username ? (
                          <button className="btn--secondary" onClick={(e) => { e.stopPropagation(); doUnlink(p); }} disabled={actionBusy}>Unlink account</button>
                        ) : (
                          <button className="btn--secondary" onClick={(e) => { e.stopPropagation(); openLink(p); }}>Link account</button>
                        )}{" "}
                        <button className="btn--secondary" onClick={(e) => { e.stopPropagation(); doRetire(p); }} disabled={actionBusy}>Retire</button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>

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
