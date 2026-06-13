---
type: design
date: 2026-06-12
status: draft
related_prs: []
workstream: safety_portal
tags: [safety-portal, form-request, scalability, month-filter, pr-6, cc-brief, design-brief]
---

# Safety Portal — Form Request month-year + form-type filter (PR-6) · CC brief

**Status: DRAFT, ready to build.** Self-contained engineering brief for a fresh Claude Code
session — assume **zero** prior context. It scopes a follow-up to PR-5 (Form Request, merged
2026-06-12, exec main `13ef2bc`). Before building, run the `brief-validator` agent to confirm
every file/line/symbol below still matches current code, then implement as one tracer-bullet PR.

---

## 1. Problem (why this exists)

PR-5 added an in-portal **Form Request** page: a field PM picks a job and sees its filed safety
forms in one flat table, then batch-requests their PDFs (requester-bound, 24h). That page calls
`GET /api/filed?job_id=…`, which returns **up to 500 filed forms in a single table**, newest-first.

A year-long job accumulates **hundreds to low-thousands** of submissions (daily JHAs + toolbox
talks + equipment pre-inspections × multiple crews). The flat table becomes an unusable wall of
rows **and silently truncates at 500** — older documents vanish with no signal. The operator
flagged this as a display-scalability problem before it bites in production.

## 2. Decisions locked (operator, 2026-06-12)

1. **Filter by `work_date`** (the date written on the form — when the crew did the work), NOT
   `filed_at`. "Show me June's documents" means *work performed in June*. (`work_date` is a
   `TEXT NOT NULL` `YYYY-MM-DD` column — migration `0003_create_portal_tables.sql:18`.)
2. **Add a form-type filter** too. Cascade becomes **Job → Month-Year → (optional) Form type →
   documents**. A busy month on a big job can still be hundreds of forms; the form-type sub-filter
   narrows June down to just JHAs, just Toolbox Talks, etc.

## 3. Current state (what PR-5 shipped — DO NOT regress)

- **Worker** `safety_portal/worker/index.ts`:
  - `GET /api/filed?job_id` (~L762, `requireSession`): 404 unless the job is **active**
    (`SELECT 1 FROM jobs WHERE job_id=? AND active=1`); else returns filed rows —
    `SELECT s.submission_uuid, s.form_code, s.work_date, s.filed_at, (s.pdf_ready_at IS NOT NULL) AS cache_ready, (pr.requested_at IS NOT NULL) AS requested FROM submissions s LEFT JOIN pdf_requests pr ON pr.submission_uuid=s.submission_uuid AND pr.account=? AND pr.requested_at > unixepoch()-86400 WHERE s.job_id=? AND s.box_verified=1 ORDER BY s.filed_at DESC, s.created_at DESC LIMIT 500`. Maps to `{filed:[{submission_uuid, form_code, work_date, filed_at, requested:bool, ready:bool}]}` where `ready = cache_ready && requested`. **The `requested`/`ready` flags are computed for the CALLING account only** (the `pr.account=?` bind = `c.get("session").username`).
  - `POST /api/request-pdfs` (~L802), the requester-bound `GET /api/submissions/:uuid/pdf` (~L709) + `/status` (~L673), and `GET /api/internal/pdf-requests` (~L938). **These are the requester-bound download model — leave them entirely untouched.** See [[reference_pdf-requests-requester-bound-model]] (memory) / the PR-5 README section `safety_portal/README.md` "Form Request browse + requester-bound PDF (PR-5 — `0012`)".
- **SPA** `safety_portal/src/pages/FormRequestPage.tsx`: job dropdown (`api.fetchJobs`) → `api.fetchFiled(jobId)` → table with a checkbox per un-requested row + a `RowDownload` cell (5s poll → Download). "Request selected" → `api.requestPdfs(uuids)`.
- **API client** `safety_portal/src/lib/api.ts`: `interface Job {job_id, project_name, …}`, `fetchJobs()`, `interface FiledForm {submission_uuid, form_code, work_date, filed_at, requested, ready}`, `fetchFiled(jobId)` (GET `/api/filed?job_id`, 404→`[]`), `requestPdfs(uuids)`, `pdfStatus(uuid)`, `downloadPdf(uuid)`.
- **Existing index** (no new migration needed — see §5): `idx_submissions_lookup ON submissions(job_id, form_code, work_date, created_at)` (migration `0003:23`).
- **Tests**: `safety_portal/test/form-request.test.ts` (Worker, 19 cases — the access matrix + batch + requester-bound matrix), `safety_portal/src/pages/__tests__/FormRequestPage.test.tsx` (SPA, 5 cases). Harness: `test/pdf.test.ts` shows `seedSubmission` + `requestAs` + the `call()/login()/provision()` helpers; `test/apply-migrations.ts` auto-applies all `migrations/*.sql` via `readD1Migrations()` (no hardcoded list — a new migration is picked up automatically; only the comment "0001…0012" is cosmetic).

## 4. Proposed design

A **two-endpoint** change (no schema migration). Worker stays **send-free**.

### 4a. New: `GET /api/filed/months?job_id=…`  (`requireSession`)
Populates the Month-Year dropdown (only months that actually have documents) **and** the
Form-type dropdown (only form types present for the job).
- 404 unless the job is active (same guard + same `{error:"not_found"}` shape as `/api/filed`; no enumeration). Bound `job_id`, length ≤ 64.
- Months: `SELECT substr(work_date,1,7) AS month, COUNT(*) AS count FROM submissions WHERE job_id=? AND box_verified=1 GROUP BY month ORDER BY month DESC`.
- Form codes: `SELECT DISTINCT form_code FROM submissions WHERE job_id=? AND box_verified=1 ORDER BY form_code`.
- Response (NAMED fields, never a bare array): `{ months: [{month:"2026-06", count:23}, …], form_codes: ["jha","toolbox", …] }`.

### 4b. Extend: `GET /api/filed?job_id=…[&month=YYYY-MM][&form_code=…]`  (`requireSession`)
- `month` (optional): validate against `^\d{4}-\d{2}$` (else 400 `bad_request`); add `AND substr(s.work_date,1,7) = ?` bound. (substr-equality with the `job_id`-leading index is fast for a single job — even a 2-year daily-form job is ~2k rows; a dedicated `(job_id, work_date)` index is an OPTIONAL future optimization only past ~10k rows/job, NOT part of PR-6.)
- `form_code` (optional): length ≤ 64; add `AND s.form_code = ?` bound. The existing `idx_submissions_lookup(job_id, form_code, work_date, …)` serves `job_id + form_code + work_date` optimally.
- **Backward-compat:** with neither param, behavior is unchanged (all filed, `LIMIT 500`). With `month` present, the 500 cap is no longer a truncation risk (a month is naturally bounded); keep a defensive `LIMIT 500` anyway. The per-account `requested`/`ready` LEFT JOIN, ordering, and response shape are unchanged.

### 4c. SPA `FormRequestPage.tsx` cascade
State: `jobId`, `months: {month,count}[]`, `formCodes: string[]`, `selectedMonth`, `selectedFormCode` (default `""` = All), `filed: FiledForm[] | null`.
- On **job** select → `api.fetchFiledMonths(jobId)` → populate Month + Form dropdowns; reset month/form/table.
- On **month** or **form** change → `api.fetchFiled(jobId, {month, formCode})` → table (same checkbox + RowDownload + "Request selected" flow as today — unchanged).
- Empty months → "No filed forms for this job yet." Month dropdown shows e.g. `June 2026 (23)`; render the `YYYY-MM` as a human "Month YYYY" label. Form dropdown: "All forms" + one option per `form_code` (map to a display label via the catalog/registry if cheap, else show the raw `form_code`).
- New `api.ts`: `interface MonthBucket {month:string; count:number}`, `fetchFiledMonths(jobId): Promise<{months:MonthBucket[]; form_codes:string[]}>` (404→`{months:[],form_codes:[]}`), and extend `fetchFiled(jobId, opts?: {month?:string; form_code?:string})` to append the query params.

## 5. Why no migration

The queries are all `WHERE job_id=? [AND form_code=?] [AND work_date…]` — covered by the existing
`idx_submissions_lookup(job_id, form_code, work_date, created_at)`. **PR-6 ships with NO new
migration → no deploy-ordering step → just `npm run deploy`.** (Contrast PR-5's `0012`, which was
order-critical.) If profiling ever shows the all-forms month aggregate is slow on a >10k-row job,
add `CREATE INDEX idx_submissions_job_workdate ON submissions(job_id, work_date)` as a separate,
non-order-critical migration — out of scope here.

## 6. Security invariants to preserve (state these in the PR; a `portal-worker-security-reviewer` pass is required)

- **Send-free Worker** — no new `fetch()`/egress; D1 + Response only.
- **Bound SQL only** — every `job_id` / `month` / `form_code` value via `.bind(...)`; NO string interpolation into SQL. `month` is regex-validated before use.
- **404-not-403, no enumeration** — `/api/filed/months` 404s an inactive/unknown job exactly like `/api/filed`.
- **`requireSession`** on both browse routes (never the internal/admin token).
- **Per-account isolation** — `requested`/`ready` stay computed for the calling account; the months/form_codes aggregates are job-scoped, not account-scoped, and leak no per-account state.
- **Input bounds** — `job_id` ≤64, `form_code` ≤64, `month` matches `^\d{4}-\d{2}$`.
- **Do NOT touch** the requester-bound `/pdf` / `/status` / `/api/internal/pdf-requests` / `prune.ts` / `pdf_requests` / migration files.

## 7. Tests (add; keep existing green)

- **Worker** `test/form-request.test.ts`: `/api/filed/months` returns months+counts (desc) and distinct form_codes; inactive/unknown job → 404; `/api/filed?month=` filters to that work-month; `&form_code=` narrows; bad `month` → 400; per-account `requested`/`ready` still correct under month/form filters; a `work_date` straddling months lands in the right bucket. Seed via the existing `seedSubmission` helper (set distinct `work_date`/`form_code`).
- **SPA** `src/pages/__tests__/FormRequestPage.test.tsx`: picking a job loads months + form options; picking a month fetches that month; changing the form filter refetches; the request/download flow still wires to the row uuid. Mock `fetchFiledMonths` + the extended `fetchFiled`.

## 8. Gates / definition of done

- `npm test` (Worker, all green — was 190 after PR-5), `npm run test:spa` (was 63), `npm run typecheck` clean, `npx gitleaks detect` clean.
- `portal-worker-security-reviewer` pass on the Worker diff (propose-only); resolve any block.
- Build in an isolated **git worktree** with its own cloned venv per worktree discipline (the live launchd daemons import `~/its`); never edit `~/its` directly. For a TS-only worktree, `npm ci` in the worktree's `safety_portal/`.
- Open one PR; four-part verify after merge (state=MERGED + mergedAt + mergeCommit + main-branch CI SUCCESS).
- **Deploy: just `cd safety_portal && npm run deploy`** (no migration). The live mirror Worker must already have PR-5 (migration `0012`) deployed first.

## 9. Out of scope / future

- Pagination/infinite-scroll within a single very-large month (month + form-type is the agreed first cut).
- A free-text search across forms.
- The optional `(job_id, work_date)` index (§5) — only if a single job exceeds ~10k rows.
- Any change to the requester-bound download model or the two-stage prune.

## 10. Pointers

- Memory: [[project_safety_portal_state]], [[reference_pdf-requests-requester-bound-model]].
- PR-5 session log: `docs/session_logs/2026-06-12_pr5-form-request-pr3-graph-upload-tree-cleanup.md`.
- PR-5 README section: `safety_portal/README.md` → "Form Request browse + requester-bound PDF (PR-5 — `0012`)".
