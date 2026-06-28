import { useState, useEffect } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_jobtracker";
import { useAuth } from "../lib/auth";
import { PageShell } from "../components/PageShell";

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

  // Write (P2.3; Worker re-gates server-side — these caps drive UI affordances only).
  const { user } = useAuth();
  const caps = user?.capabilities ?? [];
  const canManage = caps.includes("cap.jobtracker.manage"); // create / close / progress / add-task
  const canOwnTasks = caps.includes("cap.tasks.own"); // change a task's own status
  const canLogTime = caps.includes("cap.time.log"); // log a time entry against the open job
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // New-job form (list view)
  const [newJobId, setNewJobId] = useState("");
  const [newJobName, setNewJobName] = useState("");
  const [newJobClient, setNewJobClient] = useState("");
  const [newJobOpen, setNewJobOpen] = useState(false);
  // Detail manage controls
  const [progressVal, setProgressVal] = useState("");
  const [taskDesc, setTaskDesc] = useState("");
  // Time-log form (detail)
  const [logHours, setLogHours] = useState("");
  const [logNotes, setLogNotes] = useState("");
  const [logTask, setLogTask] = useState("");

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

  // ── WRITE handlers (P2.3) ──────────────────────────────────────────────────────────────────────
  // Re-fetch the open job from scratch (drops appended history legs back to the first page; the
  // mutation just landed so the first page is what we want to show). Resets all three leg cursors.
  async function reloadDetail() {
    if (!selectedJob) return;
    const res = await api.fetchJobDetail(selectedJob.job_id);
    setSelectedJob(res.job);
    setTaskCursor(res.cursors.tasks);
    setTimeCursor(res.cursors.time);
    setInspCursor(res.cursors.insp);
  }

  async function reloadList() {
    const data = await api.fetchJobList(statusFilter);
    setJobs(data.jobs);
    setCursor(data.next_cursor);
  }

  async function submitNewJob(e: FormEvent) {
    e.preventDefault();
    if (actionBusy) return;
    const jobId = newJobId.trim().toUpperCase();
    const projectName = newJobName.trim();
    if (!jobId || !projectName) {
      setActionMsg({ ok: false, text: "Job ID and project name are required." });
      return;
    }
    setActionBusy(true);
    setActionMsg(null);
    try {
      const clientName = newJobClient.trim();
      await api.createJob({
        job_id: jobId,
        project_name: projectName,
        ...(clientName ? { new_client: { name: clientName } } : {}),
      });
      setNewJobId("");
      setNewJobName("");
      setNewJobClient("");
      setNewJobOpen(false);
      await reloadList();
      setActionMsg({ ok: true, text: `Job ${jobId} created.` });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Create failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitClose() {
    if (!selectedJob || actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.closeJob(selectedJob.job_id);
      await reloadDetail();
      setActionMsg({ ok: true, text: "Job closed." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Close failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitProgress(e: FormEvent) {
    e.preventDefault();
    if (!selectedJob || actionBusy) return;
    const pct = Number(progressVal);
    if (progressVal.trim() === "" || !Number.isFinite(pct)) {
      setActionMsg({ ok: false, text: "Progress must be a number (0–100)." });
      return;
    }
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.setJobProgress(selectedJob.job_id, pct);
      setProgressVal("");
      await reloadDetail();
      setActionMsg({ ok: true, text: "Progress updated." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitAddTask(e: FormEvent) {
    e.preventDefault();
    if (!selectedJob || actionBusy) return;
    const description = taskDesc.trim();
    if (!description) {
      setActionMsg({ ok: false, text: "Task description is required." });
      return;
    }
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.addTask(selectedJob.job_id, { description });
      setTaskDesc("");
      await reloadDetail();
      setActionMsg({ ok: true, text: "Task added." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Add task failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function changeTaskStatus(taskId: number, status: api.TaskStatus) {
    if (actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.setTaskStatus(taskId, status);
      await reloadDetail();
      setActionMsg({ ok: true, text: "Task updated." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Task update failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitLogTime(e: FormEvent) {
    e.preventDefault();
    if (!selectedJob || actionBusy) return;
    const hours = logHours.trim() === "" ? undefined : Number(logHours);
    if (hours !== undefined && (!Number.isFinite(hours) || hours < 0)) {
      setActionMsg({ ok: false, text: "Hours must be a non-negative number." });
      return;
    }
    const taskId = logTask === "" ? undefined : Number(logTask);
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.logTime({
        uuid: crypto.randomUUID(), // client-generated idempotency key (integrity-bar)
        job_id: selectedJob.job_id,
        ...(hours !== undefined ? { hours } : {}),
        ...(taskId !== undefined ? { task_id: taskId } : {}),
        ...(logNotes.trim() ? { notes: logNotes.trim() } : {}),
      });
      setLogHours("");
      setLogNotes("");
      setLogTask("");
      await reloadDetail();
      setActionMsg({ ok: true, text: "Time logged." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Time log failed." });
    } finally {
      setActionBusy(false);
    }
  }

  if (view === "detail" && selectedJob) {
    const job = selectedJob;
    return (
      <PageShell onHome={onBack}>
        <div className="dash-back-btn">
          <button onClick={handleBack} className="btn--secondary">← Back to jobs</button>
        </div>

        <div className="dash-detail__head">
          <h2 className="page__heading">{job.project_name}</h2>
          <span className={jobPillClass(job.status)}>{job.status}</span>
        </div>
        <p className="dash-card__sub muted">{(job.client?.name ?? "No client")} · {job.job_id}</p>

        {actionMsg && (
          <p className="muted" style={{ color: actionMsg.ok ? "green" : "red" }}>{actionMsg.text}</p>
        )}

        <section className="card dash-section">
          <span className="dash-card__label">Progress — {job.progress}%</span>
          <div className="dash-progress">
            <div className="dash-progress__fill" style={{ width: `${clampPct(job.progress)}%` }} />
          </div>
        </section>

        {canManage && (
          <section className="card dash-section">
            <h3 className="dash-detail__h2">Manage job</h3>
            <form onSubmit={submitProgress} className="dash-row" aria-label="Update job progress">
              <label className="dash-card__label">
                Progress %:{" "}
                <input
                  value={progressVal}
                  onChange={(e) => setProgressVal(e.target.value)}
                  placeholder={String(job.progress)}
                  inputMode="numeric"
                  size={5}
                />
              </label>{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Set progress</button>
            </form>
            <form onSubmit={submitAddTask} className="dash-row" aria-label="Add a task">
              <input
                value={taskDesc}
                onChange={(e) => setTaskDesc(e.target.value)}
                placeholder="New task description"
                maxLength={256}
              />{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Add task</button>
            </form>
            {job.status === "active" && (
              <div className="dash-row">
                <button onClick={submitClose} disabled={actionBusy} className="btn--secondary">Close job</button>
              </div>
            )}
          </section>
        )}

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
                  {canOwnTasks && (
                    <>
                      {" "}
                      <select
                        aria-label={`Set status for task ${t.id}`}
                        value={t.status}
                        disabled={actionBusy}
                        onChange={(e) => changeTaskStatus(t.id, e.target.value as api.TaskStatus)}
                      >
                        <option value="open">open</option>
                        <option value="in_progress">in_progress</option>
                        <option value="done">done</option>
                      </select>
                    </>
                  )}
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
          {canLogTime && job.status === "active" && (
            <form onSubmit={submitLogTime} className="dash-row" aria-label="Log time">
              <input
                value={logHours}
                onChange={(e) => setLogHours(e.target.value)}
                placeholder="Hours"
                inputMode="decimal"
                size={5}
              />{" "}
              <label className="dash-card__label">
                Task:{" "}
                <select value={logTask} onChange={(e) => setLogTask(e.target.value)}>
                  <option value="">— job-level —</option>
                  {job.tasks.map((t) => (
                    <option key={t.id} value={t.id}>{t.description}</option>
                  ))}
                </select>
              </label>{" "}
              <input
                value={logNotes}
                onChange={(e) => setLogNotes(e.target.value)}
                placeholder="Notes (optional)"
                maxLength={2000}
              />{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Log time</button>
            </form>
          )}
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
      </PageShell>
    );
  }

  // List view
  return (
    <PageShell onHome={onBack}>

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

      {canManage && (
        <div className="dash-row">
          {newJobOpen ? (
            <form onSubmit={submitNewJob} className="dash-row" aria-label="Create job">
              <input
                value={newJobId}
                onChange={(e) => setNewJobId(e.target.value)}
                placeholder="Job ID (e.g. JOB-1042)"
                maxLength={64}
              />{" "}
              <input
                value={newJobName}
                onChange={(e) => setNewJobName(e.target.value)}
                placeholder="Project name"
                maxLength={256}
              />{" "}
              <input
                value={newJobClient}
                onChange={(e) => setNewJobClient(e.target.value)}
                placeholder="Client name (optional)"
                maxLength={256}
              />{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Create</button>{" "}
              <button type="button" onClick={() => setNewJobOpen(false)} className="btn--secondary">Cancel</button>
            </form>
          ) : (
            <button onClick={() => setNewJobOpen(true)} className="btn--secondary">+ New job</button>
          )}
        </div>
      )}
      {actionMsg && (
        <p className="muted" style={{ color: actionMsg.ok ? "green" : "red" }}>{actionMsg.text}</p>
      )}
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
    </PageShell>
  );
}
