---
type: session_log
date: 2026-07-17
status: closed
related_prs: [608, 609]
workstream: infrastructure
tags: [error-log, operator-dashboard, alert-hygiene, triage]
---

# 2026-07-17 — Error-flood triage + dashboard hardening

## Purpose

Operator asked "there are a dozen or so errors a day on the dashboard — what's the severity, is it
clutter or do we need to fix?" This session diagnosed the errors, fixed the biggest noise source at
the source, cleared the resolved backlog, and hardened two dashboard observability gaps. Closes with
two items the operator explicitly deferred to the next session.

## Diagnosis

The daily errors are **transient Smartsheet-outage fallout** — HTTP 500/502/503/504, ReadTimeouts,
and the resulting circuit-breaker-open cascade over ~07-12→07-16, plus a now-resolved row-cap
incident. LOW severity: the system responded exactly as designed (SDK backoff-retry → circuit
breaker → fail-open config reads → fail-loud-and-retry compiles). Confirmed recovered: breaker
`CLOSED`, 10/10 live reads succeed. Every error category traced to the same external cause — none
were logic bugs.

## Code changes

- **PR #608 (`24e343a`) — `shared/required_config.py::resolve_and_log`.** Transient
  `config_read_error` WARNs were logged **one per key**, so a breaker-open window × N declared keys ×
  ~7 daemons was the dominant daily ITS_Errors noise. Now they collapse into **one summarized WARN
  per pass** ("config read failed for N of M key(s) this cycle … Keys: …"), fail-open unchanged, the
  missing-row WARN kept per-key (individually actionable). Live on the daemon tree.
- **PR #609 (`b60ab1e`) — operator dashboard.**
  1. **Open-CRITICALs fire-surface panel** (`OpenCriticalsSource`) — counts *unresolved* CRITICAL
     `ITS_Errors` rows sheet-wide (the "am I on fire" set watchdog Check B tracks), reusing the same
     cached read + the canonical `errors_rotation.errors_row_is_terminal` predicate. The dashboard
     previously only had the recency slice, so the open-CRITICAL backlog — and the effect of
     resolving it — was invisible.
  2. **Daemon panel `-15` false-error fix** — the panel evaluated `status != "0"` before checking
     whether the process was alive, painting a running KeepAlive server's prior `-15` (SIGTERM =
     graceful restart) as red. Now a live pid = running/OK; the last-exit is informational.

## Live operations (data, not code)

- Resolved **92 transient + 33 historical open CRITICALs** via the #597 dashboard mark-resolved verb
  (dry-run→live per `(script, error-code)`, audit-stamped `its-diagnosis-2026-07-17`). Open CRITICALs
  **134 → 9**, verified by read-back. The 9 left (deliberately not auto-resolved — the operator's to
  review): `safety_reports.intake / uncaught_exception ×7` (a real `'tuple' object has no attribute
  'value'` bug on the now-dormant email path) + `scripts.watchdog / critical ×2` (row-cap incident,
  already fixed by storm-mode + drain).

## Non-obvious decisions

- **Did NOT add heavy-read retry.** `smartsheet_client` already layers SDK backoff-retry → 30s
  timeout → circuit breaker → fail-open; adding retry would hammer a down backend (the module warns
  against exactly this). Reported the finding and left it — observe a day before any change.
- **Resolved only clearly-transient / historical CRITICALs.** Filtered to the outage `uncaught_exception`s
  and the definitively-past categories (`portal_creds_missing`, old `publish_daemon.failed.*`,
  `subcontract_bearer_rejected`, `config_actuator.failed.tested`); left the real `intake` bug + the
  row-cap records untouched — never silently resolve a genuine fire.
- **The dashboard is real, not faked** — verified the running panel's output equals a direct
  Smartsheet query; the numbers differed only because the errors panel is recency-based while the
  "134→9" is the whole-sheet open-CRITICAL metric. That gap is what the new fire-surface panel closes.

## Verification

- pytest: 3529 passed / 0 failed (CI-equivalent `-m 'not integration'`) at #609
- mypy: clean / 393 source files
- ruff: clean
- build/live smoke: #608 live-smoked against real Smartsheet (`resolve_and_log`); #609 live-smoked
  against real `ITS_Errors` (panel shows the 9 open) + daemon fix verified
- main-branch CI on merge commits `24e343a` / `b60ab1e`: SUCCESS (required `ci.yml` trio)

## Out-of-scope notes

- Picklist audit ran CLEAN (exit 0, zero drift) — the dashboard's "exit 1" was a stale last-exit;
  `launchctl kickstart -k gui/$(id -u)/org.solutionsmith.its.picklist-audit` refreshes it.

## Operator-side actions remaining (deferred to next session — operator wants BOTH)

1. **Restart button** — a PIN-gated "Restart dashboard" Class-B ACT (detached `kickstart`,
   restart-only, NOT a git-pull/deploy). Deliberately crosses the dashboard self-exclusion invariant
   ("a service must not stop itself via its own UI") — operator-authorized. DoD: PIN-gated, detached
   spawn survives self-SIGTERM, tests, + live-verify.
2. **Bulk-clear the review queue (294 PENDING) + root cause** — 285 are "weekly compile: job
   JOB-XXXXX has no safety-reports contact (TO)" (mostly sandbox test jobs, re-flagged weekly since
   06-07). Clearing re-accrues weekly unless the root cause is fixed (populate contacts in
   `ITS_Active_Jobs` or deactivate the dead sandbox jobs).

Also: the running dashboard (pid 55622) is still pre-corpus code — one restart displays `/troubleshoot`,
`/docs`, and the new panel + `-15` fix.
