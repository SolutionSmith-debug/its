import { useState, useEffect } from "react";
import * as api from "../lib/fieldops_personnel";

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

  useEffect(() => {
    loadList();
  }, []);

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
      <div className="page">
        <div className="dash-row dash-back-btn">
          <button onClick={handleBack} className="btn--ghost">
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
      </div>
    );
  }

  // List view
  return (
    <div className="page">
      <div className="dash-row dash-back-btn">
        <button onClick={handleBack} className="btn--ghost">
          ← Back
        </button>
      </div>

      <h2 className="page__heading">Personnel</h2>
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
    </div>
  );
}
