// SINGLE-SOURCE WIRE TYPES — the Worker's JSON response shapes for the field-ops read surfaces
// the SPA consumes (optimization slice 3, finding #11). TYPE-ONLY module, importable from BOTH
// tsconfig scopes: the Worker types its `c.json` payloads with these (so a route edit that drifts
// a shape fails `tsc -p tsconfig.worker.json`), and the SPA libs re-export them for their callers
// and test fixtures (so the DailyReportTab fixture type-checks against the shape the Worker
// actually sends, not a hand-maintained copy). No framework, no codegen — just one definition.
//
// Covered endpoints:
//   • GET /api/fieldops/jobs                    → JobListResponse        (fieldops_jobtracker.ts)
//   • GET /api/fieldops/jobs/:job_id            → JobDetailResponse      (fieldops_jobtracker.ts)
//   • GET /api/fieldops/daily-form/status       → DailyFormStatus        (fieldops_daily_requirements.ts)
//   • GET /api/fieldops/daily-form/requirements → DailyRequirementsResponse (fieldops_daily_requirements.ts)
//   • GET /api/fieldops/expected-materials      → ExpectedMaterialsResponse (fieldops_expected_materials.ts)
//   • GET /api/fieldops/checklist/assigned      → AssignedInspectionsResponse (fieldops_checklist.ts)
//   • GET /api/fieldops/tasks/mine              → MyTasksResponse        (fieldops_tasks.ts)
//
// SPA re-export homes: src/lib/fieldops_jobtracker.ts, src/lib/fieldops_daily_form.ts,
// src/lib/fieldops_expected_materials.ts, src/lib/fieldops_checklist.ts, src/lib/fieldops_tasks.ts.

// ── GET /api/fieldops/jobs (job-tracker LIST) ────────────────────────────────────────────────────

export interface CrewMember {
  id: number;
  name: string;
  trade: string | null;
}

/** (R7) Detail crew row: + the linked account's role so pickers can pre-disable task-assign
 *  options the Worker's subcontractor-target guard will 403 (an assign-only manager may only
 *  target 'submitter'-linked personnel; no login → null → also rejected). Presentation only —
 *  the Worker re-gates; non-assigners receive null. */
export interface DetailCrewMember extends CrewMember {
  account_role: string | null;
}

export interface OpenTask {
  id: number;
  description: string;
  status: string;
  personnel_name: string | null;
}

export interface JobRow {
  job_id: string;
  project_name: string;
  status: string;
  progress: number;
  client_name: string | null;
  crew: CrewMember[];
  open_tasks: OpenTask[];
}

export interface JobListResponse {
  jobs: JobRow[];
  next_cursor: string | null;
  /** (R7) Where the viewer's own linked roster row is placed — drives the "Your job" list badge.
   *  null = unlinked/unplaced. Optional for back-compat; the live worker always sends it. */
  viewer_current_job?: string | null;
}

// ── GET /api/fieldops/jobs/:job_id (job-tracker DETAIL) ─────────────────────────────────────────

export interface Task {
  id: number;
  description: string;
  status: string;
  created_at: number;
  personnel_id: number | null;
  personnel_name: string | null;
}

export interface JobTimeEntry {
  uuid: string;
  hours: number | null;
  work_started_at: number | null;
  work_ended_at: number | null;
  recorded_at: number;
  notes: string | null;
  personnel_name: string | null;
  /** (R7) The task the entry was logged against (task_assignments.description); null = job-level. */
  task_id: number | null;
  task_description: string | null;
  /** (R7) WHO CREATED the entry — the write's actor_username stamp resolved to the roster display
   *  name. Display name ONLY (R1 W9 posture) — null when the recorder has no roster row; never a
   *  raw username. */
  recorded_by_name: string | null;
}

export interface EquipmentOnSite {
  id: number;
  name: string;
  kind: string | null;
  identifier: string | null;
  label: string | null;
  read_at: number | null;
}

export interface JobInspection {
  uuid: string;
  form_code: string;
  version: number;
  performed_at: number | null;
  recorded_at: number;
  equipment_name: string | null;
}

export interface JobClient {
  name: string;
  contact: string | null;
  phone: string | null;
  email: string | null;
}

export interface JobDetail {
  job_id: string;
  project_name: string;
  status: string;
  progress: number;
  client: JobClient | null;
  crew: DetailCrewMember[];
  tasks: Task[];
  time_entries: JobTimeEntry[];
  equipment_on_site: EquipmentOnSite[];
  inspections: JobInspection[];
}

/** (R7) The session user's own linked ACTIVE roster row — backs the log-time "Me (<name>)"
 *  default. null = no linked personnel (the form says so instead of guessing). */
export interface ViewerPersonnel {
  id: number;
  name: string;
}

export interface JobDetailResponse {
  job: JobDetail;
  cursors: { tasks: string | null; time: string | null; insp: string | null };
  /** Optional for back-compat with cached/older responses; the live worker always sends it. */
  viewer_personnel?: ViewerPersonnel | null;
}

// ── GET /api/fieldops/daily-form/status ─────────────────────────────────────────────────────────

/** The latest submission for one parent-form family on (job, date). `filed_by_name` is the
 *  personnel DISPLAY NAME resolved through submitted_as — NULL when the account has no roster
 *  link (never a raw username; the W9 posture — the UI drops the "by …" clause on NULL). */
export interface FiledEntry {
  filed_at: number; // epoch seconds (submissions.created_at)
  filed_by_name: string | null;
}

/** GET /api/fieldops/daily-form/status response. `filed` is keyed by PARENT form family (the
 *  DAILY_STATUS_FAMILIES set — src/shared/daily_families.ts) — a family with no submission for
 *  (job, date) is simply absent. `daily_filed` mirrors filed["daily-report"] (the banner's key). */
export interface DailyFormStatus {
  filed: Record<string, FiledEntry>;
  daily_filed: FiledEntry | null;
}

// ── GET /api/fieldops/daily-form/requirements ───────────────────────────────────────────────────

/** The closed requirement-item vocabulary (D1 job_daily_requirements.kind, migration 0030;
 *  number/date/select added by 0032 — slice D5). */
export type DailyRequirementKind =
  | "note"
  | "confirm"
  | "text"
  | "form_link"
  | "number"
  | "date"
  | "select";

/** One admin-authored per-job requirement item, as served by
 *  GET /api/fieldops/daily-form/requirements (active items only, seq order, bounded). */
export interface DailyRequirementItem {
  id: number;
  seq: number;
  kind: DailyRequirementKind;
  label: string;
  form_code: string | null; // form_link only: a catalog PARENT family code
  options: string[] | null; // select only: the pick-one choices (D1 stores JSON; served PARSED)
}

export interface DailyRequirementsResponse {
  job_id: string;
  items: DailyRequirementItem[];
}

// ── GET /api/fieldops/expected-materials ────────────────────────────────────────────────────────

export type ExpectedMaterialStatus = "expected" | "received" | "incident";

export interface ExpectedMaterialRow {
  id: number;
  material_id: number | null; // catalog-picked rows; null = free-text
  material_name: string | null; // resolved catalog model_id (display; null for free-text rows)
  description: string | null;
  qty: number | null;
  unit: string | null;
  expected_date: string | null; // YYYY-MM-DD
  status: ExpectedMaterialStatus;
  received_at: number | null; // epoch seconds, stamped by receive/flag-incident
  received_by_name: string | null; // DISPLAY NAME ONLY (W9) — null when the account has no roster link
  qty_received: number | null;
  note: string | null;
  seq: number;
}

export interface ExpectedMaterialsResponse {
  expected_materials: ExpectedMaterialRow[];
}

// ── GET /api/fieldops/checklist/assigned ────────────────────────────────────────────────────────

export type ChecklistItemStatus = "open" | "done";

/** One per-instance item state (the snapshot + completion row, migration 0026
 *  checklist_item_states). `filed_by` — WHO filed the submission that auto-closed this item
 *  (completed_by === '(auto)'): the personnel DISPLAY NAME only (W9 — no raw-username fallback);
 *  NULL for manually-completed / still-open items, or when no matching submission resolves
 *  (best-effort attribution). */
export interface ChecklistItemState {
  id: number;
  source_item_id: number | null;
  item_type: string;
  label: string | null;
  form_code: string | null;
  target_count: number | null;
  status: ChecklistItemStatus;
  note: string | null;
  photo_ref: string | null;
  completed_by: string | null;
  completed_at: number | null;
  value_num: number | null;
  filed_by: string | null;
}

export interface AssignedInstance {
  id: number;
  job_id: string | null;
  project_name: string | null;
  instance_date: string | null;
  status: "open" | "complete";
  /** (R1) The assigned template's title, SNAPSHOTTED at assign time (migration 0029) — render
   *  this, never "Inspection #<id>". NULL only on legacy instances the backfill couldn't resolve. */
  template_title: string | null;
  created_at: number;
}

export interface AssignedInspection {
  instance: AssignedInstance;
  items: ChecklistItemState[];
}

/** (R1) `linked` = whether the session has an ACTIVE linked personnel row — an unlinked account
 *  CANNOT have assignments, so the UI can explain the empty list. Instances arrive OPEN-FIRST
 *  (server CASE ordering), newest first within a status band. */
export interface AssignedInspectionsResponse {
  inspections: AssignedInspection[];
  linked: boolean;
}

// ── GET /api/fieldops/tasks/mine ────────────────────────────────────────────────────────────────

/** One of the caller's own assigned tasks (cap.tasks.own; resolved server-side through the
 *  session's linked personnel row). `assigned_by` = who last placed the task (actor username,
 *  stamped by the create/assign routes; NULL on pre-stamping historical rows). */
export interface MyTask {
  id: number;
  job_id: string;
  project_name: string | null;
  description: string;
  status: string;
  created_at: number;
  assigned_by: string | null;
}

/** (CS4 #12) The viewer's OWN current placement, resolved server-side from their linked ACTIVE
 *  personnel row (personnel.current_job → jobs). SELF-INFORMATION ONLY: `personnel_id`/`name` are
 *  the caller's own roster row (their own display name — the W9 posture), `job_id`/`project_name`
 *  their own standing placement; nothing about any other account or person rides this shape.
 *  null = unlinked OR linked-but-unplaced (disambiguate with `linked`). `project_name` is null
 *  only when the placement names a job the jobs table no longer carries (soft ref). */
export interface ViewerTaskPlacement {
  job_id: string;
  project_name: string | null;
  personnel_id: number;
  name: string;
}

/** GET /api/fieldops/tasks/mine. Tasks arrive OPEN-FIRST (open < in_progress < done, newest first
 *  within a band — server CASE ordering). `linked` = whether the session has an ACTIVE linked
 *  personnel row (an unlinked account CANNOT have tasks — the UI explains the empty list).
 *  `viewer_placement` collapses the Daily tab's placement waterfall: the tab used to derive its
 *  job from a full jobs-list page (fetchJobList → viewer_current_job); now the one endpoint it
 *  already reads carries the placement. */
export interface MyTasksResponse {
  tasks: MyTask[];
  linked: boolean;
  viewer_placement: ViewerTaskPlacement | null;
}
