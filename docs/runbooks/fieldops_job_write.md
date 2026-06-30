---
type: operations
date: 2026-06-27
status: active
related_prs: []
workstream: safety_portal
tags: [runbook, successor-remediation, field-ops, job-create, origin-fence, smartsheet, tier-2, p2.3]
---

# Runbook — Field-Ops portal job create (portal-origin jobs "stuck pending") (Successor-Remediation, Op Stds §43)

A §43 successor-remediation entry for the **Successor-Operator** (a trained operator who runs
Claude Code + reads Smartsheet rows and alert emails, but does **not** read code or touch
secrets). The §42 code-reader rationale lives in `safety_portal/worker/fieldops_job_write.ts`
(the 0017 fence stamping) and `safety_portal/migrations/0017_jobs_origin_fence.sql`.

## Purpose

An office admin can now create a job **in the portal** (the Job Tracker, `cap.jobtracker.manage`,
admin-only) instead of only in Smartsheet's `ITS_Active_Jobs`. A portal-created job is stamped
`origin='portal'`, `sync_state='pending'`, `canonical_job_id=NULL` so the 60-second
`ITS_Active_Jobs → portal` sync (which only replaces `origin='smartsheet'` jobs) can never delete
it. The **P2.4 mirror daemon** later promotes a `pending` job UP into `ITS_Active_Jobs` (Smartsheet
auto-assigns its permanent `JOB-####`), writes that into `canonical_job_id`, and flips
`sync_state` to `'synced'`.

## Symptom

A job created in the portal works fine locally (it shows in the Job Tracker, accepts time
entries / tasks) **but never appears in `ITS_Active_Jobs`, never gets a `JOB-####`, and stays
`sync_state='pending'`.**

## What the Successor-Operator checks

1. **Is the P2.4 mirror daemon (`field_ops/fieldops_sync`) live?** Look for its row in
   **ITS_Daemon_Health** (System / Daemons). **Until P2.4 ships, this daemon does NOT exist —
   and ALL portal-created jobs staying `pending` is EXPECTED, not a fault.** The job is fully
   usable in the portal meanwhile; only the Smartsheet mirror is deferred.
2. If the daemon row exists but shows stale/stopped, that's a normal stopped-daemon situation.

## The Claude prompt / UI action

- **Low-class repair (only once P2.4 is live):** if the `fieldops_sync` daemon exists but is
  stopped, restart it the same way as any other daemon (reload its launchd job) — ask Claude Code
  to "reload the fieldops-sync daemon and confirm its ITS_Daemon_Health heartbeat." The pending
  jobs promote on the next cycle.
- **No daemon yet (pre-P2.4):** nothing to repair — the pending state is by design. Do not edit
  `origin` / `sync_state` / `canonical_job_id` by hand.

## Escalate to Seth when

- The P2.4 daemon does **not exist yet** and a stakeholder needs the portal job reflected in
  Smartsheet now (it's a not-yet-built capability — a code/scheduling decision).
- A job is stuck `pending` **after** the daemon is confirmed live and heartbeating (a real sync
  failure).
- Anything that would touch the **origin fence** values (`origin` / `sync_state` /
  `canonical_job_id`) directly — that is doctrine + code (high-capability-class), always Seth.

## Activation — P2.5 Slice 1 (operator, one-time, ORDER-DEPENDENT)

Slice 1 adds migration `0021_jobs_sor_fields.sql` (the SoR/lifecycle/version-vector columns the
expanded create + the new `/job/:id/lifecycle` and `/job/:id/contacts` routes write) and a new
bearer-gated internal queue (`/api/internal/fieldops/*`). Activate in THIS order — a stale-checkout
deploy ahead of the migration caused the 2026-06-28 universal lockout (forensic class #2):

1. Pull `~/its` to current `main` first (the deploy must never run from a stale tree).
2. Apply migration `0021` to live D1 **remotely BEFORE** the Worker redeploy — else the new routes
   500 on the unknown columns (the same activation rule as 0017).
3. Set the mirror daemon's bearer secret `PORTAL_FIELDOPS_API_TOKEN` as a Worker secret
   (privilege-separated from the portal_poll + admin tokens), and mirror the SAME value into the
   macOS Keychain as `ITS_PORTAL_FIELDOPS_TOKEN` for the Slice-5 daemon.
4. Redeploy the Worker.

Until Slice 5's daemon exists + its `field_ops.fieldops_sync.sync_enabled` flag is ON, the new
`/api/internal/fieldops/*` routes simply have no caller — portal-created jobs accumulate as dirty
(`origin='portal'`, `sync_state='pending'`) and stay correctly fenced from the down-sync sweep.
