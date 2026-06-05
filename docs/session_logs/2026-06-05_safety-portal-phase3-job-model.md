---
type: session_log
date: 2026-06-05
status: closed
related_prs: [160]
workstream: safety_portal
tags: [safety-portal, job-model, active-jobs, intake, smartsheet, migration, phase3]
---

# Session log — Safety Portal Phase 3: job model + live Job-ID resolution

Built the job-model layer for the Safety Portal (back-half brief Part A): a new
`shared/active_jobs.py` lookup, a `shared/safety_week.py` week-bucket helper, a
rewritten `resolve_project()` in `safety_reports/intake.py` keyed on Job-ID
(retiring legacy subject/body string matching), and an additive sandbox migration
extending ITS_Active_Jobs with four routing columns. Landed as PR #160.

## Commits / PRs landed

- **PR #160 — feat(safety-portal): Phase 3 job model + live Job-ID resolution** —
  squash `827c3744`. New modules `shared/active_jobs.py` and `shared/safety_week.py`;
  updated `safety_reports/intake.py`, `tests/test_intake.py`,
  `tests/test_intake_stage2_refactor.py`; new migration
  `scripts/migrations/extend_its_active_jobs_phase3.py`; new §43 runbook
  `docs/runbooks/safety_portal_job_management.md`. Details per module:

  - **`shared/active_jobs.py`** (NEW): ITS_Active_Jobs Job-ID lookup. Mirrors
    `shared/project_routing.py` pattern — read-only, 60-second TTL cache, typed
    `ActiveJob` projection, deny-by-default (unknown Job-ID returns `None`).
    Read failures surface as WARN + empty result; no hardcoded fallback.
    +9 unit tests.

  - **`shared/safety_week.py`** (NEW): Pure Saturday→Friday week-bucket helper.
    Canonical key is the Saturday ISO date (e.g., `2026-06-06`). Sortable,
    year-spanning. Explicitly NOT an ISO week number, which would mis-key
    December→January boundaries. +14 unit tests.

  - **`safety_reports/intake.py`** (MODIFIED): `resolve_project()` rewritten to
    return `ProjectResolution(project_name, reason)`, driven by a new
    `ParsedEmail.job_id` field. No-Job-ID / unknown-Job-ID / inactive-Job-ID →
    Review Queue with the precise reason in summary, payload, `error_code`, and
    notes. Legacy subject/body substring matching (`_name_matches`) RETIRED by
    operator sign-off. Removed now-unused `sheet_ids` import.

  - **`tests/test_intake.py` + `tests/test_intake_stage2_refactor.py`** (MODIFIED):
    Updated for the Job-ID resolution model; a `resolved_job` / `patch_baseline`
    stub carries end-to-end tests past Stage 4.

  - **`scripts/migrations/extend_its_active_jobs_phase3.py`** (NEW): Additive live
    schema on sandbox ITS_Active_Jobs. Adds four routing columns (Stakeholder
    Name, Stakeholder Email, Stakeholder Phone, Safety Reports Contact Email) and
    renames kebab `Job ID` → `Job Slug`. Idempotent, `--dry-run` flag,
    verify-after read. Applied to sandbox this session.

  - **`docs/runbooks/safety_portal_job_management.md`** (NEW): §43 successor
    runbook — add a job, retire a job, and the one-time AUTO_NUMBER UI step.

## CI runs / four-part verify

PR #160 — four-part verify clean
- state: MERGED
- mergedAt: 2026-06-05T14:05:03Z
- mergeCommit: 827c3744107457556c5d50691abc9911756c7e8d
- main CI on merge commit: SUCCESS
  - `ci` workflow — run 27019588255, conclusion: success
  - `CodeQL` workflow — run 27019586095, conclusion: success

Per-session local validation gate before merge:

- pytest: 66 tests across the 4 touched suites — all passed
- mypy: 0 errors / 163 source files (post-fix; see CI lesson below)
- ruff: clean
- main-branch CI on merge commit: SUCCESS

Live migration applied to sandbox ITS_Active_Jobs: 4 columns added, `Job ID`
renamed to `Job Slug`. `active_jobs` live-read smoke: graceful-empty (expected
until the AUTO_NUMBER column exists). `ops-stds-enforcer`: WARN, no blockers
(§14/§30-read-only/§3/§43 clean; two §42 docstring reformats applied before merge).

## Decisions made during session

1. **Legacy email intake retired.** Operator sign-off: nothing hits the sandbox;
   any production legacy items would be handled manually. Legacy `_name_matches`
   subject/body substring matching is removed; any inbound message without a
   recognized Job-ID routes to Review Queue with a precise reason. New edge case
   captured: **manual week-sheet additions** (operator adds a row + safety doc
   directly to a week sheet, fills cells; `intake.py` ignores it; `weekly_generate`
   rolls it into the compiled packet). This mechanism requires no code change.

2. **D1 dropdown sync (brief item A.1.4) deferred to the Phase 2 deploy session.**
   The portal D1 database does not exist yet (Phase 2 deploy is deferred). Building
   the sync before the target database exists would be premature. Alternative
   considered: scaffold the sync against a local D1 via `wrangler dev --local`.
   Rejected: the sync touches the live portal's Job-ID list; its correct shape
   depends on the production D1 structure, which is resolved at deploy time.

3. **Job-ID key switched to Smartsheet AUTO_NUMBER.** The ITS_Active_Jobs sheet was
   seeded in the prior session with kebab Job IDs (e.g., `bradley-1`). The Phase 3
   brief originally proposed a creation guard (A.4) to enforce uniqueness. Operator
   decision: use Smartsheet's built-in AUTO_NUMBER column type (prefix `JOB-`,
   4-digit fill, start 1) instead — uniqueness is enforced by the platform, and the
   guard is unnecessary. `shared/active_jobs.py` reads whatever value the `Job ID`
   column holds, so the lookup is agnostic to the key format.

## Non-obvious findings

- **AUTO_NUMBER columns cannot be created via the Smartsheet REST API.** Bare
  `type: AUTO_NUMBER` in a column create call returns errorCode 1008 ("Unable to
  parse request"). It is a UI-only column type. The migration script therefore does
  the API-doable work (4 routing columns + `Job ID` rename to free up the title)
  and DETECTS-OR-INSTRUCTS the AUTO_NUMBER column as a one-time manual operator
  UI step. This is documented in the migration script header and in the §43
  runbook.

- **Built in `~/its` directly, not a git worktree.** The `resolve_project` change
  is live in the launchd daemon tree from the moment it was written. This was safe
  because legacy intake is retired: any inbound message with no recognized Job-ID
  routes to Review Queue rather than silently mis-routing. Nothing is incoming to
  the sandbox, so the in-tree build carried zero operational risk.

- **mypy CI failure from `dict[str, object]` inference.** `pytest + ruff` ran clean
  locally before merge. A `len(AUTONUM_FORMAT['fill'])` call on a `dict[str, object]`
  value triggered a mypy type error (`Argument 1 to "len" has incompatible type
  "object"; expected "Sized"`) that only surfaced in the CI `test` job (which runs
  mypy as a blocking step). Fixed in a follow-up commit (`75fa8f9`). Lesson: when
  working in `~/its` directly, run `mypy` explicitly before push — `pytest + ruff`
  alone do not catch all CI blocking errors.

## What was NOT touched

- Invariant 1 (External Send Gate) and Invariant 2 (Adversarial Input Handling)
  mechanics unchanged. `shared/active_jobs.py` is read-only with no send or AI
  capability.
- `weekly_generate.py` and `weekly_send.py` not touched — the Phase 3 job model
  feeds `intake.py` only; the generate/send path is Phase 5.
- The Phase 5 portal-marker branch in `intake.py` (populated `job_id` from HMAC
  header) is not built — that is Phase 5 scope.
- No launchd plists added or modified.
- `lint_doc_conventions.py` workstream set not updated to include `safety_portal`
  (pre-existing gap; carried forward).
- No doctrine or blueprint files touched.

## Open items handed off

- **Operator UI step (immediate):** Add the `Job ID` AUTO_NUMBER column in the
  Smartsheet UI on ITS_Active_Jobs. Settings: prefix `JOB-`, 4-digit fill, start 1.
  This completes the Phase 3 schema. Steps are in
  `docs/runbooks/safety_portal_job_management.md`.

- **Operator UI step:** Create the "New Job" Smartsheet form on ITS_Active_Jobs
  (fields per the runbook). Enables operator self-service job provisioning without
  requiring a developer.

- **D1 dropdown sync (A.1.4):** Build at the Phase 2 deploy session, once the portal
  D1 exists. Not blocking Phase 3.

- **Phase 5 (submission pipeline + compile + send):** Separate session, brief Part B.
  Includes the HMAC-verified portal-marker branch in `intake.py` (populating
  `ParsedEmail.job_id` from `X-ITS-Portal-HMAC`), the manual-additions mechanism,
  and the `weekly_generate` / `weekly_send` integration.

- **Fill the 6 Address cells** in ITS_Active_Jobs — carried forward from the prior
  session. PM fills manually; Work Location auto-fill on the portal remains empty
  until then.

## Cross-references

- Prior safety_portal session log (Phase 2):
  [`2026-06-04_safety-portal-phase2-cloudflare-scaffold.md`](2026-06-04_safety-portal-phase2-cloudflare-scaffold.md)
- Prior safety_portal session log (config sheets):
  [`2026-06-03_safety-portal-config-sheets-and-alignment-audit.md`](2026-06-03_safety-portal-config-sheets-and-alignment-audit.md)
- Safety Portal mission: `../its-blueprint/workstreams/safety-portal/mission.md` v1
- Op Stds v16 §30 (SDK-vs-Live; read-only path verified via live smoke)
- Op Stds v16 §42/§43 (self-documentation + successor-remediation DoD; applied to
  both new modules and the runbook)
- `docs/runbooks/safety_portal_job_management.md` — §43 successor-remediation runbook
- `docs/operations/pr_merge_discipline.md` — four-part verify; leg 4 = main-branch CI
- `docs/tech_debt.md` — Address cells entry (pre-existing); mypy-before-push lesson
- CLAUDE.md `safety_reports/intake.py` table entry — updated to reflect retired
  legacy matching and new `ParsedEmail.job_id` field
