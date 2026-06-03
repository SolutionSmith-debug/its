---
type: session_log
date: 2026-06-02
status: closed
related_prs: [149, 150]
workstream: infrastructure
tags: [config-migration, project-routing, picklist-drift, smartsheet, sdk-vs-live, ship-and-leave]
---

# Session log — E1 project-routing migration + first picklist-drift reconcile

Two pieces this session: **E1** (Tier-E keystone, landed) and the **first-ever
picklist-drift reconcile** (Phase 1+2 landed in an open PR; Phase 3 deferred to
Seth). Session ended on Seth's "shut down — capture everything uncompleted so
tomorrow picks up, lose nothing."

## Commits / PRs landed

- **PR #149 (MERGED, squash `ee82c4c`)** — E1: migrate hardcoded
  `shared/defaults.py:BOX_PROJECT_FOLDERS` → `ITS_Project_Routing` Smartsheet
  sheet. New `shared/project_routing.py` (TTL-cached reader, sheet→fallback→""
  ladder, warn-not-crash), build+seed migrations, `SHEET_PROJECT_ROUTING`
  placeholder, `intake.py` consumer swap, 17 unit + 1 (then 2) §30 integration
  tests, §43 onboarding runbook. **Pre-cutover = zero behavior change.**
- **PR #150 (OPEN, ready)** — picklist-drift reconcile. Commits: Phase-1
  classification doc; Phase-2 `ensure_picklist_options` + tests + §43 runbook;
  review-fix (single-pass/generator-safe); Phase-3 tech-debt capture; a
  merge-in of main (E1) resolving the runbook auto-index conflict.

## CI runs

- #149 main-CI on `ee82c4c`: SUCCESS (run 26858557175; test 59s, CodeQL,
  secrets). Four-part verify CLEAN via `pr-landed-verifier`.
- #150 head CI: all checks pass at shutdown (test/CodeQL/Analyze/secrets).
  Seth re-verifies as part of the four-part merge tomorrow.

## Decisions made during session

- **E1 review (2 minor, both folded):** stale §42 docstring at `intake.py`
  pipeline-step 10 (still named `BOX_PROJECT_FOLDERS[...]`); integration test
  didn't exercise the wired-sheet fallback-WARN live → added a second live test.
- **Picklist finding #1 classified LATENT, not active-break** (corrects the
  brief's implied "active break? YES"). Live `Reason` column has
  `validation: false`, so the 3 enum values write as free-text successfully —
  impact is operator-dropdown + pivot-bucketing only, not data loss. Verified by
  raw column dict + scanning all 186 ITS_Errors rows (no write rejection; the
  only hit is the audit's own finding) + 0 live rows carrying the values.
- **Findings #2/#3 classified DORMANT** — column absent live AND no code writer
  (registry over-declares). Confirmed against `error_log.py` / `quarantine.py`.
- **`ensure_picklist_options` is ADDITIVE, built on the existing REPLACE-style
  `update_column_options`** (rejected: using `picklist_sync` — it's sheet→sheet,
  not registry→live; rejected: a bare `update_column_options` — it replaces the
  whole list and would drop existing options). Read current → append missing
  (order preserved) → never remove. Preview-then-apply on the live sheet (9→12).
- **Adversarial review of the live-mutating helper found a CRITICAL**
  (generator double-iteration silently emptied `already_present`) + dedup/
  empty asymmetry + an integration-test order-flake. Fixed (single-pass;
  live-re-read asserted as a set since Smartsheet doesn't guarantee option
  order) rather than deferred — leaving a known-CRITICAL in an open PR would
  violate "lose nothing." Re-validated LIVE after the fix.
- **Branch-name slip:** first push of the E1 branch accidentally targeted
  `picklist-drift-reconcile` (empty, at `ba2c833`); re-pushed `e1-project-
  routing-sheet` correctly. The stray remote branch was then reused as #150's
  base (no harm). Force-push/delete are hook-blocked, so it couldn't be cleaned
  from CC.

## Open items handed off (DECISIONS for Seth — picked up next session)

Captured as tech-debt in `docs/tech_debt.md` (#150 branch) + the
`session-2026-06-03-picklist-e1-state` memory:

- **Merge #150** (verify CI green on head + four-part verify). The live `Reason`
  remediation is ALREADY applied — do not re-apply.
- **Phase 3a** — the two DORMANT columns: trim-registry (CC rec) vs add-empty-
  columns vs defer-keep-WARN.
- **Phase 3b** — no automated registry→live apply (the systemic ship-and-leave
  gap that let `Reason` drift): (a) automate additive `--apply` on
  `audit_picklist_drift.py` (CC rec; **do not build without sign-off**) vs
  (b) document-only.
- **E1 operator cutover** — run build+seed migrations, fill
  `SHEET_PROJECT_ROUTING`, run the integration test, verify parity, retire the
  hardcoded dict.

## What was NOT touched

- No defense-layer / quarantine-decision / review-queue-routing logic changed —
  picklist work is schema-only (Invariant 2 untouched). Invariant 1 not
  implicated (internal Smartsheet writes only).
- Did NOT wire a `Workstream` writer into `error_log` or a `Disposition` writer
  into `quarantine` (explicitly out of scope).
- Did NOT build the Phase-3b `--apply` tool (needs Seth's sign-off).
- Did NOT remove any picklist option or column anywhere (additive-only).

## Lessons captured to memory

- `session-2026-06-03-picklist-e1-state.md` (NEW) — end-of-session state +
  tomorrow's pickup list (the durable anti-loss anchor).
- Reinforces [[feedback_mandatory-live-smoke]] — the helper was live-validated
  before AND after the review fix; the adversarial review caught a CRITICAL the
  mocks-only unit tests missed.
- Audit-flag gotcha recorded: `audit_picklist_drift.py` dry-run flag is
  `--no-emit` (not `--no-error-log`).
