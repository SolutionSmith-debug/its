# P2.2 Field-Ops READ-views — design + build briefs (workflow-authored, scalability-first)

I have enough verified ground truth. Key corrections to the surveys' assumptions that shape the design: the target is `its-fieldops/safety_portal/`; the `.dash-*` CSS is **not yet in the target** (must be ported, 0 matches vs demo's 39); the SPA is a `useState` nav (not react-router) wired through `App.tsx` + `HomePage.tsx`'s `HOME_CARDS`; `requireCapability` is named-in-comment-only (line 231) and must be added next to `requireRole` (332-336); routes are inline `app.get` in a single 2,200-line `index.ts`; next migration is `0018`; tests use `cloudflare:test` `SELF.fetch` (worker) + vitest SPA config. Here is the design doc.

---

# DESIGN — Field-Ops READ layer port (URS-Marine → ITS `its-fieldops` Safety Portal)

**Target repo:** `/Users/sethsmith/its-fieldops/safety_portal/`
**Logic source (write-stripped):** `/Users/sethsmith/its-urs-marine/src/pages/*` + `…/src/lib/{api,format,jobStatus}.ts`
**Style + read-only template:** `/Users/sethsmith/its-demo/safety_portal/src/pages/SolarDashboard.tsx` + `…/src/styles/global.css:935-1151`

**Containment rule that drives the whole split:** local agents drift on multi-file edits, so **all shared-file edits are quarantined into Brief 0 (prereqs), which lands FIRST.** After Brief 0 the app compiles green with empty placeholders. Briefs A/B/C are then **purely additive within their own files** — each overwrites its own stub worker module + stub page and creates its own lib + tests. **No A/B/C brief touches `index.ts`, `App.tsx`, `HomePage.tsx`, `global.css`, or the migration.** A/B/C are fully parallel-dispatchable with zero merge collisions.

Verified target facts the briefs depend on:
- Worker gate scaffold already present: `requireSession` (`worker/index.ts:239`), `requireRole` (`:332-336`), `createMiddleware` imported (`:2`), `c.set("capabilities", await resolveCapabilities(role, c.env.DB))` (`:320`, a `Set<string>`), `resolveCapabilities` fail-closed `new Set()` (`worker/auth.ts:99-113`), `Vars` (`worker/types.ts:72`). **`requireCapability` is referenced in a comment only (`:231`) — never defined.**
- Pagination idiom in-repo: `Math.min(Number(c.req.query("limit")) || 50, 200)` (`:927`); D1 `.batch([...])` idiom (`:917`).
- Cap grants are pure data (migration `0013`): `cap.jobtracker.read` → submitter+admin (`0013:89,96`); `cap.equipment.field` → submitter+admin (`0013:90`); `cap.personnel.read` → **admin-only** (omitted from submitter list `0013:85-93`).
- SPA nav is `useState<View>` in `App.tsx`; cards declared in `HomePage.tsx` `HOME_CARDS` (key/cap/badge/title/desc, cap-filtered).
- Tests: `npm run typecheck` (3 tsc projects) · `npm test` (worker, `cloudflare:test` `SELF.fetch`, Miniflare D1; new migrations auto-applied via `test/apply-migrations.ts`) · `npm run test:spa` (`vitest.config.spa.ts`). Canonical patterns: worker route+gate = `test/form-request.test.ts`; SPA = `src/pages/__tests__/FormRequestPage.test.tsx`.

---

## BRIEF 0 — Shared prerequisites (LANDS FIRST, single agent, must be green before A/B/C dispatch)

**Goal:** add the P0-deferred `requireCapability` gate, the read-scalability index migration, the `.dash-*` CSS kit, the keyset-cursor helper, and the nav + stub scaffold — so A/B/C drop into a green, fully-wired skeleton.

### Files to CREATE
1. `migrations/0018_fieldops_read_indexes.sql` — the read-path indexes:
```sql
-- 0018 — indexes the field-ops READ layer needs to stay O(page), not O(table).
CREATE INDEX IF NOT EXISTS idx_time_entries_personnel    ON time_entries(personnel_id, created_at);   -- Personnel list+detail
CREATE INDEX IF NOT EXISTS idx_inspections_job           ON inspections(job_id, created_at);          -- Job detail inspections (URS flag B)
CREATE INDEX IF NOT EXISTS idx_equipment_location_job    ON equipment_location(job_id, recorded_at);  -- Job equipment-on-site fan-out fix (URS flag A)
CREATE INDEX IF NOT EXISTS idx_task_assignments_personnel ON task_assignments(personnel_id, status);  -- Personnel who-is-where
CREATE INDEX IF NOT EXISTS idx_personnel_active          ON personnel(active, name);                  -- roster keyset list
CREATE INDEX IF NOT EXISTS idx_equipment_active          ON equipment(active, name);                  -- fleet keyset list
CREATE INDEX IF NOT EXISTS idx_jobs_status_name          ON jobs(status, project_name);               -- Job Tracker F5 status-filter + name sort
```
2. `worker/fieldops_gates.ts` — type-only shared contract (no runtime ⇒ no circular import):
```ts
import type { Hono, MiddlewareHandler } from "hono";
import type { Env, Vars } from "./types";
export type FieldopsApp = Hono<{ Bindings: Env; Variables: Vars }>;
export type FieldopsGates = {
  requireSession: MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
  requireCapability: (cap: string) => MiddlewareHandler<{ Bindings: Env; Variables: Vars }>;
};
```
3. `worker/cursor.ts` — one keyset codec all tabs share (opaque, validate-or-first-page, never throws):
```ts
// Opaque base64url(JSON) of an ordering tuple. decode returns null on ANY malformed
// input (→ first page); values are ALWAYS bound as params, never interpolated (Invariant 2).
export function encodeCursor(tuple: Record<string, string | number>): string {
  return btoa(JSON.stringify(tuple)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
export function decodeCursor(raw: string | undefined): Record<string, string | number> | null {
  if (!raw) return null;
  try {
    const o = JSON.parse(atob(raw.replace(/-/g, "+").replace(/_/g, "/")));
    return o && typeof o === "object" && !Array.isArray(o) ? o : null;
  } catch { return null; }
}
```
4. Three **stub worker route modules** (A/B/C overwrite these) — `worker/fieldops_personnel.ts`, `worker/fieldops_equipment.ts`, `worker/fieldops_jobtracker.ts`, each:
```ts
import type { FieldopsApp, FieldopsGates } from "./fieldops_gates";
export function registerPersonnelRoutes(_app: FieldopsApp, _gates: FieldopsGates): void {
  /* implemented in Brief A */
}
```
(equipment → `registerEquipmentRoutes`, jobtracker → `registerJobTrackerRoutes`).
5. Three **stub pages** (A/B/C overwrite) — `src/pages/FieldOpsPersonnel.tsx`, `…/FieldOpsEquipment.tsx`, `…/FieldOpsJobTracker.tsx`, each `export function FieldOpsPersonnel({ onBack }: { onBack: () => void }) { return <div className="page" />; }`.

### Files to EDIT
6. `worker/index.ts` — define `requireCapability` adjacent to `requireRole` (~`:337`), then wire the three modules right after it:
```ts
const requireCapability = (cap: string) =>
  createMiddleware<{ Bindings: Env; Variables: Vars }>(async (c, next) => {
    if (!c.get("capabilities").has(cap)) return c.json({ error: "forbidden" }, 403);
    await next();
  });
// — field-ops READ layer (each tab owns its own module; gates passed in to avoid circular import) —
const fieldopsGates = { requireSession, requireCapability };
registerPersonnelRoutes(app, fieldopsGates);
registerEquipmentRoutes(app, fieldopsGates);
registerJobTrackerRoutes(app, fieldopsGates);
```
Add the three `import { register…Routes } from "./fieldops_<tab>";` at the top with the other imports. **Do not move `requireSession`** (preservation / Op Stds §14) — pass it in.
7. `src/pages/HomePage.tsx` — extend `HomeNav` union with `"fieldops-personnel" | "fieldops-equipment" | "fieldops-jobs"` and add three `HOME_CARDS` entries:
```ts
{ key: "fieldops-jobs",      cap: "cap.jobtracker.read", badge: "Field Ops", title: "Job Tracker",
  desc: "Jobs, crew, open tasks, and equipment on site." },
{ key: "fieldops-equipment", cap: "cap.equipment.field", badge: "Field Ops", title: "Equipment",
  desc: "Fleet readiness, current location, inspections, and machine logs." },
{ key: "fieldops-personnel", cap: "cap.personnel.read",   badge: "Admin",     title: "Personnel",
  desc: "Who is where and per-person hour history." },
```
8. `src/App.tsx` — import the three pages and add three render branches mirroring the existing gated pattern (`view === "fieldops-personnel" && has("cap.personnel.read")` → `<FieldOpsPersonnel onBack={home} />`, etc.).
9. `src/styles/global.css` — **append** the `.dash-*` component block ported verbatim from `/Users/sethsmith/its-demo/safety_portal/src/styles/global.css:935-1151` (table/grid/card/pill/chips/progress/lists/states/drill-click/detail classes). Tokens already exist in the target `tokens.css` (`--c-brg`, `--c-gold`, `--gap`, `--radius` confirmed). Add nothing else — base shell classes (`page`, `card`, `btn*`, `banner`, `admin-tabs`, `page__main/__heading`, `welcome`, `muted`) already exist and are in use.

### Self-validation (must all pass before A/B/C dispatch)
```
npm run db:migrate:local      # 0018 applies clean
npm run typecheck             # green with empty stubs
npm test                      # worker suite green (new index migration auto-applied)
npm run test:spa              # SPA green; cards render gated, branches compile
```
Add one worker test asserting an unauthenticated `GET /api/fieldops/personnel` → 401 and a submitter (lacking `cap.personnel.read`) → 403, proving the gate is live before any real route exists.

### Skills
- **workers-best-practices** + **wrangler** (the `requireCapability` middleware + migration apply).
- **frontend-design** (the CSS port — verify the `.dash-*` block renders against the target tokens).
- **code-review** + the **portal-worker-security-reviewer** agent (the new gate is a security boundary — Invariant 2, fail-closed).

### Guardrails
Touch ONLY the 9 files above. Do **not** implement any route body or page body (leave stubs). Do **not** alter `requireSession`/`requireRole`/`resolveCapabilities`. Migration is `CREATE INDEX IF NOT EXISTS` only — no schema/data change. No `send`/Box/Graph imports anywhere (Invariant 1).

---

## BRIEF A — Personnel tab (`cap.personnel.read`, ADMIN-ONLY)

**Visibility:** admin only — submitter session resolves no `cap.personnel.read` (`0013`) ⇒ 403 server-side and no card client-side. Keep sharp: this is per-person hour history / who-is-where *across* jobs, distinct from `cap.jobtracker.read`'s crew-on-a-job view.

**Source to port (write-stripped):** `URS/pages/PersonnelDashboard.tsx` + `PersonnelDetail.tsx`; read-only template `DEMO/SolarDashboard.tsx` `PersonnelView` (`:303`), `ListShell`/`DetailShell`/`useFetch`/`clickProps` (`:156-300`).

### Files
- **OVERWRITE** `worker/fieldops_personnel.ts` — the two routes below.
- **CREATE** `src/lib/fieldops_personnel.ts` — `fetchPersonnelList(cursor?)`, `fetchPersonnelDetail(id, cursor?)` + TS types mirroring the response shapes.
- **OVERWRITE** `src/pages/FieldOpsPersonnel.tsx` — list (`dash-table`, `dash-row--click`) + detail ("Time history" with **Load more** via `next_cursor`). Reuse `.dash-*` + `fmtDateTime`/`fmtHours` (epoch **seconds** → `*1000`; port from `URS/lib/format.ts`).
- **CREATE** `test/fieldops-personnel.test.ts` (worker) + `src/pages/__tests__/FieldOpsPersonnel.test.tsx` (SPA).

### Route SQL (scalable: keyset page parents → ONE grouped-latest batch for the page)
**`GET /api/fieldops/personnel`** — gate `requireSession` + `requireCapability("cap.personnel.read")`. `limit = Math.min(Math.max(parseInt(q.limit)||50,1),200)`; `cur = decodeCursor(q.cursor)`.

1) Roster page (keyset on `(name,id)`, served by `idx_personnel_active`):
```sql
SELECT id, name, trade, username
FROM personnel
WHERE active = 1
  AND (?1 IS NULL OR name > ?1 OR (name = ?1 AND id > ?2))   -- bind cur.n, cur.i (or null,null)
ORDER BY name ASC, id ASC
LIMIT ?3;
```
2) Latest entry for **only this page's ids** (deterministic window, served by `idx_time_entries_personnel`; build `IN (?,?,…)` from the ≤200 page ids — bound params only):
```sql
SELECT personnel_id, job_id, project_name, hours, work_started_at, work_ended_at, recorded_at
FROM (
  SELECT t.personnel_id, t.job_id, j.project_name, t.hours, t.work_started_at,
         t.work_ended_at, t.recorded_at,
         ROW_NUMBER() OVER (PARTITION BY t.personnel_id
                            ORDER BY t.created_at DESC, t.uuid DESC) AS rn
  FROM time_entries t
  LEFT JOIN jobs j ON j.job_id = t.job_id
  WHERE t.personnel_id IN (/* page ids */)
) WHERE rn = 1;
```
Merge in JS (Map by `personnel_id`). Response `{ personnel: PersonnelRow[], next_cursor }`; `next_cursor = rows.length === limit ? encodeCursor({n: last.name, i: last.id}) : null`.

**`GET /api/fieldops/personnel/:id`** — same gate; validate `:id` integer (400), 404 if no personnel row. Header by PK; entries keyset-paginated (served by `idx_time_entries_personnel`):
```sql
SELECT t.uuid, t.job_id, j.project_name, t.hours, t.work_started_at, t.work_ended_at,
       t.recorded_at, t.notes
FROM time_entries t
LEFT JOIN jobs j ON j.job_id = t.job_id
WHERE t.personnel_id = ?1
  AND (?2 IS NULL OR t.created_at < ?2 OR (t.created_at = ?2 AND t.uuid < ?3))  -- cur.c, cur.u
ORDER BY t.created_at DESC, t.uuid DESC
LIMIT ?4;
```
Response `{ personnel: { id, name, username, trade, time_entries: [...] }, next_cursor }`.

### Self-validation
`npm run typecheck && npm test && npm run test:spa`. Worker tests (model on `test/form-request.test.ts`): provision admin + submitter; assert `200` admin / `403` submitter / `401` anon on both routes; `400` non-int id; `404` unknown id; seed >`limit` entries and assert page size honored + `next_cursor` walks the second page + no overlap. SPA test: list renders rows + "No time logged" (`dash-unavail`) when `latest_entry` null; row click opens detail.

### Guardrails
Edit ONLY the 5 files above. Bound params only (no string SQL). **Never** `SELECT *` / `payload_json`. No correlated per-row subquery — the windowed batch is mandatory. No `send`/AI/Box/Graph import (Invariant 1). Confirm exact column names against `migrations/0014`/`0015` before binding.

### Skills
**tdd** (route tests first) · **workers-best-practices** + **wrangler** (D1 keyset + window) · **frontend-design** (`.dash-*` list/detail) · **code-review** + **portal-worker-security-reviewer** (admin-only gate).

---

## BRIEF B — Equipment tab (`cap.equipment.field`, SUBMITTER + ADMIN)

**Visibility:** submitter + admin (`0013:90`). Read surface only — **do NOT port** the URS write panels (status/move/maintenance/pre-inspection forms, "Manage equipment") or their CSS; the demo strips them and `dash-detail__inline/__actions` do not exist in this CSS.

**Source:** `URS/pages/EquipmentDashboard.tsx` + `EquipmentDetail.tsx`; template `DEMO/SolarDashboard.tsx` `EquipmentView` (`:410`, detail sections `:440-526`).

### Files
- **OVERWRITE** `worker/fieldops_equipment.ts`.
- **CREATE** `src/lib/fieldops_equipment.ts` (`fetchEquipmentList(cursor?)`, `fetchEquipmentDetail(id, cursors?)` + types).
- **OVERWRITE** `src/pages/FieldOpsEquipment.tsx` — `dash-grid` of `dash-card--click` units (status pill via `equipStatusPillClass`, location, latest inspection, recent logs) + detail's three history sections, all keyset "Load more".
- **CREATE** `test/fieldops-equipment.test.ts` + `src/pages/__tests__/FieldOpsEquipment.test.tsx`.

### Route SQL
**`GET /api/fieldops/equipment`** — gate `requireSession` + `requireCapability("cap.equipment.field")`. Read the **denormalized snapshot** for the pill (S3) — no subquery for status:

1) Fleet page (keyset `(name,id)`, `idx_equipment_active`):
```sql
SELECT id, name, kind, identifier, status, status_note
FROM equipment
WHERE active = 1
  AND (?1 IS NULL OR name > ?1 OR (name = ?1 AND id > ?2))
ORDER BY name ASC, id ASC
LIMIT ?3;
```
2–4) Three windowed batches over **the page's ids only** (run via D1 `.batch([...])` after step 1), each index-served, deterministic, scalar-only (NO `payload_json`):
```sql
-- latest location  (idx_equipment_location_latest)
SELECT equipment_id, label, lat, lon, read_at, recorded_at, job_id FROM (
  SELECT el.*, ROW_NUMBER() OVER (PARTITION BY equipment_id
                                  ORDER BY recorded_at DESC, id DESC) rn
  FROM equipment_location el WHERE equipment_id IN (/* page ids */)
) WHERE rn = 1;

-- latest inspection  (idx_inspections_equipment) — scalar cols only
SELECT equipment_id, form_code, version, performed_at, recorded_at FROM (
  SELECT i.equipment_id, i.form_code, i.version, i.performed_at, i.recorded_at,
         ROW_NUMBER() OVER (PARTITION BY i.equipment_id
                            ORDER BY i.created_at DESC, i.uuid DESC) rn
  FROM inspections i WHERE i.equipment_id IN (/* page ids */)
) WHERE rn = 1;

-- recent logs, bounded ≤5/unit  (idx_equipment_logs_equipment) — filter is the indexed
--   leading col (equipment_id), NOT the URS non-SARGable `log_type != 'status'`
SELECT equipment_id, log_type, value_num, detail, performed_at, recorded_at FROM (
  SELECT el.equipment_id, el.log_type, el.value_num, el.detail, el.performed_at, el.recorded_at,
         ROW_NUMBER() OVER (PARTITION BY el.equipment_id
                            ORDER BY el.created_at DESC, el.uuid DESC) rn
  FROM equipment_logs el WHERE el.equipment_id IN (/* page ids */)
) WHERE rn <= 5;
```
Merge by `equipment_id`. Response `{ equipment: EquipmentRow[], next_cursor }`.

**`GET /api/fieldops/equipment/:id`** — same gate; `:id` int (400), 404 missing. Header by PK (incl. snapshot `status/status_note/status_changed_at/status_actor`), then three **independently keyset-paginated** history legs (`Promise.all`), each fully index-covered (this is URS's cleanest route #5):
```sql
-- locations (idx_equipment_location_latest), cursor (recorded_at,id)
SELECT label,lat,lon,read_at,recorded_at,job_id FROM equipment_location
WHERE equipment_id = ?1
  AND (?2 IS NULL OR recorded_at < ?2 OR (recorded_at = ?2 AND id < ?3))
ORDER BY recorded_at DESC, id DESC LIMIT ?4;
-- inspections (idx_inspections_equipment), cursor (created_at,uuid), scalar cols only
SELECT uuid,form_code,version,performed_at,recorded_at,job_id FROM inspections
WHERE equipment_id = ?1
  AND (?2 IS NULL OR created_at < ?2 OR (created_at = ?2 AND uuid < ?3))
ORDER BY created_at DESC, uuid DESC LIMIT ?4;
-- logs (idx_equipment_logs_equipment), cursor (created_at,uuid)
SELECT uuid,log_type,value_num,detail,status_value,performed_at,recorded_at FROM equipment_logs
WHERE equipment_id = ?1
  AND (?2 IS NULL OR created_at < ?2 OR (created_at = ?2 AND uuid < ?3))
ORDER BY created_at DESC, uuid DESC LIMIT ?4;
```
Response `{ equipment: { …header, locations, inspections, logs }, cursors: { loc, insp, log } }` (one `next_cursor` per leg).

**Deliberate scalability deviation to flag for sign-off:** the list card sources recent-logs from the page-scoped `≤5/unit` window (above), **not** the URS global 4,000-row `WHERE log_type!='status'` batch that filesorted the whole table. Same card UX, O(page) cost.

### Self-validation
`npm run typecheck && npm test && npm run test:spa`. Worker tests: 401/403/200 matrix; submitter **allowed**; 400/404 on detail; seed a unit with >5 logs and assert list returns exactly 5 (deterministic newest); detail leg cursors paginate independently. SPA: card grid renders pills (`dash-pill--ok/--warn/--danger`), `dash-unavail` on null location, detail's three sections render.

### Guardrails
Edit ONLY the 5 files. Bound params; scalar columns only — **never select `payload_json`** (S5). Pill from the snapshot column, never a subquery. No write routes/forms. No `send`/AI/Box/Graph import. Confirm columns against `0014`/`0015`/`0016`.

### Skills
**tdd** · **workers-best-practices** + **wrangler** (windowed batches, `.batch`) · **frontend-design** (`dash-grid`/pills/`dash-loglist`) · **code-review** + **portal-worker-security-reviewer**.

---

## BRIEF C — Job Tracker tab (`cap.jobtracker.read`, SUBMITTER + ADMIN)

**Visibility:** submitter + admin (`0013:89,96`). Read only — **do NOT port** "+ New job"/`NewJobPage`, Log-time, Close-job, progress edit, add-task, task-status (`dash-detail__addtask/__num`, `dash-task-status`, `btn--danger` are absent from this CSS).

**Source:** `URS/pages/JobTrackerDashboard.tsx` + `JobDetail.tsx`; template `DEMO/SolarDashboard.tsx` `JobTrackerView` (`:609`, detail `:632-749`).

**F5 — job-scope guard (explicit design call):** the Job Tracker spans the lifecycle, so the LIST **filters by a validated `status` param and paginates the all-status set** — it does **NOT** hard-gate `active=1` like the inherited `/api/filed`. The per-job DETAIL 404s an unknown `job_id` (no-enumeration parity) but must serve `closed`/`on_hold` jobs.

### Files
- **OVERWRITE** `worker/fieldops_jobtracker.ts`.
- **CREATE** `src/lib/fieldops_jobtracker.ts` (`fetchJobList(status?, cursor?)`, `fetchJobDetail(jobId, cursors?)` + types).
- **OVERWRITE** `src/pages/FieldOpsJobTracker.tsx` — `dash-grid` of `dash-card--click` jobs (title + `jobPillClass` pill, client·job_id, `dash-progress`/`__fill` bar, `dash-chips` crew, `dash-tasklist` open-tasks w/ `taskPillClass`) + detail.
- **CREATE** `test/fieldops-jobtracker.test.ts` + `src/pages/__tests__/FieldOpsJobTracker.test.tsx`.

### Route SQL
**`GET /api/fieldops/jobs`** — gate `requireSession` + `requireCapability("cap.jobtracker.read")`. `status = q.status ∈ {active,closed,on_hold,all}` (validate; default `active`); `limit`/`cursor` as usual.

1) Jobs page (keyset on `(project_name,job_id)`; `status` filter served by `idx_jobs_status_name`; `all` ⇒ omit status predicate):
```sql
SELECT job_id, project_name, status, progress, client_name
FROM jobs
WHERE (?1 = 'all' OR status = ?1)
  AND (?2 IS NULL OR project_name > ?2 OR (project_name = ?2 AND job_id > ?3))
ORDER BY project_name ASC, job_id ASC
LIMIT ?4;
```
2) Crew per page job (distinct personnel, page-scoped; `idx_task_assignments_job`):
```sql
SELECT DISTINCT ta.job_id, p.id, p.name, p.trade
FROM task_assignments ta JOIN personnel p ON p.id = ta.personnel_id
WHERE ta.job_id IN (/* page job_ids */);
```
3) Open tasks per page job (page-scoped; `idx_task_assignments_job` serves `job_id`+`status`):
```sql
SELECT t.id, t.job_id, t.description, t.status, p.name AS personnel_name
FROM task_assignments t LEFT JOIN personnel p ON p.id = t.personnel_id
WHERE t.job_id IN (/* page job_ids */) AND t.status != 'done'
ORDER BY t.job_id, t.created_at DESC;
```
Group/cap crew & tasks ≤20/job in JS. Response `{ jobs: JobRow[], next_cursor }`.

**`GET /api/fieldops/jobs/:job_id`** — same gate; 404 unknown `job_id` (serve any status). Header by PK (+ `clients` join for `{name,contact,phone,email}` — confirm the client-linkage column in `0014`/`0016` before binding). Five legs via `Promise.all`:
```sql
-- tasks (all), idx_task_assignments_job; cursor (created_at,id)
SELECT id,description,status,(SELECT name FROM personnel WHERE id=t.personnel_id) personnel_name
FROM task_assignments t WHERE t.job_id = ?1
  AND (?2 IS NULL OR created_at < ?2 OR (created_at = ?2 AND id < ?3))
ORDER BY created_at DESC, id DESC LIMIT ?4;
-- crew (distinct), idx_task_assignments_job
SELECT DISTINCT p.id,p.name,p.trade FROM task_assignments ta JOIN personnel p ON p.id=ta.personnel_id
WHERE ta.job_id = ?1 LIMIT ?2;
-- time_entries (job-scoped), idx_time_entries_job; cursor (created_at,uuid)
SELECT t.uuid,t.hours,t.work_started_at,t.work_ended_at,t.recorded_at,t.notes,
       p.name personnel_name
FROM time_entries t LEFT JOIN personnel p ON p.id=t.personnel_id
WHERE t.job_id = ?1
  AND (?2 IS NULL OR t.created_at < ?2 OR (t.created_at = ?2 AND t.uuid < ?3))
ORDER BY t.created_at DESC, t.uuid DESC LIMIT ?4;
-- equipment-on-site — URS flag-A fan-out FIX via idx_equipment_location_job:
--   restrict candidates to equipment EVER on this job, window to each one's latest,
--   keep only those whose latest is still this job. Cost tracks equipment-on-job, not fleet.
SELECT e.id,e.name,e.kind,e.identifier,loc.label,loc.read_at FROM (
  SELECT equipment_id,label,read_at,job_id,
         ROW_NUMBER() OVER (PARTITION BY equipment_id ORDER BY recorded_at DESC, id DESC) rn
  FROM equipment_location
  WHERE equipment_id IN (SELECT DISTINCT equipment_id FROM equipment_location WHERE job_id = ?1)
) loc JOIN equipment e ON e.id = loc.equipment_id
WHERE loc.rn = 1 AND loc.job_id = ?1 LIMIT ?2;
-- inspections (job-scoped) — URS flag-B FIX via idx_inspections_job; scalar cols only
SELECT i.uuid,i.form_code,i.version,i.performed_at,i.recorded_at,
       (SELECT name FROM equipment WHERE id=i.equipment_id) equipment_name
FROM inspections i WHERE i.job_id = ?1
  AND (?2 IS NULL OR i.created_at < ?2 OR (i.created_at = ?2 AND i.uuid < ?3))
ORDER BY i.created_at DESC, i.uuid DESC LIMIT ?4;
```
Response `{ job: { …header, client, crew, tasks, time_entries, equipment_on_site, inspections }, cursors: {...} }`.

### Self-validation
`npm run typecheck && npm test && npm run test:spa`. Worker tests: 401/403/200 (submitter allowed); **`?status=closed` returns a closed job and `?status=active` excludes it** (F5); detail of a `closed` job returns 200 (not 404); unknown `job_id` → 404; keyset walks page 2 with no overlap; equipment-on-site returns a unit whose latest location is this job and excludes one moved away. SPA: progress bar width clamps 0-100, crew chips + open-task pills render.

### Guardrails
Edit ONLY the 5 files. Bound params; scalar columns only (no `payload_json`). No N+1 — page-scoped batches/joins. List filters `status`, never hard `active=1`. No write routes. No `send`/AI/Box/Graph import. Confirm columns against `0014`/`0015`/`0016`/`0017`.

### Skills
**tdd** · **workers-best-practices** + **wrangler** · **frontend-design** (progress/chips/tasklist) · **code-review** + **portal-worker-security-reviewer** (F5 scope + gate).

---

## SCALABILITY CHECKLIST — review gate every brief's output MUST pass

A reviewer (run **code-review** + **portal-worker-security-reviewer**) rejects the PR if any item fails.

1. **Pagination cap present.** Every list/history route parses `limit` as `Math.min(Math.max(parseInt(limit)||50,1),200)` (default 50, hard cap ≤200). No route returns an unbounded or fixed-huge set.
2. **Keyset, not OFFSET.** Every accumulating-table read paginates by `WHERE (sort_key, pk) < cursor` + `ORDER BY sort_key DESC, pk DESC` and returns `next_cursor`. Zero `OFFSET`. Cursor decode is fail-safe (malformed ⇒ first page, never 500).
3. **No unbounded SELECT.** Every query carries `LIMIT`. No `SELECT *`. Scalar columns only — **`inspections.payload_json` / any payload blob is never selected on any list or history route** (S5).
4. **Indexes present + aligned.** Every `WHERE`/`ORDER BY` is served by an index whose leading column is the filter and trailing column is the sort. The seven `0018` indexes exist and `npm run db:migrate:local` applies clean.
5. **No N+1; no per-row correlated subquery.** "Latest per group" is a single windowed/grouped batch keyed on the page's ids (or the denormalized `equipment.status` snapshot), **bounded by group count, not parent count**. Nested arrays (crew/tasks/logs) come from page-scoped batches, never a per-parent loop. The URS fan-out (equipment-on-site) and filesort (logs/inspections) offenders are demonstrably fixed.
6. **Determinism.** Every keyset cursor and every window/group adds the PK as a tiebreak (`…, id DESC` / `…, uuid DESC`).
7. **Gate correct + fail-closed.** Route chains `requireSession` then `requireCapability(<cap>)`; cap matches the F3 matrix (`personnel.read` admin-only; `equipment.field` + `jobtracker.read` submitter+admin); unauth→401, missing-cap→403, bad id→400, unknown id→404. Verified by a worker test, not just inspection.
8. **Bound params only (Invariant 2).** No string-interpolated SQL anywhere, including the `IN (...)` page-id lists.
9. **Send-free (Invariant 1).** No `send`/Box/Graph/AI import in any route module — pure D1 read served live (never Smartsheet).
10. **Contained.** Briefs A/B/C touched only their own 5 files; `index.ts`/`App.tsx`/`HomePage.tsx`/`global.css`/migration unchanged since Brief 0.

**Files referenced (all absolute):** `/Users/sethsmith/its-fieldops/safety_portal/worker/{index.ts,auth.ts,types.ts}`, `…/migrations/0013…0017_*.sql` (new `0018_fieldops_read_indexes.sql`), `…/src/{App.tsx,pages/HomePage.tsx,lib/api.ts,styles/global.css,styles/tokens.css}`, `…/test/{form-request.test.ts,apply-migrations.ts}`; template `/Users/sethsmith/its-demo/safety_portal/src/{pages/SolarDashboard.tsx,styles/global.css}`; logic source `/Users/sethsmith/its-urs-marine/src/{pages/*,lib/{api,format,jobStatus}.ts}`.