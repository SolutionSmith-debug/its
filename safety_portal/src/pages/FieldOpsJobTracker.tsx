import { useState, useEffect } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_jobtracker";
import { fetchPersonnelList, assignPersonnel, type PersonnelRow } from "../lib/fieldops_personnel";
import { fetchEquipmentList, moveEquipment } from "../lib/fieldops_equipment";
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

const STATUS_OPTIONS: { value: api.JobStatusFilter; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "closed", label: "Closed" },
  { value: "on_hold", label: "On hold" },
  { value: "all", label: "All" },
];

const MAX_CC = 5; // mirrors each Active-Jobs sheet's CC 1..5 columns (worker re-enforces)

// ── Routing SoR form block (P2.5 Slice 2) ────────────────────────────────────────────────────────
// The create form OWNS the full job source-of-truth; the contacts-edit form re-sends it. Both reuse
// <RoutingFields/>. Local form-state keeps every field a controlled string / string[]; routingPayload
// trims + drops empties so an UNTOUCHED routing block adds no keys to the create body (keeping the
// minimal-create contract byte-identical) and is the full intended routing for an edit.
interface RoutingForm {
  address: string;
  stakeholder_name: string;
  stakeholder_email: string;
  stakeholder_phone: string;
  safety_contact_name: string;
  safety_contact_email: string;
  safety_cc: string[];
  progress_contact_name: string;
  progress_contact_email: string;
  progress_cc: string[];
}

const EMPTY_ROUTING: RoutingForm = {
  address: "",
  stakeholder_name: "",
  stakeholder_email: "",
  stakeholder_phone: "",
  safety_contact_name: "",
  safety_contact_email: "",
  safety_cc: [],
  progress_contact_name: "",
  progress_contact_email: "",
  progress_cc: [],
};

function routingPayload(r: RoutingForm): api.JobRouting {
  const out: Record<string, unknown> = {};
  const scalars: [string, string][] = [
    ["address", r.address],
    ["stakeholder_name", r.stakeholder_name],
    ["stakeholder_email", r.stakeholder_email],
    ["stakeholder_phone", r.stakeholder_phone],
    ["safety_contact_name", r.safety_contact_name],
    ["safety_contact_email", r.safety_contact_email],
    ["progress_contact_name", r.progress_contact_name],
    ["progress_contact_email", r.progress_contact_email],
  ];
  for (const [k, v] of scalars) {
    const t = v.trim();
    if (t) out[k] = t;
  }
  const safetyCc = r.safety_cc.map((s) => s.trim()).filter(Boolean);
  const progressCc = r.progress_cc.map((s) => s.trim()).filter(Boolean);
  if (safetyCc.length) out.safety_cc = safetyCc;
  if (progressCc.length) out.progress_cc = progressCc;
  return out as api.JobRouting;
}

// CC editor: up to MAX_CC email rows, each independently editable / removable.
function CcEditor({ label, ccs, onChange }: { label: string; ccs: string[]; onChange: (next: string[]) => void }) {
  return (
    <div className="dash-row" role="group" aria-label={label}>
      <span className="dash-card__label">{label} (≤{MAX_CC}):</span>{" "}
      {ccs.map((cc, i) => (
        <span key={i}>
          <input
            aria-label={`${label} ${i + 1}`}
            value={cc}
            placeholder="email@example.com"
            maxLength={320}
            onChange={(e) => onChange(ccs.map((c, j) => (j === i ? e.target.value : c)))}
          />{" "}
          <button
            type="button"
            className="btn--ghost"
            aria-label={`Remove ${label} ${i + 1}`}
            onClick={() => onChange(ccs.filter((_, j) => j !== i))}
          >
            ✕
          </button>{" "}
        </span>
      ))}
      <button
        type="button"
        className="btn--secondary"
        aria-label={`Add ${label}`}
        disabled={ccs.length >= MAX_CC}
        onClick={() => onChange([...ccs, ""])}
      >
        + Add CC
      </button>
    </div>
  );
}

// The full routing block: address, stakeholder, a Safety Reports block + a Progress Reports block
// (each contact name/email + CC editor), and a "Same as safety" copy button. After a copy the
// progress block stays INDEPENDENTLY editable (it's plain form state).
function RoutingFields({ routing, onChange }: { routing: RoutingForm; onChange: (next: RoutingForm) => void }) {
  const set = (patch: Partial<RoutingForm>) => onChange({ ...routing, ...patch });
  return (
    <>
      <div className="dash-row">
        <input
          value={routing.address}
          onChange={(e) => set({ address: e.target.value })}
          placeholder="Job address (optional)"
          maxLength={512}
        />
      </div>
      <fieldset className="dash-section" aria-label="Stakeholder">
        <legend className="dash-card__label">Stakeholder</legend>
        <div className="dash-row">
          <input value={routing.stakeholder_name} onChange={(e) => set({ stakeholder_name: e.target.value })} placeholder="Stakeholder name" maxLength={256} />{" "}
          <input value={routing.stakeholder_email} onChange={(e) => set({ stakeholder_email: e.target.value })} placeholder="Stakeholder email" maxLength={320} />{" "}
          <input value={routing.stakeholder_phone} onChange={(e) => set({ stakeholder_phone: e.target.value })} placeholder="Stakeholder phone" maxLength={40} />
        </div>
      </fieldset>
      <fieldset className="dash-section" aria-label="Safety Reports">
        <legend className="dash-card__label">Safety Reports</legend>
        <div className="dash-row">
          <input value={routing.safety_contact_name} onChange={(e) => set({ safety_contact_name: e.target.value })} placeholder="Safety contact name" maxLength={256} />{" "}
          <input value={routing.safety_contact_email} onChange={(e) => set({ safety_contact_email: e.target.value })} placeholder="Safety contact email" maxLength={320} />
        </div>
        <CcEditor label="Safety CC" ccs={routing.safety_cc} onChange={(safety_cc) => set({ safety_cc })} />
      </fieldset>
      <fieldset className="dash-section" aria-label="Progress Reports">
        <legend className="dash-card__label">Progress Reports</legend>
        <div className="dash-row">
          <button
            type="button"
            className="btn--secondary"
            onClick={() =>
              set({
                progress_contact_name: routing.safety_contact_name,
                progress_contact_email: routing.safety_contact_email,
                progress_cc: [...routing.safety_cc],
              })
            }
          >
            Same as safety
          </button>
        </div>
        <div className="dash-row">
          <input value={routing.progress_contact_name} onChange={(e) => set({ progress_contact_name: e.target.value })} placeholder="Progress contact name" maxLength={256} />{" "}
          <input value={routing.progress_contact_email} onChange={(e) => set({ progress_contact_email: e.target.value })} placeholder="Progress contact email" maxLength={320} />
        </div>
        <CcEditor label="Progress CC" ccs={routing.progress_cc} onChange={(progress_cc) => set({ progress_cc })} />
      </fieldset>
    </>
  );
}

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
  const canManage = caps.includes("cap.jobtracker.manage"); // create / close / add-task / reassign-task
  const canOwnTasks = caps.includes("cap.tasks.own"); // change a task's own status
  const canLogTime = caps.includes("cap.time.log"); // log a time entry against the open job
  // Unified job-create flow: per-control caps (convenience — the Worker re-gates every call). A
  // manager (P2.6) holds crew.assign + equipment.field but NOT jobtracker.manage → can place crew /
  // move equipment on a job WITHOUT the add-task control (which stays under canManage).
  const canAssignCrew = caps.includes("cap.crew.assign"); // place / remove crew on this job
  const canFieldEquip = caps.includes("cap.equipment.field"); // move a piece of equipment to this job
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // New-job form (list view)
  const [newJobName, setNewJobName] = useState("");
  const [newJobClient, setNewJobClient] = useState("");
  const [newJobOpen, setNewJobOpen] = useState(false);
  const [createRouting, setCreateRouting] = useState<RoutingForm>(EMPTY_ROUTING); // P2.5 routing SoR
  // Detail manage controls
  const [taskDesc, setTaskDesc] = useState("");
  const [taskPerson, setTaskPerson] = useState(""); // add-task assignee (personnel id, "" = unassigned)
  // Detail lifecycle selector + routing/contacts editor (P2.5)
  const [lifecycleSel, setLifecycleSel] = useState<api.JobLifecycle>("active");
  const [editContactsOpen, setEditContactsOpen] = useState(false);
  const [editRouting, setEditRouting] = useState<RoutingForm>(EMPTY_ROUTING);
  // Time-log form (detail)
  const [logHours, setLogHours] = useState("");
  const [logNotes, setLogNotes] = useState("");
  const [logTask, setLogTask] = useState("");
  const [logPerson, setLogPerson] = useState(""); // log-time subject (personnel id, "" = me/unassigned)
  // Unified job-create flow (Slice 2/3): assign-crew + assign-equipment pickers on the detail view,
  // plus the one-shot "finish setting up" nudge shown when a create routes into the new job's detail.
  const [crewOpts, setCrewOpts] = useState<PersonnelRow[]>([]);
  const [equipOpts, setEquipOpts] = useState<{ id: number; name: string; identifier: string | null }[]>([]);
  const [crewToAdd, setCrewToAdd] = useState("");
  const [equipToAdd, setEquipToAdd] = useState("");
  const [setupBanner, setSetupBanner] = useState<string | null>(null);

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

  // Load the crew + equipment pickers when the detail view is open and the actor can assign. These
  // are the full active roster / equipment set (not job-scoped); reloaded after each mutation so the
  // "already placed here" exclusion stays current. A load failure falls back to an empty option list
  // (the control still renders) — it never blocks the detail view.
  async function reloadPickers() {
    if (canAssignCrew) {
      try {
        setCrewOpts((await fetchPersonnelList()).personnel);
      } catch {
        setCrewOpts([]);
      }
    }
    if (canFieldEquip) {
      try {
        const r = await fetchEquipmentList();
        setEquipOpts(r.equipment.map((eq) => ({ id: eq.id, name: eq.name, identifier: eq.identifier })));
      } catch {
        setEquipOpts([]);
      }
    }
  }

  useEffect(() => {
    if (view === "detail" && selectedJob && (canAssignCrew || canFieldEquip)) void reloadPickers();
    // reloadPickers is recreated each render and reads the current caps; job_id keys the reload.
  }, [view, selectedJob?.job_id, canAssignCrew, canFieldEquip]);

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
    setEditContactsOpen(false);
    setEditRouting(EMPTY_ROUTING);
    setSetupBanner(null); // opening an existing job from the list is not the create-nudge path
    setCrewToAdd("");
    setEquipToAdd("");
    api
      .fetchJobDetail(job.job_id)
      .then((res) => {
        setSelectedJob(res.job);
        // Seed the lifecycle selector from the legacy status (detail carries no lifecycle field):
        // active → 'active', anything else → 'inactive'. An explicit selector change overrides it.
        setLifecycleSel(res.job.status === "active" ? "active" : "inactive");
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
      setSetupBanner(null);
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
    const projectName = newJobName.trim();
    if (!projectName) {
      setActionMsg({ ok: false, text: "Project name is required." });
      return;
    }
    setActionBusy(true);
    setActionMsg(null);
    try {
      const clientName = newJobClient.trim();
      // Slice 6: no job_id in the body — the worker assigns the next JOB-###### and returns it.
      const created = await api.createJob({
        project_name: projectName,
        ...(clientName ? { new_client: { name: clientName } } : {}),
        ...routingPayload(createRouting),
      });
      setNewJobName("");
      setNewJobClient("");
      setCreateRouting(EMPTY_ROUTING);
      setNewJobOpen(false);
      await reloadList();
      // Slice 3: route into the new job's detail with a one-shot "finish setting up" nudge so the
      // office immediately assigns crew / equipment / tasks. If the detail fetch fails, stay on the
      // list — the job is created and the success toast still shows.
      try {
        const res = await api.fetchJobDetail(created.job_id);
        setSelectedJob(res.job);
        setLifecycleSel(res.job.status === "active" ? "active" : "inactive");
        setTaskCursor(res.cursors.tasks);
        setTimeCursor(res.cursors.time);
        setInspCursor(res.cursors.insp);
        setEditContactsOpen(false);
        setEditRouting(EMPTY_ROUTING);
        setCrewToAdd("");
        setEquipToAdd("");
        setSetupBanner(created.job_id);
        setView("detail");
      } catch {
        /* detail fetch failed — stay on the list; the job was still created */
      }
      setActionMsg({ ok: true, text: `Job ${created.job_id} created.` });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Create failed." });
    } finally {
      setActionBusy(false);
    }
  }

  // Lifecycle selector (P2.5) — replaces the bare Close button. Setting it explicitly persists the
  // chosen value through the reload (which would otherwise re-derive only active/inactive from status).
  async function submitLifecycle(lifecycle: api.JobLifecycle) {
    if (!selectedJob || actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.setLifecycle(selectedJob.job_id, lifecycle);
      setLifecycleSel(lifecycle);
      await reloadDetail();
      setActionMsg({ ok: true, text: `Lifecycle set to ${lifecycle}.` });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Lifecycle update failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitEditContacts(e: FormEvent) {
    e.preventDefault();
    if (!selectedJob || actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.editContacts(selectedJob.job_id, routingPayload(editRouting));
      setEditContactsOpen(false);
      setEditRouting(EMPTY_ROUTING);
      await reloadDetail();
      setActionMsg({ ok: true, text: "Routing / contacts updated." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Routing update failed." });
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
    const personnelId = taskPerson === "" ? undefined : Number(taskPerson);
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.addTask(selectedJob.job_id, {
        description,
        ...(personnelId !== undefined ? { personnel_id: personnelId } : {}),
      });
      setTaskDesc("");
      setTaskPerson("");
      await reloadDetail();
      setActionMsg({ ok: true, text: "Task added." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Add task failed." });
    } finally {
      setActionBusy(false);
    }
  }

  // (Re)assign or clear a task's assignee (cap.jobtracker.manage). Options are the job's placed crew.
  async function submitReassignTask(taskId: number, personId: number | null) {
    if (!selectedJob || actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.reassignTask(taskId, personId);
      await reloadDetail();
      setActionMsg({ ok: true, text: personId === null ? "Task unassigned." : "Task reassigned." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Reassign failed." });
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
    const personnelId = logPerson === "" ? undefined : Number(logPerson);
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.logTime({
        uuid: crypto.randomUUID(), // client-generated idempotency key (integrity-bar)
        job_id: selectedJob.job_id,
        ...(hours !== undefined ? { hours } : {}),
        ...(taskId !== undefined ? { task_id: taskId } : {}),
        ...(personnelId !== undefined ? { personnel_id: personnelId } : {}),
        ...(logNotes.trim() ? { notes: logNotes.trim() } : {}),
      });
      setLogHours("");
      setLogNotes("");
      setLogTask("");
      setLogPerson("");
      await reloadDetail();
      setActionMsg({ ok: true, text: "Time logged." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Time log failed." });
    } finally {
      setActionBusy(false);
    }
  }

  // ── Unified job-create flow: assign crew + equipment to the open job (Slice 2) ───────────────────
  // Reuse the P2.6 assignPersonnel (cap.crew.assign) and the equipment move (cap.equipment.field)
  // routes — already security-reviewed. Each reloads the detail (crew / equipment_on_site reflect
  // the change via the Slice-1 crew-convergence + the equipment-on-site leg) and the pickers.
  async function submitAssignCrew(e: FormEvent) {
    e.preventDefault();
    if (!selectedJob || actionBusy) return;
    const pid = Number(crewToAdd);
    if (crewToAdd === "" || !Number.isInteger(pid)) {
      setActionMsg({ ok: false, text: "Select a crew member to place on this job." });
      return;
    }
    setActionBusy(true);
    setActionMsg(null);
    try {
      await assignPersonnel(pid, selectedJob.job_id);
      setCrewToAdd("");
      await reloadDetail();
      await reloadPickers();
      setActionMsg({ ok: true, text: "Crew member placed on this job." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Assign failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function removeCrew(personId: number) {
    if (!selectedJob || actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await assignPersonnel(personId, null); // clear the placement (unassign)
      await reloadDetail();
      await reloadPickers();
      setActionMsg({ ok: true, text: "Crew member removed from this job." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Remove failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitAssignEquip(e: FormEvent) {
    e.preventDefault();
    if (!selectedJob || actionBusy) return;
    const eid = Number(equipToAdd);
    if (equipToAdd === "" || !Number.isInteger(eid)) {
      setActionMsg({ ok: false, text: "Select a piece of equipment to move to this job." });
      return;
    }
    setActionBusy(true);
    setActionMsg(null);
    try {
      await moveEquipment(eid, { job_id: selectedJob.job_id });
      setEquipToAdd("");
      await reloadDetail();
      await reloadPickers();
      setActionMsg({ ok: true, text: "Equipment moved to this job." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Move failed." });
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

        {setupBanner === job.job_id && (
          <section className="card dash-section" aria-label="Finish setting up job">
            <strong>Finish setting up {job.job_id}</strong> — assign crew, equipment, and tasks below to
            get this job ready.{" "}
            <button type="button" className="btn--secondary" onClick={() => setSetupBanner(null)}>Done</button>
          </section>
        )}

        {actionMsg && (
          <p className="muted" style={{ color: actionMsg.ok ? "green" : "red" }}>{actionMsg.text}</p>
        )}

        {canManage && (
          <section className="card dash-section">
            <h3 className="dash-detail__h2">Manage job</h3>
            <form onSubmit={submitAddTask} className="dash-row" aria-label="Add a task">
              <input
                value={taskDesc}
                onChange={(e) => setTaskDesc(e.target.value)}
                placeholder="New task description"
                maxLength={256}
              />{" "}
              <label className="dash-card__label">
                Assign to:{" "}
                <select value={taskPerson} onChange={(e) => setTaskPerson(e.target.value)} aria-label="Assign new task to">
                  <option value="">— unassigned —</option>
                  {job.crew.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </label>{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Add task</button>
            </form>
            <form className="dash-row" aria-label="Set job lifecycle">
              <label className="dash-card__label">
                Lifecycle:{" "}
                <select
                  aria-label="Job lifecycle"
                  value={lifecycleSel}
                  disabled={actionBusy}
                  onChange={(e) => submitLifecycle(e.target.value as api.JobLifecycle)}
                >
                  <option value="active">Active</option>
                  <option value="inactive">Inactive</option>
                  <option value="archived">Archived</option>
                </select>
              </label>
            </form>
            <div className="dash-row">
              {editContactsOpen ? (
                <form onSubmit={submitEditContacts} aria-label="Edit routing and contacts">
                  <RoutingFields routing={editRouting} onChange={setEditRouting} />
                  <div className="dash-row">
                    <button type="submit" disabled={actionBusy} className="btn--primary">Save routing</button>{" "}
                    <button type="button" onClick={() => setEditContactsOpen(false)} className="btn--secondary">Cancel</button>
                  </div>
                </form>
              ) : (
                <button type="button" onClick={() => setEditContactsOpen(true)} className="btn--edit">
                  Edit routing / contacts
                </button>
              )}
            </div>
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
          <h3 className="dash-detail__h2">
            Assigned crew ({job.crew.length})
            {setupBanner === job.job_id && job.crew.length === 0 && (
              <span className="dash-pill dash-pill--warn"> needs crew</span>
            )}
          </h3>
          {job.crew.length ? (
            <div className="dash-chips">
              {job.crew.map((p) => (
                <span className="dash-chip" key={p.id}>
                  {p.name}{p.trade ? ` · ${p.trade}` : ""}
                  {canAssignCrew && (
                    <>
                      {" "}
                      <button
                        type="button"
                        className="btn--ghost"
                        aria-label={`Remove ${p.name} from crew`}
                        disabled={actionBusy}
                        onClick={() => removeCrew(p.id)}
                      >
                        ✕
                      </button>
                    </>
                  )}
                </span>
              ))}
            </div>
          ) : (
            <div className="dash-unavail">No crew assigned.</div>
          )}
          {canAssignCrew && (
            <form onSubmit={submitAssignCrew} className="dash-row" aria-label="Assign crew to job">
              <select
                value={crewToAdd}
                onChange={(ev) => setCrewToAdd(ev.target.value)}
                aria-label="Crew member to place"
              >
                <option value="">— select crew member —</option>
                {crewOpts
                  .filter((p) => p.current_job !== job.job_id)
                  .map((p) => (
                    <option key={p.id} value={p.id}>{p.name}{p.trade ? ` · ${p.trade}` : ""}</option>
                  ))}
              </select>{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Add to crew</button>
            </form>
          )}
        </section>

        <section className="card dash-section">
          <h3 className="dash-detail__h2">Tasks</h3>
          {job.tasks.length ? (
            <ul className="dash-tasklist">
              {job.tasks.map((t) => (
                <li key={t.id}>
                  <span className={taskPillClass(t.status)}>{t.status}</span> {t.description}
                  {t.personnel_name && !canManage ? <span className="muted"> — {t.personnel_name}</span> : null}
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
                  {canManage && (
                    <>
                      {" "}
                      <select
                        aria-label={`Assign task ${t.id}`}
                        value={t.personnel_id != null ? String(t.personnel_id) : ""}
                        disabled={actionBusy}
                        onChange={(e) => submitReassignTask(t.id, e.target.value === "" ? null : Number(e.target.value))}
                      >
                        <option value="">— unassigned —</option>
                        {job.crew.map((p) => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                        {t.personnel_id != null && !job.crew.some((p) => p.id === t.personnel_id) && (
                          <option value={t.personnel_id}>{t.personnel_name ?? `#${t.personnel_id}`}</option>
                        )}
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
                For:{" "}
                <select value={logPerson} onChange={(e) => setLogPerson(e.target.value)} aria-label="Log time for">
                  <option value="">— me / unassigned —</option>
                  {job.crew.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </label>{" "}
              <label className="dash-card__label">
                Task:{" "}
                <select value={logTask} onChange={(e) => setLogTask(e.target.value)} aria-label="Log time task">
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
          <h3 className="dash-detail__h2">
            Equipment on site ({job.equipment_on_site.length})
            {setupBanner === job.job_id && job.equipment_on_site.length === 0 && (
              <span className="dash-pill dash-pill--warn"> needs equipment</span>
            )}
          </h3>
          {job.equipment_on_site.length ? (
            <div className="dash-chips">
              {job.equipment_on_site.map((e) => (
                <span className="dash-chip" key={e.id}>{e.name}{e.identifier ? ` · ${e.identifier}` : ""}</span>
              ))}
            </div>
          ) : (
            <div className="dash-unavail">No equipment on site.</div>
          )}
          {canFieldEquip && (
            <form onSubmit={submitAssignEquip} className="dash-row" aria-label="Assign equipment to job">
              <select
                value={equipToAdd}
                onChange={(ev) => setEquipToAdd(ev.target.value)}
                aria-label="Equipment to move here"
              >
                <option value="">— select equipment —</option>
                {equipOpts.map((eq) => (
                  <option key={eq.id} value={eq.id}>{eq.name}{eq.identifier ? ` · ${eq.identifier}` : ""}</option>
                ))}
              </select>{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Move here</button>
            </form>
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
            <form onSubmit={submitNewJob} aria-label="Create job">
              <p className="dash-card__sub muted">A Job ID (JOB-######) is assigned automatically on create.</p>
              <div className="dash-row">
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
                />
              </div>
              <RoutingFields routing={createRouting} onChange={setCreateRouting} />
              <div className="dash-row">
                <button type="submit" disabled={actionBusy} className="btn--primary">Create</button>{" "}
                <button
                  type="button"
                  onClick={() => {
                    setNewJobOpen(false);
                    setCreateRouting(EMPTY_ROUTING);
                  }}
                  className="btn--secondary"
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <button onClick={() => setNewJobOpen(true)} className="btn--primary">+ New job</button>
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
