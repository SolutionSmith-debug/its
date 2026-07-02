import { useEffect, useState } from "react";
import * as api from "../lib/fieldops_tasks";
import * as checklist from "../lib/fieldops_checklist";
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

// Status → pill class for a checklist item state (done = ok, else neutral).
function itemPill(status: string): string {
  return status === "done" ? "dash-pill dash-pill--ok" : "dash-pill";
}

/**
 * S3 — "Today's checklist" for a placed manager. Self-contained: fetches GET /checklist/mine, which
 * runs Worker-on-read generation and returns `instance: null` for anyone who isn't a placed manager
 * (a subcontractor, or a manager with no current_job) — in which case this renders NOTHING (the
 * My-Tasks list stays exactly as S1). manual_attest items get a done/undo control (S3); the other
 * three item types are shown read-only (loop-closure + count/inspection completion arrive in S4).
 */
function DailyChecklistSection() {
  const [data, setData] = useState<checklist.MyChecklist | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    checklist
      .fetchMyChecklist()
      .then(setData)
      .catch(() => setData({ instance: null, items: [] }));
  }, []);

  async function toggle(item: checklist.ChecklistItemState) {
    if (busyId !== null) return;
    setBusyId(item.id);
    setMsg(null);
    try {
      const res =
        item.status === "done"
          ? await checklist.uncompleteChecklistItem(item.id)
          : await checklist.completeChecklistItem(item.id, notes[item.id] ? { note: notes[item.id] } : undefined);
      // Refresh from the server (also picks up the recomputed instance status).
      const fresh = await checklist.fetchMyChecklist();
      setData(fresh);
      setMsg({ ok: true, text: res.instance_status === "complete" ? "Checklist complete." : "Item updated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusyId(null);
    }
  }

  // Not a placed manager → no daily section at all.
  if (!data || data.instance === null) return null;

  return (
    <section className="card dash-section" aria-label="Today's checklist">
      <h3 className="dash-detail__h2">
        Today&apos;s checklist
        <span className="dash-card__sub"> · {data.instance.job_id} · {data.instance.instance_date}</span>{" "}
        <span className={data.instance.status === "complete" ? "dash-pill dash-pill--ok" : "dash-pill dash-pill--warn"}>
          {data.instance.status}
        </span>
      </h3>
      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}
      {data.items.length === 0 ? (
        <div className="muted">No checklist items for today.</div>
      ) : (
        <ul className="dash-tasklist">
          {data.items.map((it) => {
            const isManual = it.item_type === "manual_attest";
            const done = it.status === "done";
            return (
              <li key={it.id}>
                <span className={itemPill(it.status)}>{it.status}</span> {it.label}
                <span className="dash-card__sub"> · {it.item_type}</span>
                {isManual ? (
                  <>
                    {!done && (
                      <>
                        {" "}
                        <input
                          type="text"
                          aria-label={`Note for item ${it.id}`}
                          placeholder="note (optional)"
                          value={notes[it.id] ?? ""}
                          onChange={(e) => setNotes((n) => ({ ...n, [it.id]: e.target.value }))}
                          disabled={busyId !== null}
                        />
                      </>
                    )}{" "}
                    <button
                      type="button"
                      className={done ? "btn btn--ghost" : "btn btn--primary"}
                      aria-label={done ? `Undo item ${it.id}` : `Complete item ${it.id}`}
                      disabled={busyId !== null}
                      onClick={() => toggle(it)}
                    >
                      {done ? "Undo" : "Mark done"}
                    </button>
                    {done && it.note ? <span className="dash-card__sub"> · {it.note}</span> : null}
                  </>
                ) : (
                  <span className="dash-card__sub"> · completion arrives soon</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
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

      {/* S3 — the placed manager's daily checklist (renders nothing for anyone else). */}
      <DailyChecklistSection />

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
