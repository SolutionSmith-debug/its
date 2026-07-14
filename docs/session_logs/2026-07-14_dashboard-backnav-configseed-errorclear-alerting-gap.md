---
type: session_log
date: 2026-07-14
status: closed
workstream: null
related_prs: [587, 591, 594]
tags: [operator_dashboard, infrastructure, ws2, config, errors, alerting-gap, resend, sentry, launchd, ships-dark, four-part-verify]
---

# Session — Dashboard back-nav, ITS_Config seed, clear-error-log verb, and the out-of-band alerting gap

## Purpose

Started as two small operator questions ("how do I relaunch picklist-sync?", "can the dashboard
change daemon run intervals?") and grew into three landed PRs plus a forensic error-log wipe that
surfaced the session's real finding: both out-of-band CRITICAL alert legs (Resend, Sentry) are
currently down, so triple-fire alerting is local-log-only.

## Pre-flight findings

- **picklist-sync was never down.** Investigation of the "relaunch" question found the daemon
  healthy; no action needed.
- **The dashboard CAN edit daemon intervals, but only for an allowlisted 8 — picklist-sync is not
  one of them.** The Class-B `operator_dashboard/act/daemon_ops.edit_interval` verb (built 2026-07-13,
  PR #570) covers 8 label-allowlisted daemons; picklist-sync's 3600s cadence is a hardcoded
  `StartInterval` literal in its plist, outside that allowlist. Confirmed the general mechanic: a
  bare `ITS_Config` edit never changes a live interval on a one-shot-per-`StartInterval` daemon —
  interval changes require the plist to be rewritten and reloaded, which is exactly what the
  `edit_interval` verb does (row write → `install.sh load <interval>`). This is a real gap
  (picklist-sync unreachable via the dashboard) recorded as a follow-up, not fixed this session.

## Code changes

### PR #587 — dashboard back-nav + panel rename
Replaced the small `← dashboard` text link on inner dashboard pages with a prominent banner-extension
"← Back to dashboard" strip — sticky `.chrome` wrapper + `.subnav`, mirroring the Safety Portal's
`BackHomeNav` pattern (the existing `reference_portal-button-design-language.md` house convention).
Also renamed the heartbeats-panel title "Daemon liveness (local)" → "Daemon status (local)" for
clarity. Verified live in a worktree dashboard instance via Playwright (screenshots captured:
`checklists-*`, `mytasks-*`, `design_language.png` from the same live-render pass).

### PR #591 — seed the 11 remaining un-seeded ITS_Config rows
New migration `scripts/migrations/seed_generate_and_interval_config.py` seeds the 11 rows that were
still missing: 5 `*.poll_interval_seconds` rows and 6 weekly-compile `REQUIRED_CONFIG` keys. Applied
LIVE against the sandbox `ITS_Config` sheet — row count 76 → 87; a re-run is idempotent (11/11
skipped on the second pass). None of the 11 are gates (no dark-capability activation involved). This
is the low-impact residue left over after the `config_row_missing` WARN firehose (95% of ITS_Errors
volume) had already been remediated in an earlier session — not a new discovery, just cleanup.

### PR #594 — clear-error-log dashboard verb
New Class-B verb `operator_dashboard/act/errors_ops.clear_error_log`:
- Terminal-rows-only — **never deletes an open CRITICAL** (the row Check B reads to answer "am I on
  fire").
- Optional older-than-N-days filter.
- Batched at 200 rows / 4600-row cap per invocation.
- Snapshot-then-audit-last ordering (the audit row for the clear itself is written after the
  snapshot, so a mid-run failure never loses the "what got cleared" record).
- Fenced (errors on the clear path don't cascade).

New route `POST /act/errors/clear`; new "Error log (Class B)" section in `config.html`.

Extracted `shared/errors_rotation.py` as the **single source of truth** for the terminality
predicate (which `ITS_Errors` rows are safe to delete). `scripts/watchdog.py` Check O now delegates
to it via thin aliases — zero call-site churn, no behavior change to Check O, just de-duplication of
a predicate that used to live only in the watchdog (HOUSE_REFLEXES §1, "enumerate all
implementations first").

Along the way: CI caught two real defects before merge — a missing enrollment in the
capability-gating network-lib allowlist for `errors_ops`, and a mypy append-in-expression error.
Both fixed pre-merge, not deferred.

12 new tests, including the prove-the-control-bites case: inject an open CRITICAL, run every clear
variant against it, assert it survives all of them.

Ships **dark** (same activation gate as the rest of the dashboard — inert until
`ITS_OPERATOR_PIN` is provisioned).

### Forensic wipe (operator-ratified, not a code change)
Ran the new `clear_error_log` verb against the live `ITS_Errors` sheet in two passes: 6038 terminal
rows deleted. `ITS_Errors` 6249 → 217 (215 open CRITICALs + 2 `errors_log_cleared` audit rows
preserved). Verified post-wipe: open-CRITICAL count unchanged by the wipe.

### Root-cause error chase (operator-directed, diagnosis only — no PR)
Operator asked to "chase" the subcontract / `config_actuator` / send-lane errors visible in the
sheet. Findings:
- All named errors are already resolved or benign — no code fix needed for any of them.
- **The real finding: both out-of-band CRITICAL alert legs are down.** `ITS_RESEND_API_KEY` is
  present in Keychain but invalid (401 on send); `ITS_SENTRY_DSN` is present but empty (`BadDsn`).
  Triple-fire is therefore local-log-only right now — a CRITICAL is recorded in `ITS_Errors` but does
  not reach the operator by email or Sentry capture. This is a P1 follow-up, not fixed this session
  (credential rotation is a §44 high-class action — secrets — reserved for the Developer-Operator).
- A Smartsheet API token flap made the fleet's Smartsheet calls invalid-token fleet-wide from
  17:41–21:06 UTC; self-recovered, no action taken.
- The subcontract 401 traced to a gate-flipped-before-Worker-secret-bound race (an activation-order
  lesson, not a bug in the code itself).
- `po_send_poll (no marker)` traced to the po-send plist never having been installed while its gate
  reads `True` — a deploy/activation gap, not a runtime failure.

### Dashboard recovery (operator-side incident, no PR)
The operator's manual `install.sh load` against the dashboard plist hit
`Bootstrap failed: 5: Input/output error` — a bootout-then-bootstrap race that left the
launchd-managed dashboard down. Recovered with a clean `install.sh load` (no code change involved).

## Verification

Per-PR full gate, run before each merge:

- **PR #587**
  - pytest: full suite green
  - mypy: clean
  - ruff: clean
  - main-branch CI on merge commit: SUCCESS
- **PR #591**
  - pytest: full suite green (migration idempotency exercised live: 11/11 re-run skip)
  - mypy: clean
  - ruff: clean
  - main-branch CI on merge commit: SUCCESS
- **PR #594** (largest change this session)
  - pytest: 227 passed (full suite; targeted subsets 215+ across `errors_ops`/`operator_dashboard`/`watchdog`)
  - mypy: clean — "Success: no issues found in 377 source files"
  - ruff: clean
  - main-branch CI on merge commit: SUCCESS

## Live smoke

The clear-error-log verb's live smoke **was** the forensic wipe itself: run against the real
`ITS_Errors` sheet with the injected-open-CRITICAL prove-it-bites case run first in the test suite,
then the live wipe run in two passes against production-shaped data, with the open-CRITICAL count
checked before and after each pass (unchanged both times). No dry-run-only path was exercised — this
verb was validated against real data, respecting the working assumption that a parallel session's
forensic error data should not be destroyed casually.

## Out-of-scope notes

- picklist-sync's interval was not added to the dashboard's `edit_interval` allowlist — recorded as a
  follow-up, not built this session.
- No fix attempted for the Resend/Sentry alerting gap — credential rotation is reserved for the
  Developer-Operator (secrets are a fixed §44 high-capability class).
- No fix attempted for `config_actuator`'s except-clause shape, the PO-send lane plist gap, or the
  subcontract activation-order race — all diagnosed, none remediated this session.
- A parallel session owned `fix/sc-cfg-2-max-address` (subcontracts Worker, landed separately as
  PR #590); this session deliberately avoided every file under that PR's scope.

## Sequencing context

This session sits directly after the 2026-07-14 debt-zero + security-scrub session (PRs #584-590,
#592-593) on the same day's timeline — that session left `main` at `de83852`; this one starts from
PR #588's tip (`ba87b39`) and lands three more PRs on top (#587, #591, #594), ending `main` at
`36be504`. The clear-error-log verb (#594) depends on nothing from the debt-zero session; it is
new WS2 dashboard scope, the seventh-plus Class-B/C ACT verb on that surface.

What this unblocks: the operator now has a dashboard-side lever to keep `ITS_Errors` from
approaching its row cap without a manual SDK script (Check O still handles unattended rotation;
this verb is the operator-triggered complement). The alerting-gap finding is now a named, tracked
P1 rather than a silent blind spot.

## Operator-side actions remaining

1. **Restore out-of-band alerting (P1).** Rotate `ITS_RESEND_API_KEY` (currently invalid, 401) and
   populate `ITS_SENTRY_DSN` (currently empty, `BadDsn`) in Keychain. Both are §44 high-class
   (secrets) — Developer-Operator only. Until fixed, a CRITICAL is visible only in `ITS_Errors` /
   the dashboard, not by email or Sentry.
2. **`config_actuator` except-clause hardening** — named during the error chase, not yet scoped as a
   PR.
3. **Mark-errors-resolved feature (new, captured this session, not built).** A verb to stamp
   `ITS_Errors.Resolved At`, making a row terminal (and therefore clearable by the existing
   `clear_error_log` button) without waiting for the natural TTL/rotation path. Also implies a
   "dead CRITICAL" triage pass — CRITICALs that are actually resolved but still open because nothing
   ever stamped them.
4. **PO-send lane reconcile** — the po-send plist was never installed while
   `po_send.polling_enabled` (or its poll twin) reads `True`; needs either the plist installed or the
   gate corrected to match reality.
5. **Dashboard native-app repackaging — Option A decided, not built.** `pywebview` + `py2app` into a
   native `.app`, keeping Tailscale for remote access. Captured as the direction for next session,
   nothing implemented yet.
6. **CLAUDE.md doc-drift**: the "What's stubbed vs. real" `operator_dashboard` row and
   `verify_cutover.py` still say "no launchd plist yet" — stale since PR #570 (2026-07-13) added the
   KeepAlive service plist. Pre-existing drift, not introduced this session, but re-surfaced during
   this session's dashboard-recovery incident.
7. Add picklist-sync to the dashboard's interval-edit allowlist, or explicitly document why it's
   excluded, so the "can the dashboard change this daemon's cadence" question doesn't need
   re-investigating next time.

## Cross-references

- `docs/operations/pr_merge_discipline.md` — four-part landing verify definition.
- `docs/HOUSE_REFLEXES.md` §1 ("enumerate all implementations") — the rationale for extracting
  `shared/errors_rotation.py` instead of duplicating the terminality predicate into `errors_ops`.
- `docs/HOUSE_REFLEXES.md` §2 ("prove the control bites") — the injected-open-CRITICAL test behind
  the clear-error-log verb, and the live-wipe-as-smoke pattern.
- `~/its-blueprint/references/claude-code-info-gap.md` §4 — the PR #34-ghost cure this log follows
  (verbatim four-part verify quoting).
- CLAUDE.md `operator_dashboard` "What's stubbed vs. real" row — stale re: launchd plist (item 6
  above); due for correction.
- Prior same-day session log: `docs/session_logs/2026-07-14_debt-zero-and-security-scrub.md`.
- Prior WS2 dashboard completion log: `docs/session_logs/2026-07-13_ws2-operator-dashboard-completion.md`
  (source of the `edit_interval` allowlist and the Evergreen brand pattern this session's back-nav
  strip follows).

## Merge verification quartet output

PR #587 — four-part verify clean (leg 4 confirmed via the immediate-next-commit run #588 `ba87b39`,
green, since #587's own run was cancelled by rapid supersession; mergeCommit
`53b2d57308dda7a9be6dc3e3eeb3108358d01d6f`, mergedAt 2026-07-14T20:22:06Z).

PR #591 — four-part verify clean (mergeCommit `9dca3b03de3ef1f633e8a79fbfe6794a2f185d6f`, mergedAt
2026-07-14T20:58:54Z, main CI ci=SUCCESS run 29367811956, CodeQL=SUCCESS).

PR #594 — four-part verify clean (mergeCommit `36be504836f901921986b4d7772517fdbfb246aa`, mergedAt
2026-07-14T21:12:13Z, main CI ci=SUCCESS run 29368656301, CodeQL=SUCCESS).
