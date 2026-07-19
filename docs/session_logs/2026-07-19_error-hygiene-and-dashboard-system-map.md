---
type: session_log
date: 2026-07-19
status: closed
workstream: operator_dashboard
related_prs: [613, 614]
tags: [operator_dashboard, error_log, alert-hygiene, system-map, forensic-audit, review-queue, dash-12, dash-13]
---

# 2026-07-19 — Error-hygiene forensic survey + dashboard system-map build (DASH-12/DASH-13)

## Purpose

Operator-directed dashboard pass: evaluate the current error picture (especially open
CRITICALs), build a live system-map visualization into the operator dashboard, refine the
frontend design including the config editor, and close the two items the 2026-07-17 session
handed off — DASH-12 (restart-dashboard verb) and DASH-13 (review-queue bulk-resolve verb). A
parallel RFQ/estimate worktree (`~/its-rfq-a`) was deliberately left untouched for the duration.

## Pre-flight forensic findings (5-agent survey, ~785k tokens)

Before writing any fix, a 5-agent forensic survey re-examined every open-CRITICAL / review-queue
claim carried forward from prior session logs and tech-debt entries, rather than trusting them:

- **The DASH-9 "intake tuple-bug ×7 = real still-open bug" claim was FALSIFIED.** All 7
  `safety_reports.intake` CRITICALs are 2026-05-21 pytest test pollution — tracebacks contain
  `unittest.mock` frames, paths point at pytest tmpdirs, and all 7 land in a single 9-minute
  window. Production `kill_switch` cannot structurally produce the `'tuple' object has no
  attribute 'value'` shape these carry. The 2 `scripts.watchdog` CRITICALs are stale row-cap
  history — the underlying issue has been fixed and zero-recurrent since 2026-07-13.
- **The 4 new CRITICALs from 07-17/07-18 were NOT a regression from PRs #608/#609.** Diff review
  confirmed they are independent background Smartsheet read-timeout transients (the existing
  2–10/day baseline), with identical pre-#608 occurrences of the same shape found in history.
- **DASH-13's tech-debt characterization was stale.** The live `ITS_Review_Queue` held 296
  PENDING rows: 277 were "weekly compile failed" rows (189 of those a single-day 06-13 storm
  for a now-deleted `JOB-000013`) + 6 "has no safety-reports contact" rows, all for deleted
  jobs + 13 miscellaneous. Only 3 jobs remain in `ITS_Active_Jobs` at all (`JOB-000017`,
  `JOB-000018`, `JOB-000027` — all sandbox; `-027` has a blank Safety Reports Contact Email).

These findings directly shaped what PR #613 fixed (real bugs only) and what the post-merge
disposition pass resolved vs. left for the operator (see below).

## PRs landed

### PR #613 — error-hygiene fixes (`fix/error-hygiene-2026-07-19`, squash-merged `f8156a8`)

Four independent fixes surfaced by the forensic survey, plus two doc corrections:

1. **`shared/error_log.py`** — sanitize newlines out of the Resend alert subject line. Real
   recurring bug: any CRITICAL message containing `\n` within its first 80 characters produced a
   malformed Resend subject, Resend returned HTTP 422, and the operator page silently never
   delivered. Confirmed as the cause of 3 prior occurrences (06-27, 07-13, 07-18).
2. **Config-read transient fence, three more replicas** — `po_materials/po_poll.py`,
   `subcontracts/subcontract_poll.py`, `safety_reports/send_poll_core.py` `_read_str_setting` now
   catch the base `SmartsheetError` and WARN `config_read_error` + fall back, instead of
   propagating to an unhandled-exception CRITICAL — the same pattern already applied elsewhere
   (`shared.required_config.resolve_and_log`, `config_actuator`). This was the cause of 3 of the 4
   new post-07-17 CRITICALs. F22's `_load_authorized_approvers` was deliberately left untouched —
   it is fail-closed by design and a transient there must escalate, not fall back.
3. **`safety_reports/compile_now_poll.py`** — scan-phase failures (before a compile is triggered)
   now log `compile_now_poll.scan_failed` with no `ITS_Review_Queue` row. Post-trigger failure
   behavior is unchanged.
4. **`safety_reports/generate_core.py` `_safe_review_queue`** — PENDING-row dedupe per (job,
   week), fail-open on a dedupe-read error. The 06-13 outage had written 189 rows for a single job
   because this dedupe didn't exist yet; this closes that class going forward.
5. Also: `docs/tech_debt.md` DASH-9 corrected to reflect the falsification above; `CLAUDE.md`
   tracked-job count corrected 12 → 13.

Build-agent gates (pre-merge, worktree venv): pytest 3540 passed / 49 deselected; mypy 0 errors /
393 source files; ruff clean; prove-it-bites — 8 discriminating tests confirmed RED against
unmodified `origin/main` before the fix, then green after.

Four-part verify (`pr-landed-verifier`, quoted verbatim):

> PR #613 — four-part verify clean / state: MERGED / mergedAt: 2026-07-19T13:47:36Z / mergeCommit:
> f8156a868fd3da97fe10610ec55cda9a8026eee6 / main CI on merge commit: SUCCESS (run 29689560601,
> workflow: ci, event: push) — also CodeQL run 29689560473: SUCCESS

### PR #614 — dashboard system-map + DASH-12 + DASH-13 (`feat/dashboard-system-map`, squash-merged `eee155d`)

The dashboard build, landed as one PR:

- **`/system` live system map.** Trust-gradient lanes with the two doctrine walls drawn
  structurally, not decoratively — the Invariant-2 ingress hatch and the External Send Gate
  rendered as a gold double-rule with port glyphs where send edges cross it. Registry
  `operator_dashboard/system_map.py` holds 43 nodes / 52 edges keyed to real join fields
  (`error_script`, `launchd_label`, `heartbeat_stem`, `config_gate`, `TRACKED_JOBS` marker,
  runbook path). Nodes carry live badges: open-CRITICAL counts via the existing shared cached
  `ITS_Errors` read + the canonical terminality predicate, DARK-gate tags via a TTL-cached
  `ITS_Config` read, and launchd state dots. An htmx detail rail per node shows blurb, live state,
  flows, runbook, troubleshooting-tree joins, and a filtered-errors link. Edge drawing is
  dependency-free progressive-enhancement SVG. Deep links go both ways: the existing panels' Script
  / daemon cells link to `/system?focus=`, and `/troubleshoot` cards link to `/system?wf=`;
  `/troubleshoot` itself gained `?wf=&step=&fm=` pre-expanded deep links, `/view` gained `?col=&eq=`,
  `/config` gained `?f=` filter prefill.
- **`tests/test_system_map.py`** enforces registry parity — a new launchd plist or `TRACKED_JOBS`
  marker with no corresponding map node fails the build. Flagged as a heads-up for the RFQ/estimate
  session: their new estimate daemon will need a system_map node or this test goes red on their PR.
- **DASH-12 — restart-dashboard verb.** `POST /act/dashboard/restart`, elevated confirm string
  `"restart-dashboard"`. Audit-logs before spawning, then a detached `kickstart -k` via
  `start_new_session` so the dashboard process can restart itself without the request handler dying
  mid-response. A restart-only command allowlist is asserted by tests — this deliberately crosses
  the dashboard's usual self-exclusion invariant ("a service must not stop itself via its own UI"),
  which the operator explicitly authorized in the 2026-07-17 handoff. `daemon_ops`'s general
  self-exclusion is otherwise unchanged.
- **DASH-13 — review-resolve verb.** `POST /act/review/resolve`, elevated confirm string
  `"resolve-review"`. An `errors_ops`-shaped twin scoped to `ITS_Review_Queue`: filter required (no
  unfiltered mass-resolve), preview/dry-run mode, PENDING-only, nothing ever deleted.
- **Design pass.** Pulse strip (6 status chips), config editor sticky section rail with a live
  client-side filter and section anchors, `:focus-visible` states, tabular-nums for count/ID
  columns, compact-panel token nowrap, correlation-ID shortening in the errors panel.
- **Registries reconciled in the same PR**, per the House Reflexes fan-out discipline: mutating-
  routes count 9 → 11; F02 capability allowlist entries added for the 3 new modules; §43
  successor-remediation runbook entries for both new verbs; enablement doc updated and its
  manifest sha256 re-recorded; dashboard README rewritten; `CLAUDE.md`'s `operator_dashboard/` row
  updated.

Gates (final local run, worktree venv, all PR #614 work integrated): pytest 3559 passed / 49
deselected; mypy 0 errors / 400 source files; ruff clean; `check_doctrine_drift --strict` clean;
docs-currency check — all 22 manifest entries current.

Four-part verify (`pr-landed-verifier`, quoted verbatim):

> PR #614 — four-part verify clean / state: MERGED / mergedAt: 2026-07-19T13:57:15Z / mergeCommit:
> eee155d0ffcde4f0bde8ec3d44e44967e0179ef1 / main CI on merge commit: SUCCESS (run 29689871803,
> workflow: ci — jobs test/portal/secrets all success; CodeQL run 29689871678 also success)

## Decisions made during session

1. **Fixed only survey-confirmed real bugs in PR #613, not the whole open-CRITICAL/review-queue
   backlog.** The forensic survey deliberately separated "real recurring bug" (Resend subject
   newline, the 3 config-read fence gaps, compile_now_poll scan-phase noise, generate_core dedupe)
   from "stale/misattributed history" (DASH-9's tuple bug, the watchdog rows, DASH-13's compile-
   failure characterization). Only the former became code changes; the latter became disposition-
   pass data operations (below), never code, since there was nothing broken to fix.
2. **F22's `_load_authorized_approvers` was excluded from the config-read transient fence.**
   Approval-attestation verification must fail closed on a Smartsheet read error, not silently fall
   back — the same fence pattern applied everywhere else would weaken a security-relevant gate.
3. **The restart-dashboard verb (DASH-12) uses a detached `kickstart -k` + `start_new_session`,
   not an in-process self-restart.** An in-process approach can't survive its own SIGTERM cleanly;
   detaching the restart from the request-handling process lets the HTTP response for the restart
   request itself complete before the old process dies.
4. **DASH-13's review-resolve verb mirrors the `errors_ops.mark_errors_resolved` shape exactly**
   (filter-required, preview-first, PENDING-only) rather than inventing a new bulk-action pattern —
   consistency with the already-shipped mark-resolved verb, and the same guard rationale (never an
   unfiltered mass-resolve).
5. **Registry-parity test (`test_system_map.py`) added as a build-time gate, not a runtime check.**
   A stale system map (missing a real daemon node) is a silent documentation-drift class identical
   to the ones House Reflexes §1 already calls out; catching it at CI time on the PR that adds a
   new daemon is cheaper than catching it in a later audit.

## Live rollout + disposition (data operations, post-merge)

- `~/its` pulled to `eee155d` (both PRs). Live dashboard restarted via
  `launchctl kickstart -k gui/$(id -u)/org.solutionsmith.its.dashboard`.
- `/healthz` verified post-restart: `registry_keys=70`, `secrets=9`, `panels=11`.
- `/system`, `/pulse`, and node-detail-rail requests all returned 200 — Playwright-verified after a
  browser-cache clear (the served HTML/CSS bytes were checked byte-correct against the deployed
  templates first, before concluding a stale-cache render was the only remaining discrepancy).
- **Disposition pass** (dry-run → commit, audit-stamped `its-diagnosis-2026-07-19`, via
  `errors_ops.mark_errors_resolved` + the new `review_ops.resolve_review_rows`):
  - Open CRITICALs: 13 → 0. Fire-surface panel now reads "0 open — clear."
  - `ITS_Review_Queue`: 296 → 64 PENDING. 232 stale rows tied to deleted jobs (the 06-13 storm's
    189 for `JOB-000013` + related deleted-job no-contact rows) were REJECTED with disposition
    notes. The 51 rows belonging to the 3 still-live sandbox jobs, plus 13 miscellaneous rows, were
    deliberately left PENDING for an operator decision, not auto-resolved.
  - Idempotency verified: re-running both dry-run passes after the commit reports all-noop.

## Open items handed off / next session

1. **Disposition of the 3 sandbox jobs (`JOB-000017`, `JOB-000018`, `JOB-000027`).** Either
   deactivate them PORTAL-SIDE (a sheet-side status flip alone gets overwritten by
   `fieldops_sync`'s down-sync) or keep them as intentional fixtures and populate
   `JOB-000027`'s currently-blank Safety Reports Contact Email.
2. **The 64 remaining `ITS_Review_Queue` rows** — 51 tied to the 3 live sandbox jobs (resolves once
   item 1 is decided) + 13 miscellaneous: 2 subcontract render refusals, 2 picklist
   mismatched-reference rows, 4 `sheet_capacity` margin-warning rows, 3 portal one-offs, 2
   integration-test artifacts. None auto-resolved — each needs a real operator look.
3. **`sheet_capacity` `ITS_Config` misconfiguration** — margin is currently set equal to cap
   (60/60), so every sheet-create operation emits a margin-breach WARN. Needs a real margin value,
   not a code fix.
4. **The config-read transient fence still has a gap in 3 more replicas** —
   `compile_now_poll.py`'s own top-level reader, `field_ops/fieldops_sync.py`, and
   `safety_reports/generate_core.py`'s remaining unfenced reads — not touched by PR #613's four
   fixes. Candidate follow-up PR.
5. **The RFQ/estimate PR will trip `test_system_map`'s registry-parity gate** — their new estimate
   daemon needs a `system_map.py` node added in the same PR that adds its launchd plist / tracked
   job, or CI goes red on their merge.

## What was NOT touched

- The parallel RFQ/estimate worktree (`~/its-rfq-a`) — deliberately left alone for the session's
  duration, per the operator's original scoping.
- No doctrine (`~/its-blueprint`) edits.
- No Keychain/secret writes.
- No `ITS_Config` gate flips beyond what shipped inside PR #614 (both DASH-12/DASH-13 verbs ship
  as immediately-usable dashboard actions, not behind a separate activation gate).
- The 64 remaining review-queue rows and the `sheet_capacity` misconfiguration — left for the
  operator, not resolved unilaterally (see Open items above).

## Cross-references

- `docs/session_logs/2026-07-17_error-triage-and-dashboard-hardening.md` — the session that
  originally opened DASH-9/DASH-12/DASH-13 and whose "134 → 9" open-CRITICAL claim this session's
  forensic survey re-examined (and partially falsified — see Pre-flight findings above).
- `docs/tech_debt.md` — DASH-9 entry corrected by PR #613; DASH-12/DASH-13 closed by PR #614.
- `docs/operations/pr_merge_discipline.md` — the four-part landing-verify definition applied above.
- `docs/HOUSE_REFLEXES.md` §1 — the registry-parity fan-out discipline `test_system_map.py`
  enforces; §2 — prove-the-control-bites, applied to PR #613's 8 discriminating tests.
- `docs/runbooks/` — new §43 successor-remediation entries for both DASH-12 (restart-dashboard) and
  DASH-13 (review-resolve).
- `docs/enablement/` — operator dashboard enablement doc updated, manifest sha256 re-recorded.
