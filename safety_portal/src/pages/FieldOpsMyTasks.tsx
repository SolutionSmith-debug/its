import { useEffect, useState } from "react";
import * as api from "../lib/fieldops_tasks";
import { PageShell } from "../components/PageShell";
import { useAuth } from "../lib/auth";

// Status → pill class (mirrors the Job Tracker task-list styling): done = ok, in_progress = warn.
function statusPill(status: string): string {
  if (status === "done") return "dash-pill dash-pill--ok";
  if (status === "in_progress") return "dash-pill dash-pill--warn";
  return "dash-pill";
}

interface JobGroup {
  job_id: string;
  project_name: string | null;
  tasks: api.MyTask[];
}

// Group a flat task list by job, preserving the server order within each group and the order in
// which each job first appears.
function groupByJob(tasks: api.MyTask[]): JobGroup[] {
  const order: string[] = [];
  const byJob = new Map<string, JobGroup>();
  for (const t of tasks) {
    let g = byJob.get(t.job_id);
    if (!g) {
      g = { job_id: t.job_id, project_name: t.project_name, tasks: [] };
      byJob.set(t.job_id, g);
      order.push(t.job_id);
    }
    g.tasks.push(t);
  }
  return order.map((id) => byJob.get(id)!);
}

/**
 * Assigned-Tasks tab (P4 S1) — "My Tasks". A subcontractor (submitter) or manager sees the one-off
 * tasks assigned to THEM, grouped by job, and can advance each task's status (cap.tasks.own, reusing
 * the existing setTaskStatus route). "Assigned to me" is resolved server-side via the personnel↔account
 * link; an account with no linked personnel row simply has no tasks (empty state). The cap gate here is
 * a CONVENIENCE — the Worker re-gates every read + status write (Invariant 2).
 */
export function FieldOpsMyTasks({ onBack }: { onBack: () => void }) {
  const { user } = useAuth();
  const canOwn = (user?.capabilities ?? []).includes("cap.tasks.own");

  const [tasks, setTasks] = useState<api.MyTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchMyTasks();
      setTasks(data.tasks);
    } catch {
      setError("Failed to load your tasks.");
    } finally {
      setLoading(false);
    }
  }

  async function changeStatus(taskId: number, status: api.TaskStatus) {
    if (busyId !== null) return;
    setBusyId(taskId);
    setActionMsg(null);
    // Optimistic update; revert on failure.
    const prev = tasks;
    setTasks((ts) => ts.map((t) => (t.id === taskId ? { ...t, status } : t)));
    try {
      await api.setTaskStatus(taskId, status);
      setActionMsg({ ok: true, text: "Task updated." });
    } catch (err) {
      setTasks(prev);
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  const groups = groupByJob(tasks);

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">My Tasks</h2>
      <p className="dash__intro">
        The one-off tasks assigned to you, grouped by job. Update a task's status as you work it.
      </p>

      {actionMsg && (
        <div className={`banner ${actionMsg.ok ? "banner--ok" : "banner--err"}`}>{actionMsg.text}</div>
      )}
      {error && <div className="banner banner--err">{error}</div>}

      {loading && tasks.length === 0 ? (
        <div className="muted">Loading your tasks…</div>
      ) : groups.length === 0 ? (
        <div className="dash-unavail">
          No tasks are assigned to you. Tasks your crew lead or the office assigns to you will appear here.
        </div>
      ) : (
        <div className="dash-grid">
          {groups.map((g) => (
            <section key={g.job_id} className="card dash-section">
              <h3 className="dash-detail__h2">
                {g.project_name ?? g.job_id}
                <span className="dash-card__sub"> · {g.job_id}</span>
              </h3>
              <ul className="dash-tasklist">
                {g.tasks.map((t) => (
                  <li key={t.id}>
                    <span className={statusPill(t.status)}>{t.status}</span> {t.description}
                    {canOwn && (
                      <>
                        {" "}
                        <select
                          aria-label={`Set status for task ${t.id}`}
                          value={t.status}
                          disabled={busyId !== null}
                          onChange={(e) => changeStatus(t.id, e.target.value as api.TaskStatus)}
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
            </section>
          ))}
        </div>
      )}
    </PageShell>
  );
}
