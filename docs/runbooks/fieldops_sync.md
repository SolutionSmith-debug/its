---
type: operations
date: 2026-06-30
status: active
related_prs: []
workstream: field_ops
tags: [runbook, successor-remediation, fieldops_sync, job-tracker-pivot, mirror-daemon, smartsheet-write, version-vector, tier-2, tier-3, "50", "51"]
---

# Runbook — Field-ops mirror daemon (`fieldops_sync`) (Successor-Remediation, Op Stds §43)

`field_ops/fieldops_sync.py` (P2.5 Slice 5) is the Mac-side daemon that up-syncs portal-created
jobs (`origin='portal'`) into BOTH ITS-owned Active-Jobs Smartsheets — `ITS_Active_Jobs` (safety)
and `ITS_Active_Jobs_Progress` — via `shared/active_jobs_writer.py`. It is the first ITS-owned
**structured-SoR write-back** (Op Stds §51) and runs under the §50 code-actuation gate. It is
**send-free + AI-free** (GATED_SCRIPTS) — its only write capability is the two Active-Jobs sheets.

**Runtime gate (ships OFF):** `field_ops.fieldops_sync.sync_enabled` in ITS_Config (default false →
the daemon short-circuits to a no-op). The operator flips it ON only at the cutover, AFTER Slice 4
(the `Portal Job Key` TEXT column exists on BOTH sheets). The daemon polls the Worker's
`GET /api/internal/fieldops/pending-jobs` (bearer `ITS_PORTAL_FIELDOPS_TOKEN`), find-or-creates each
dirty job in both sheets keyed on `Portal Job Key`, and commits per-sheet via
`POST /api/internal/fieldops/jobs-mark-mirrored`.

> **The both-rule (Op Stds §44).** Tier-2 (Successor-Operator) self-repair is allowed only for a
> **documented + low-capability-class** fault. Anything touching **secrets/auth, doctrine (§50
> enable), the External Send Gate, or code** is FIXED high-class → escalate to Seth.

## Symptom A — a portal job is missing from a sheet, in one sheet only, or `sync_state` stuck `pending`

**What it means.** The daemon hasn't successfully mirrored that job to one (or both) sheets. The
version vector keeps a job `pending` until BOTH sheets confirm — a one-sheet failure is a
first-class self-healing state (next cycle re-attempts; the succeeded sheet's find-or-create
no-ops), so a transient blip clears itself.

**Low-class Tier-2 checks (read-only first):**
1. Confirm the daemon is enabled + running: ITS_Config `field_ops.fieldops_sync.sync_enabled` is
   `true`, and the `ITS_Daemon_Health` row for **`field_ops.fieldops_sync`** is heartbeating
   (recent Last Run, status not ERROR).
2. Confirm **BOTH** sheets have a **`Portal Job Key`** column — the safety `ITS_Active_Jobs` one is
   operator-manual (Slice 4). If it's missing, the writer can't find-or-create there → add the TEXT
   column, then force a cycle.
3. Check `ITS_Review_Queue` (workstream `progress_reports`): a job that hit a **permanent** error
   (e.g. a picklist value the sheet rejects) is parked there with the reason — work that item.
4. Force one cycle: unload→reload the `org.solutionsmith.its.fieldops-sync` launchd job, or run
   `python -m field_ops.fieldops_sync` once from `~/its` (on `main`). Re-check the row in both
   sheets shares the same `Portal Job Key` and `Active` value.

**Escalate to Seth** if: the two sheets persistently DIVERGE for the same `Portal Job Key` (a
version-vector bug), or the fix would require flipping `sync_enabled` (a §50 doctrine/code-actuation
decision), or touching the fence/identity columns (`origin`/`sync_state`/`canonical_job_id`).

## Symptom B — CRITICAL `fieldops_creds_missing` (daemon won't sync; no watchdog marker)

**What it means.** Fail-CLOSED: the Worker base URL (ITS_Config `safety_reports.portal.worker_base_url`)
or the `ITS_PORTAL_FIELDOPS_TOKEN` Keychain bearer is missing, so the daemon refuses to sync (it
also writes NO watchdog marker, so Check C goes stale too — a deliberate double-signal).

**Boundary.** Confirming the **config row** exists is low-class Tier-2 (a config check). **Setting or
rotating the `ITS_PORTAL_FIELDOPS_TOKEN` secret is high-class → escalate to Seth** (secrets/auth is a
fixed high-class category; it must match the Worker's `PORTAL_FIELDOPS_API_TOKEN`).

## Symptom C — CRITICAL `fieldops_pending_auth_failed` (401 on pending-jobs)

**What it means.** The daemon's `ITS_PORTAL_FIELDOPS_TOKEN` does not match the Worker's
`PORTAL_FIELDOPS_API_TOKEN` secret (privilege-separated from the portal_poll + admin tokens).
**Secrets/auth → escalate to Seth** (re-set the Keychain entry to match the Worker secret). The
Successor-Operator does not handle token rotation.

## Symptom D — a single job repeatedly `fieldops_job_transient` (stays dirty), or `fieldops_pending_fetch_failed`

**What it means.** A transient Smartsheet error on that job (or fetching the queue). The per-job
fence leaves the job `pending` and continues; the next cycle retries. **Decoupled:** a
`fieldops_pending_fetch_failed` (the job-QUEUE fetch itself blipping) no longer skips the rest of the
cycle — the hours + equipment passes hit independent endpoints (`/hours-pending`,
`/equipment-snapshot`) and still run, so a recurring job-queue blip does NOT starve the Hours Log
mirror. (A 401 — `fieldops_pending_auth_failed`, Symptom C — DOES stop the whole cycle: the shared
bearer fails every endpoint.) A **sustained** job-queue outage (≥5 consecutive cycles) escalates from
ERROR to **CRITICAL** (`fieldops_pending_fetch_sustained` — email/Sentry), so a persistent
`/pending-jobs` outage is observable even though hours keep mirroring. **Low-class Tier-2:** for
`fieldops_job_transient` confirm Smartsheet is reachable (the circuit breaker / `ITS_Errors`); for
`fieldops_pending_fetch_failed`/`_sustained` confirm the **Worker** base URL + `/pending-jobs` are
reachable (the circuit breaker covers Smartsheet only, not this Worker fetch); if a single job is stuck transient for
many cycles after Smartsheet is healthy, capture its `job_id` and escalate (likely a row-shape edge
the §30 integration scaffold + a live smoke should reproduce).

## Symptom E — CRITICAL `fieldops_mark_mirrored_unauthorized` (401 on the mark-mirrored write-back)

**What it means.** The same auth mismatch as Symptom C, but caught on the *write-back* call (the
daemon telling the Worker a job was mirrored) instead of the initial pending-jobs fetch: the
`ITS_PORTAL_FIELDOPS_TOKEN` no longer matches the Worker's `PORTAL_FIELDOPS_API_TOKEN` secret. The
Smartsheet sheet write ALREADY landed (the job is in the sheet), so **nothing is lost** — only the
Worker's watermark is missing, so the job stays `pending` and is safely re-attempted next cycle
(find-or-create no-ops) once the token is fixed. **Secrets/auth → escalate to Seth** (re-set the
Keychain entry to match the Worker secret); the Successor-Operator does not handle token rotation.

## Symptom F — Material Incidents ledger (M3 Slice 2): sheet not appearing, or an incident missing

**What it is.** A per-job `<Job> — Material Incidents` Smartsheet (beside the Hours Log / Equipment /
Material List) that mirrors the FILED, §34-screened material-incident submissions — an **APPEND-ONLY
LEDGER** of delivery problems (damaged / short / wrong item / other), each optionally referencing its
expected-materials line (M3 Slice 1 `line_uuid`) and showing that line's live `Line Status`. Runs
inside `fieldops_sync`; gated by `field_ops.fieldops_sync.incidents_enabled` (ships **OFF**).

**Activation sequence (order matters; Developer-Operator / Seth).** (1) Deploy the Worker (the new
read route `GET /api/internal/fieldops/material-incidents` must exist first — **no D1 migration
needed**, it reads the existing `submissions` table). (2) Seed the ITS_Config row
`incidents_enabled = false` (Workstream `field_ops`) if it does not exist — a MISSING row reads as
`false`, so **there is no switch to flip until the row exists** (the #468/#470 dark-gate lesson,
HOUSE_REFLEXES §5). (3) Flip that row to `true`. A cell-flip is the only activation; a wrong Worker
base URL / bearer fails **closed** (Symptom B/C), never silently.

**Nothing is ever removed.** An incident is an immutable historical event — the pass NEVER marks a row
Removed and has no retire path, so the count-drops-to-zero / zero-drop class simply does not exist
here (unlike the Material List). A resolved incident stays on the ledger; only its `Line Status` cell
flips (e.g. to `received`). An archived job's ledger is MOVED to the Archive workspace on closure
(never deleted).

**Symptoms → repair (all LOW-class unless noted).**
- `fieldops_incident_permanent` → a Review-Queue row (workstream `progress_reports`): a permanent
  Smartsheet reject (validation / picklist) on one job or incident. Read the row's payload
  (`phase`, `incident_uuid`), fix the offending data or sheet, and it re-projects next cycle
  (idempotent). **Low-class.**
- `fieldops_incidents_fetch_failed`, `fieldops_incidents_sheet_transient`,
  `fieldops_incident_upsert_transient` → transient (Worker blip / Smartsheet 5xx); the ledger
  re-projects every cycle, so these **self-heal** — no action unless sustained. **Low-class**
  (re-run `sync_once` from a worktree venv to confirm recovery).
- `fieldops_incident_row_malformed` → a WARN (never silent): an incident row missing
  submission_uuid/job_id/project_name is skipped. Usually a Worker payload defect — escalate if it
  persists.
- `fieldops_incidents_fetch_auth_failed` (CRITICAL, 401) → the field-ops bearer was rejected. Same as
  Symptom C/E: **secrets/auth → escalate to Seth** (the Successor-Operator does not rotate tokens).
- `material_incidents_row_cap_warn` → a Review-Queue row: the append-only ledger is nearing the
  Smartsheet ~20k row cap (it grows monotonically, unlike the bounded Material List). Operator
  **period-splits** it (archive this sheet, start a fresh one) — **NEVER delete rows** (§51 SoR).
  **Low-class** but coordinate with Seth on the archive location.

## Why the daemon is shaped this way (pointer to §42)

The code-reader rationale lives in `field_ops/fieldops_sync.py` (the gate → fail-closed creds →
per-job fence → per-sheet mark-mirrored commit point) and `shared/active_jobs_writer.py` (the
non-clobbering find-or-create writes ONLY portal-owned columns by `Portal Job Key`; the version
vector advances each sheet's watermark independently — a progress failure leaves the job dirty with
safety already advanced, so it self-heals). Companion: `docs/runbooks/fieldops_job_write.md` (the
portal-side write surface). **Live-API correctness (the SDK-vs-Live class) is covered by
`tests/test_active_jobs_writer_integration.py` (Op Stds §30, `pytest -m integration`, operator-run).**

## Known fast-follows (NOT live yet)

- The watchdog `fieldops_sync` slug is registered in `TRACKED_JOBS` + a **stale-pending** check
  wire at the cutover (register + load together, like the progress slugs) — until the daemon is
  loaded, those would WARN, so they are deferred to activation.
- The launchd plist hardcodes a 300 s interval; an `install.sh` ITS_Config-driven interval is a
  tidy follow-up.
