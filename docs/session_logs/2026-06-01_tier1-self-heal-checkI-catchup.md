---
type: session_log
date: 2026-06-01
status: closed
related_prs: [133]
workstream: infrastructure
tags: [watchdog, weekly_generate, tier-1-self-heal, catch-up, successor-remediation]
---

# Session log — Tier-1 self-heal: weekly_generate catch-up (Check I)

PR #133 (merge commit `98f8117`). Part B of the Tier-1 self-heal brief; Part A
(the companion doctrine correction) landed separately as `its-blueprint`#34
(`275e664`). Do-Part-A-first ordering was honored — the doctrine was accurate
before this code landed.

## Purpose

Close the one daemon launchd cannot self-recover. Every other tracked daemon
is interval-driven (`StartInterval`), so a crashed cycle is simply re-run at
the next interval — launchd re-invocation *is* the recovery. `weekly_generate`
runs Friday 14:00 via `StartCalendarInterval`, so a **crashed Friday cycle is
not re-invoked until next Friday** (launchd treats a started-then-failed
calendar job as "ran"). This was the sole open leg of V&R Pre-Cutover
Condition 4 (Tier-1 self-heal). Add watchdog **Check I — weekly_generate
catch-up** to detect and re-fire a missed run on a subsequent daily watchdog
pass.

## Pre-flight findings

The brief's code-shape claims were validated against live HEAD (`585823d`)
before any edit (a 9-agent validation workflow + direct reads). Several brief
claims were **corrected** by the validation and built-to accordingly:

- **`_write_watchdog_marker` is not the watchdog's helper name.** The public
  helper is `write_last_run_marker(job_name)`; `weekly_generate` has its own
  inline `_write_watchdog_marker`. Both write `~/its/.watchdog/{slug}.last_run`.
- **`weekly_generate.main` is double-decorated** (`@its_error_log` +
  `@require_active`). The brief said "call the function `__main__` calls"
  (= `main`), but `@require_active` blocks MAINTENANCE — and the brief *also*
  requires catch-up to run during MAINTENANCE. Resolved by calling the
  undecorated **`_run_pipeline(week_start_override=…)`** (its own docstring
  designates it the direct-invocation entry point). The watchdog's `main()`
  already honored the kill switch, so this avoids a double-gate; documented in
  the Check I docstring.
- **`log(CRITICAL, …)` does NOT triple-fire.** It writes the local log +
  ITS_Errors row only; the Resend/Sentry push legs fire only via
  `error_log._alert_critical`. The canonical programmatic triple-fire pattern
  is `shared.picklist_sync.sync_all:559-572` (two calls, shared
  `correlation_id`) — mirrored in Check I's `_escalate_catchup_failure`.
- **§43 entry shape is FOUR parts, not five** (the brief said five): Symptom /
  What the Successor-Operator checks / The Claude prompt or UI action /
  Escalate-to-Seth condition (the both-rule folds into Escalate). No mandated
  directory — this PR establishes `docs/runbooks/`.
- **Audit F16 vendor is UptimeRobot, not Healthchecks.io** ("Healthchecks.io"
  appears nowhere in the blueprint). The watchdog's own F16 comment had drifted
  to "Healthchecks.io"; corrected to "UptimeRobot".
- **Capability gate is unaffected:** `scripts/watchdog.py` is in neither
  `GATED_SCRIPTS` nor `SEND_SCRIPTS` and `scripts/` is not walked, so importing
  `weekly_generate` adds no send capability.

## Code changes

- `scripts/watchdog.py` — Check I (`_check_weekly_generate_catchup`) + helpers
  (`_local_now`, `_most_recent_friday_trigger`, `_read_marker_datetime`,
  `_wpr_rows_exist_for_week`, `_fire_weekly_generate_catchup`,
  `_escalate_catchup_failure`); constants `WEEKLY_GENERATE_JOB_SLUG`,
  `WEEKLY_GENERATE_TRIGGER_{WEEKDAY,HOUR}`, `CATCHUP_WINDOW`. Appended to
  `CHECKS` (runs after Check C). Module docstring + in-code rationale; F16
  comment vendor fix.
- `tests/test_watchdog.py` — 22 Group-I tests + updated the exact-CHECKS-list
  equality test. test_watchdog.py: 95 passed (73 prior + 22 new).
- `docs/runbooks/safety_weekly_generate.md` + `README.md` — first §43
  successor-remediation runbook; establishes the `docs/runbooks/` convention.
- `scripts/smoke_test_watchdog_catchup.py` — operator-run live smoke.
- `docs/tech_debt.md` — reconciled the stale "Check H heartbeat-staleness
  successor" entry; added the `audit_picklist_drift.py`-not-wired finding.
- `CLAUDE.md` — reconciled the maintenance-model Tier-1 description (#132 had
  kept the stale "Check H" framing).

## Decisions made during execution (the non-obvious ones)

1. **Detection signal — conservative AND-of-negatives.** Catch up iff marker
   stale/missing AND no WPR rows for the week (+ in window). The two "ran"
   signals are OR'd so a fresh marker OR existing rows suppresses a re-fire.
   This deliberately trusts a fresh marker (protects reviewer-deleted rows from
   regeneration) at the cost of a rare false-negative if a manual/other-week
   run refreshed the shared marker after the trigger — which degrades safely to
   Check C / human. The alternative (rows-only) would regenerate
   reviewer-deleted rows. Documented in the check docstring.
2. **Scope: "ran-but-all-projects-failed" is OUT.** Those runs completed and
   wrote `[GENERATION_FAILED]` rows (so rows exist → no catch-up). Auto-retry
   of failed projects is owned by the future generation-retry redesign
   (planning #1). Check I closes only the "calendar run never executed" gap.
3. **Catch-up window = Friday trigger → end of the following Monday**
   (`timedelta(days=3)`, end-of-day): covers the Sat/Sun/Mon daily-or-on-wake
   watchdog runs; a miss not recovered by Monday falls to Check C's 8-day WARN.
   No holiday-awareness (matches `weekly_generate`'s own non-holiday Friday run).
4. **No "Check H".** The new check is **I**, not H — H was a doctrine naming
   artifact the marker-file Check C already fulfills; naming the new check H
   would collide with the (now-corrected) doctrine. Skip documented in the
   module docstring.
5. **Escalation is MAINTENANCE-aware via push-vs-record (Op Stds §3.1).** On
   catch-up failure the CRITICAL **record** row always writes; the operator
   **page** (`_alert_critical`) defers under `alerts_suppressed` (Check G
   pattern). Returns INFO afterward so `_run_check` does not write a second,
   correlation-id-less row.

## Verification

- pytest: 1162 passed / 0 skipped / 20 deselected (integration)
- mypy: 0 errors / 141 source files
- ruff: clean
- main-branch CI on merge commit: SUCCESS

(`docs/runbooks/` index `regen_doc_indexes.py --check`: clean.)

## Live smoke

Operator-authorized live run against the **evergreenmirror sandbox**
(`system.state = ACTIVE`). Phase A (read-only) confirmed live detection AND
surfaced that the sandbox's `weekly_generate` had genuinely not run for the
current target week **2026-05-25** (stale marker, 0 WPR rows). Operator chose
to do a **genuine backfill** (keep results):

```
RUN 1 -> FIRES:  catch-up fired for week 2026-05-25: 6 draft(s) written, 0 failed
  + Bradley 1/2, Rockford, Brimfield 1/2, Huntley — each [ZERO_DATA_WEEK],
    Approved=False  (no field data -> no Anthropic spend; exercised the
    transient-404 retry on Bradley 2)
  marker refreshed -> 2026-06-01T18:51:30Z
RUN 2 -> NO re-fire:  "weekly_generate ran for week 2026-05-25 (marker fresh)"
VERDICT: fired once ✓  marker refreshed ✓  no re-fire ✓  = PASS
```

The 6 ZERO_DATA placeholders are kept in `WPR_Pending_Review` for reviewer
disposition — Check I doing its production job for a genuinely-missed week.

## Out-of-scope notes

- `safety_reports` escalation/scheduling redesign (#1–#4), Tranche 0 (landed
  independently as #132 mid-session), and any `KeepAlive` plist changes — all
  out of scope per the brief.
- **`audit_picklist_drift.py` not wired to an in-tree plist** — its
  `safety_picklist_audit` marker writer isn't scheduled by any
  `scripts/launchd/` plist (the picklist plist drives `run_picklist_sync.py`,
  which writes no marker). Logged to `docs/tech_debt.md`; not fixed here.
- **`references/daemon-health-schema.md:174-175`** (blueprint) still carries the
  stale "retrofit to write heartbeat" model — flagged in Part A's PR for a
  deliberate references-pass.
- **Integration test ↔ shared marker interaction:** `pytest -m integration`'s
  `weekly_generate` live test refreshes the shared
  `safety_weekly_generate.last_run` marker (for its disposable week), which by
  Check I's "trust a fresh marker" design can mask a catch-up for that window.
  Operator-aware; candidate fix is to have that integration test redirect
  `WATCHDOG_MARKER_DIR` like the unit tests do. Noted on the PR.

## Sequencing context

Closes the V&R Pre-Cutover Condition 4 residual (the only un-self-recovered
daemon); the all-daemon Check C coverage and the F16 ping legs were already
met. Unblocks treating Condition 4 as essentially MET. The future
generation-retry redesign (planning #1) will supersede/extend this catch-up.

## Operator-side actions remaining

- Worktree cleanup (hook-blocked from inside CC): `~/its-worktrees/tier1-self-heal`
  and the blueprint worktree `~/its-blueprint-tier1-doctrine`.
- Return `~/its` to `main` and pull (`git -C ~/its checkout main && git pull`).
- No git tags to push (Part A was entirely status-absorbing).
- Optional: triage the 6 backfilled week-2026-05-25 ZERO_DATA drafts in
  `WPR_Pending_Review`.

## Merge verification quartet output

```
state:        MERGED
mergedAt:     2026-06-01T18:57:56Z
mergeCommit:  98f81176e2fdfa0fdb6c89879cfe91ecc1e61cba
main-CI:      SUCCESS (ci on 98f8117 — Tests + doc-conventions lint + doc-index)
```
