import { useState, useEffect } from "react";
import * as api from "../lib/fieldops_jobtracker";

// epoch SECONDS → ×1000 for JS Date
function fmtDateTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString();
}

function fmtHours(hours: number | null): string {
  if (hours == null || isNaN(hours)) return "—";
  return hours.toFixed(2);
}

function jobPillClass(s: string): string {
  if (s === "active") return "dash-pill dash-pill--ok";
  if (s === "on_hold") return "dash-pill dash-pill--warn";
  return "dash-pill"; // closed (and anything else)
}

function taskPillClass(s: string): string {
  if (s === "in_progress") return "dash-pill dash-pill--warn";
  if (s === "done") return "dash-pill dash-pill--ok";
  return "dash-pill"; // open
}

function clampPct(n: number): number {
  return Math.max(0, Math.min(100, n));
}

const STATUS_OPTIONS: { value: api.JobStatusFilter; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "closed", label: "Closed" },
  { value: "on_hold", label: "On hold" },
  { value: "all", label: "All" },
];

export function FieldOpsJobTracker({ onBack }: { onBack: () => void }) {
  const [view, setView] = useState<"list" | "detail">("list");
  const [jobs, setJobs] = useState<api.JobRow[]>([]);
  const [statusFilter, setStatusFilter] = useState<api.JobStatusFilter>("active");
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Detail state
  const [selectedJob, setSelectedJob] = useState<api.JobDetail | null>(null);
  const [taskCursor, setTaskCursor] = useState<string | null>(null);
  const [timeCursor, setTimeCursor] = useState<string | null>(null);
  const [inspCursor, setInspCursor] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Reload the list whenever the status filter changes (and on mount).
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError(null);
    api
      .fetchJobList(statusFilter)
      .then((data) => {
        if (!live) return;
        setJobs(data.jobs);
        setCursor(data.next_cursor);
      })
      .catch(() => live && setError("Failed to load jobs."))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [statusFilter]);

  async function loadMore() {
    if (!cursor || loading) return;
    setLoading(true);
    try {
      const data = await api.fetchJobList(statusFilter, cursor);
      setJobs((prev) => [...prev, ...data.jobs]);
      setCursor(data.next_cursor);
    } catch {
      setError("Failed to load more jobs.");
    } finally {
      setLoading(false);
    }
  }

  function handleCardClick(job: api.JobRow) {
    setView("detail");
    setSelectedJob(null);
    setDetailLoading(true);
    api
      .fetchJobDetail(job.job_id)
      .then((res) => {
        setSelectedJob(res.job);
        setTaskCursor(res.cursors.tasks);
        setTimeCursor(res.cursors.time);
        setInspCursor(res.cursors.insp);
        setDetailLoading(false);
      })
      .catch(() => setError("Failed to load job details."));
  }

  function handleBack() {
    if (view === "detail") {
      setView("list");
      setSelectedJob(null);
      setTaskCursor(null);
      setTimeCursor(null);
      setInspCursor(null);
      setError(null);
    } else {
      onBack();
    }
  }

  // Each history leg (tasks / time / inspections) paginates INDEPENDENTLY: a "Load more" on one
  // leg re-fetches the detail with only that leg's cursor and appends just that leg's new rows,
  // leaving the others untouched. The worker returns a fresh { tasks, time, insp } cursor set.
  async function loadMoreLeg(leg: "task" | "time" | "insp") {
    if (!selectedJob) return;
    const cur = leg === "task" ? taskCursor : leg === "time" ? timeCursor : inspCursor;
    if (!cur) return;
    setDetailLoading(true);
    try {
      const res = await api.fetchJobDetail(selectedJob.job_id, { [leg]: cur });
      setSelectedJob((prev) => {
        if (!prev) return res.job;
        if (leg === "task") return { ...prev, tasks: [...prev.tasks, ...res.job.tasks] };
        if (leg === "time") return { ...prev, time_entries: [...prev.time_entries, ...res.job.time_entries] };
        return { ...prev, inspections: [...prev.inspections, ...res.job.inspections] };
      });
      if (leg === "task") setTaskCursor(res.cursors.tasks);
      else if (leg === "time") setTimeCursor(res.cursors.time);
      else setInspCursor(res.cursors.insp);
    } catch {
      setError("Failed to load more.");
    } finally {
      setDetailLoading(false);
    }
  }

  function LoadMoreBtn({ leg }: { leg: "task" | "time" | "insp" }) {
    return (
      <div className="dash-row dash-load-more">
        <button onClick={() => loadMoreLeg(leg)} disabled={detailLoading} className="btn--secondary">
          {detailLoading ? "Loading..." : "Load more"}
        </button>
      </div>
    );
  }

  if (view === "detail" && selectedJob) {
    const job = selectedJob;
    return (
      <div className="page">
        <div className="dash-row dash-back-btn">
          <button onClick={handleBack} className="btn--ghost">← Back to jobs</button>
        </div>

        <div className="dash-detail__head">
          <h2 className="page__heading">{job.project_name}</h2>
          <span className={jobPillClass(job.status)}>{job.status}</span>
        </div>
        <p className="dash-card__sub muted">{(job.client?.name ?? "No client")} · {job.job_id}</p>

        <section className="card dash-section">
          <span className="dash-card__label">Progress — {job.progress}%</span>
          <div className="dash-progress">
            <div className="dash-progress__fill" style={{ width: `${clampPct(job.progress)}%` }} />
          </div>
        </section>

        {job.client && (
          <section className="card dash-section">
            <h3 className="dash-detail__h2">Client</h3>
            <div>{job.client.name}</div>
            <div className="muted">
              {[job.client.contact, job.client.phone, job.client.email].filter(Boolean).join(" · ") || "—"}
            </div>
          </section>
        )}

        <section className="card dash-section">
          <h3 className="dash-detail__h2">Assigned crew ({job.crew.length})</h3>
          {job.crew.length ? (
            <div className="dash-chips">
              {job.crew.map((p) => (
                <span className="dash-chip" key={p.id}>{p.name}{p.trade ? ` · ${p.trade}` : ""}</span>
              ))}
            </div>
          ) : (
            <div className="dash-unavail">No crew assigned.</div>
          )}
        </section>

        <section className="card dash-section">
          <h3 className="dash-detail__h2">Tasks</h3>
          {job.tasks.length ? (
            <ul className="dash-tasklist">
              {job.tasks.map((t) => (
                <li key={t.id}>
                  <span className={taskPillClass(t.status)}>{t.status}</span> {t.description}
                  {t.personnel_name ? <span className="muted"> — {t.personnel_name}</span> : null}
                </li>
              ))}
            </ul>
          ) : (
            <div className="dash-unavail">No tasks.</div>
          )}
          {taskCursor && <LoadMoreBtn leg="task" />}
        </section>

        <section className="card dash-section">
          <h3 className="dash-detail__h2">Time entries</h3>
          {job.time_entries.length ? (
            <table className="dash-table">
              <thead>
                <tr>
                  <th className="dash-header">Recorded</th>
                  <th className="dash-header">Who</th>
                  <th className="dash-header">Hours</th>
                  <th className="dash-header">Notes</th>
                </tr>
              </thead>
              <tbody>
                {job.time_entries.map((t) => (
                  <tr key={t.uuid} className="dash-row">
                    <td className="dash-cell">{fmtDateTime(t.recorded_at)}</td>
                    <td className="dash-cell">{t.personnel_name ?? "—"}</td>
                    <td className="dash-cell">{fmtHours(t.hours)}</td>
                    <td className="dash-cell">{t.notes ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="dash-unavail">No time logged.</div>
          )}
          {timeCursor && <LoadMoreBtn leg="time" />}
        </section>

        <section className="card dash-section">
          <h3 className="dash-detail__h2">Equipment on site ({job.equipment_on_site.length})</h3>
          {job.equipment_on_site.length ? (
            <div className="dash-chips">
              {job.equipment_on_site.map((e) => (
                <span className="dash-chip" key={e.id}>{e.name}{e.identifier ? ` · ${e.identifier}` : ""}</span>
              ))}
            </div>
          ) : (
            <div className="dash-unavail">No equipment on site.</div>
          )}
        </section>

        <section className="card dash-section">
          <h3 className="dash-detail__h2">Inspections</h3>
          {job.inspections.length ? (
            <table className="dash-table">
              <thead>
                <tr>
                  <th className="dash-header">Form</th>
                  <th className="dash-header">Equipment</th>
                  <th className="dash-header">Performed</th>
                </tr>
              </thead>
              <tbody>
                {job.inspections.map((i) => (
                  <tr key={i.uuid} className="dash-row">
                    <td className="dash-cell">{i.form_code} v{i.version}</td>
                    <td className="dash-cell">{i.equipment_name ?? "—"}</td>
                    <td className="dash-cell">{fmtDateTime(i.performed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="dash-unavail">No inspections.</div>
          )}
          {inspCursor && <LoadMoreBtn leg="insp" />}
        </section>
      </div>
    );
  }

  // List view
  return (
    <div className="page">
      <div className="dash-row dash-back-btn">
        <button onClick={handleBack} className="btn--ghost">← Back</button>
      </div>

      <h2 className="page__heading">Job Tracker</h2>
      <div className="dash-row">
        <label className="dash-card__label">Status:{" "}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as api.JobStatusFilter)}
          >
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
      </div>
      {error && <p className="muted" style={{ color: "red" }}>{error}</p>}

      {jobs.length === 0 ? (
        loading ? (
          <div className="muted">Loading jobs…</div>
        ) : (
          <div className="dash-unavail">No jobs for this status.</div>
        )
      ) : (
        <>
          <div className="dash-grid">
            {jobs.map((job) => (
              <div
                key={job.job_id}
                onClick={() => handleCardClick(job)}
                className="dash-card--click"
                role="button"
              >
                <div className="dash-card__head">
                  <h3 className="dash-card__title">{job.project_name}</h3>
                  <span className={jobPillClass(job.status)}>{job.status}</span>
                </div>
                <div className="dash-card__sub">{(job.client_name ?? "No client")} · {job.job_id}</div>

                <div className="dash-progress">
                  <div className="dash-progress__fill" style={{ width: `${clampPct(job.progress)}%` }} />
                </div>

                {job.crew.length > 0 && (
                  <div className="dash-chips">
                    {job.crew.map((p) => (
                      <span className="dash-chip" key={p.id}>{p.name}</span>
                    ))}
                  </div>
                )}

                {job.open_tasks.length > 0 && (
                  <ul className="dash-tasklist">
                    {job.open_tasks.map((t) => (
                      <li key={t.id}>
                        <span className={taskPillClass(t.status)}>{t.status}</span> {t.description}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
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
    </div>
  );
}
