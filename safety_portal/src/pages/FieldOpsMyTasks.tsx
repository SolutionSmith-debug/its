import { useEffect, useRef, useState } from "react";
import * as api from "../lib/fieldops_tasks";
import * as checklist from "../lib/fieldops_checklist";
import { statusLabel } from "../lib/labels";
import { PageShell } from "../components/PageShell";
import { DailyChecklistSection } from "../components/DailyChecklistSection";
import { AssignedInspectionsSection } from "../components/AssignedInspectionsSection";
import { AddCrewSection } from "../components/AddCrewSection";
import {
  CompletedDisclosure,
  InlineRowMsg,
  ROSTER_LINK_COPY,
  SectionError,
  SectionLoading,
  SectionRefreshWarn,
  errMsg,
  fmtEpochDate,
  type RowFeedback,
} from "../components/myTasksShared";
import { useAuth } from "../lib/auth";
import type { FormPrefill } from "./FormFillPage";

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

type Tab = "assigned" | "daily";

/**
 * "My Tasks" (P4 S1 + R2 restructure) — TWO TABS:
 *   • "Assigned tasks" — the one-off tasks assigned to the actor (grouped by job, open-first per
 *     the R1 server ordering), the Assigned-inspections section, and the Add-crew disclosure
 *     (cap.crew.create).
 *   • "Daily checklist" — the placed manager's daily checklist (DailyChecklistSection), with the
 *     R1 reason codes explaining WHY it's empty for everyone else (Mandatory A).
 *
 * Both tab panels stay MOUNTED (the inactive one is `hidden`) so each section's single fetch runs
 * once and the daily section can report its instance up for the auto-switch: on first load, an
 * actor with a daily instance and no open one-off tasks lands on the Daily tab.
 *
 * R2 never-silent hardening (Mandatory B): the tasks fetch has distinct loading / error+Retry /
 * empty states (error and empty mutually exclusive); `linked:false` (R1) explains the roster-link
 * gap instead of a lying "no tasks"; status changes are optimistic per-row (busy + inline feedback
 * scoped to the row) with contextual Start / Done / Reopen buttons per the portal button standard.
 *
 * Refresh: a header Refresh control + visibilitychange/focus refetch bump `refreshToken`, which
 * re-runs the page's own tasks fetch and every section's fetch (day-rollover recovery lives in
 * DailyChecklistSection).
 *
 * "Assigned to me" is resolved server-side via the personnel↔account link. The cap gates here are
 * a CONVENIENCE — the Worker re-gates every read + status write (Invariant 2).
 */
export function FieldOpsMyTasks({
  onBack,
  onOpenForm,
}: {
  onBack: () => void;
  onOpenForm?: (p: FormPrefill) => void;
}) {
  const { user } = useAuth();
  const caps = user?.capabilities ?? [];
  const canOwn = caps.includes("cap.tasks.own");
  const canCreateCrew = caps.includes("cap.crew.create");

  const [tab, setTab] = useState<Tab>("assigned");
  const [refreshToken, setRefreshToken] = useState(0);
  const [resp, setResp] = useState<api.MyTasksResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyIds, setBusyIds] = useState<ReadonlySet<number>>(new Set());
  const [rowMsgs, setRowMsgs] = useState<Record<number, RowFeedback>>({});
  // What the daily section last loaded — drives the one-time auto-tab-switch + the Add-crew
  // placement hint. Reported up via DailyChecklistSection's onLoaded.
  const [dailyInfo, setDailyInfo] = useState<{
    instance: checklist.DailyInstance | null;
    reason: checklist.DailyEmptyReason | null;
  } | null>(null);
  const autoSwitched = useRef(false);
  const lastWakeRef = useRef(0);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setResp(await api.fetchMyTasks());
    } catch (err) {
      // Never silent (Mandatory B): with no data yet → error+Retry block; with data on screen →
      // refresh warn, previous list stays up.
      setError(errMsg(err, "Failed to load your tasks."));
    } finally {
      setLoading(false);
    }
  }

  // Refresh EVERYTHING: the page's own tasks fetch + (via refreshToken) every section's fetch.
  function refreshAll() {
    setRefreshToken((t) => t + 1);
    void load();
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Day-rollover / stale-tab recovery: refetch when the tab regains visibility or focus (debounced —
  // focus + visibilitychange typically fire together).
  useEffect(() => {
    const onWake = () => {
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - lastWakeRef.current < 1500) return;
      lastWakeRef.current = now;
      refreshAll();
    };
    window.addEventListener("focus", onWake);
    document.addEventListener("visibilitychange", onWake);
    return () => {
      window.removeEventListener("focus", onWake);
      document.removeEventListener("visibilitychange", onWake);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // One-time auto-switch (judgment call, kept simple): once BOTH first loads land, an actor with a
  // daily instance and no open one-off tasks starts on the Daily tab. Never fires again (no tab
  // yanking after the user interacts).
  useEffect(() => {
    if (autoSwitched.current || !resp || !dailyInfo) return;
    autoSwitched.current = true;
    // (An explicit user tab click also sets autoSwitched — see pickTab — so a slow first load can
    // never yank the user off a tab they chose; review WARN.)
    const hasOpenTasks = resp.tasks.some((t) => t.status !== "done");
    if (dailyInfo.instance && !hasOpenTasks) setTab("daily");
  }, [resp, dailyInfo]);

  // Explicit tab choice pins the tab: the one-time auto-switch may never override it.
  function pickTab(t: "assigned" | "daily") {
    autoSwitched.current = true;
    setTab(t);
  }

  function markBusy(id: number, on: boolean) {
    setBusyIds((s) => {
      const next = new Set(s);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function setRowMsg(id: number, msg: RowFeedback | null) {
    setRowMsgs((m) => {
      const next = { ...m };
      if (msg) next[id] = msg;
      else delete next[id];
      return next;
    });
  }

  async function changeStatus(taskId: number, status: api.TaskStatus) {
    if (busyIds.has(taskId)) return;
    markBusy(taskId, true);
    setRowMsg(taskId, null);
    // Optimistic update (the local application of the mutation); revert ONLY this row on failure —
    // a whole-snapshot revert would clobber a sibling row's concurrently-landed update (review WARN).
    const prevStatus = resp?.tasks.find((t) => t.id === taskId)?.status;
    setResp((r) => (r ? { ...r, tasks: r.tasks.map((t) => (t.id === taskId ? { ...t, status } : t)) } : r));
    try {
      await api.setTaskStatus(taskId, status);
      setRowMsg(taskId, { ok: true, text: "Updated." });
    } catch (err) {
      if (prevStatus !== undefined) {
        setResp((r) => (r ? { ...r, tasks: r.tasks.map((t) => (t.id === taskId ? { ...t, status: prevStatus } : t)) } : r));
      }
      setRowMsg(taskId, { ok: false, text: errMsg(err, "Update failed.") });
    } finally {
      markBusy(taskId, false);
    }
  }

  const groups = groupByJob(resp?.tasks ?? []);

  const renderTaskRow = (t: api.MyTask) => {
    const rowBusy = busyIds.has(t.id);
    return (
      <li key={t.id}>
        <span className={statusPill(t.status)}>{statusLabel(t.status)}</span>{" "}
        <span>
          {t.description}
          <span className="dash-card__sub">
            {" "}
            · {t.assigned_by ? `Assigned by ${t.assigned_by} · ` : "Assigned "}
            {fmtEpochDate(t.created_at)}
          </span>
        </span>
        {canOwn && (
          <>
            {t.status === "open" && (
              <>
                {" "}
                <button
                  type="button"
                  className="btn btn--secondary"
                  aria-label={`Start task ${t.id}`}
                  disabled={rowBusy}
                  onClick={() => void changeStatus(t.id, "in_progress")}
                >
                  Start
                </button>
              </>
            )}
            {t.status !== "done" && (
              <>
                {" "}
                <button
                  type="button"
                  className="btn btn--primary"
                  aria-label={`Mark task ${t.id} done`}
                  disabled={rowBusy}
                  onClick={() => void changeStatus(t.id, "done")}
                >
                  Done
                </button>
              </>
            )}
            {t.status === "done" && (
              <>
                {" "}
                <button
                  type="button"
                  className="btn btn--secondary"
                  aria-label={`Reopen task ${t.id}`}
                  disabled={rowBusy}
                  onClick={() => void changeStatus(t.id, "open")}
                >
                  Reopen
                </button>
              </>
            )}
          </>
        )}
        {rowMsgs[t.id] ? <InlineRowMsg msg={rowMsgs[t.id]} /> : null}
      </li>
    );
  };

  const tasksBlock =
    loading && !resp ? (
      <SectionLoading label="Loading your tasks…" />
    ) : error && !resp ? (
      <SectionError message={error} onRetry={() => void load()} what="loading your tasks" />
    ) : !resp ? null : (
      <>
        {error && <SectionRefreshWarn message={error} onRetry={() => void load()} what="loading your tasks" />}
        {!resp.linked ? (
          <div className="dash-unavail">{ROSTER_LINK_COPY}</div>
        ) : groups.length === 0 ? (
          <div className="dash-unavail">
            No tasks are assigned to you. Tasks your crew lead or the office assigns to you will appear here.
          </div>
        ) : (
          <div className="dash-grid">
            {groups.map((g) => {
              const openTasks = g.tasks.filter((t) => t.status !== "done");
              const doneTasks = g.tasks.filter((t) => t.status === "done");
              return (
                <section key={g.job_id} className="card dash-section">
                  {/* R2: no job-navigation prop exists on this page (App owns routing — out of R2's
                      surface), so the group header renders UNLINKED. R7: link it to the Job Tracker
                      detail view once App passes a navigation callback. */}
                  <h3 className="dash-detail__h2">
                    {g.project_name ?? g.job_id}
                    <span className="dash-card__sub"> · {g.job_id}</span>
                  </h3>
                  {openTasks.length > 0 && <ul className="dash-tasklist">{openTasks.map(renderTaskRow)}</ul>}
                  <CompletedDisclosure count={doneTasks.length}>
                    <ul className="dash-tasklist">{doneTasks.map(renderTaskRow)}</ul>
                  </CompletedDisclosure>
                </section>
              );
            })}
          </div>
        )}
      </>
    );

  return (
    <PageShell onHome={onBack}>
      <div className="dash-detail__head">
        <h2 className="page__heading">My Tasks</h2>
        <button type="button" className="btn btn--secondary" aria-label="Refresh" onClick={refreshAll}>
          Refresh
        </button>
      </div>
      <p className="dash__intro">
        Your assigned work — one-off tasks and inspections under Assigned tasks; placed crew leads also get a
        Daily checklist.
      </p>

      {/* Tab strip — the admin-nav banner-extension pattern (.admin-tabs), same look site-wide. */}
      <nav className="admin-tabs" role="tablist" aria-label="My Tasks sections">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "assigned"}
          className={`admin-tabs__tab${tab === "assigned" ? " admin-tabs__tab--active" : ""}`}
          onClick={() => pickTab("assigned")}
        >
          Assigned tasks
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "daily"}
          className={`admin-tabs__tab${tab === "daily" ? " admin-tabs__tab--active" : ""}`}
          onClick={() => pickTab("daily")}
        >
          Daily checklist
        </button>
      </nav>

      {/* Both panels stay mounted (inactive hidden) — single fetch per section + the daily section
          reports its instance up for the auto-switch even while its tab is inactive. */}
      <div role="tabpanel" aria-label="Assigned tasks" hidden={tab !== "assigned"}>
        {tasksBlock}

        {/* S6 — admin-assigned inspection checklists (renders nothing when confirmed-empty). */}
        <AssignedInspectionsSection onOpenForm={onOpenForm} refreshToken={refreshToken} />

        {/* Slice T — a subcontractor adds field-only crew, auto-placed on their current job. */}
        {canCreateCrew && (
          <AddCrewSection
            placementJob={dailyInfo?.instance?.job_id ?? null}
            placementProject={dailyInfo?.instance?.project_name ?? null}
          />
        )}
      </div>

      <div role="tabpanel" aria-label="Daily checklist" hidden={tab !== "daily"}>
        {/* S3/S4 — the placed manager's daily checklist; reason-coded empty states for everyone else. */}
        <DailyChecklistSection onOpenForm={onOpenForm} refreshToken={refreshToken} onLoaded={setDailyInfo} />
      </div>
    </PageShell>
  );
}
