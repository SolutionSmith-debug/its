---
type: session_log
date: 2026-06-03
status: closed
related_prs: [155, 156]
workstream: safety_portal
tags: [safety-portal, smartsheet, config-sheets, sdk-vs-live, forensic-audit, alignment, ci-ghost-check]
---

# Session log — Safety Portal config sheets + unifying alignment audit

Second session of 2026-06-03 (first covered PRs #151–#153; see
[`2026-06-03_phase3a-3b-e1-cutover.md`](2026-06-03_phase3a-3b-e1-cutover.md)).
Built the two Smartsheet config sheets the Safety Portal reads as its only
Smartsheet inputs (ITS_Active_Jobs + ITS_Forms_Catalog), then committed the
propose-only unifying forensic alignment and drift audit produced earlier in
the session via a multi-agent workflow.

## Commits / PRs landed

- **PR #155 — feat(safety-portal): build ITS_Active_Jobs + ITS_Forms_Catalog
  config sheets** — squash `141a5738`. Created a dedicated "Safety Portal" folder
  (id 6663869084002180) in the ITS — Operations workspace; built
  ITS_Active_Jobs (id 6223950341164932) and ITS_Forms_Catalog (id
  423274885369732). Added `find_folder_by_name_in_workspace` and
  `create_folder_in_workspace` to `shared/smartsheet_client.py` (workspace-level
  folder primitives missing from the SDK surface; direct REST +
  `@_breaker_guard` + §42 docstrings). Seeded 6 jobs (Project Name = display
  name matching ITS_Project_Routing; Job ID = kebab-case e.g. `bradley-1`;
  Address BLANK by policy) and 4 locked v1 forms (jha-v1,
  daily-site-safety-v1, equipment-preinspection-v1, toolbox-talk-v1).
  `picklist_validation.REGISTRY` entries for both Active columns are
  GUARDED on non-zero sheet IDs. §30 integration test
  (`tests/test_safety_portal_config_sheets_integration.py`). §43 runbook
  (`docs/runbooks/safety_portal_config_sheets.md`). LIVE in production tenant.

- **PR #156 — docs(audit): unifying forensic alignment & drift audit** — squash
  `9e4b51b1`. Committed the propose-only meta-audit document
  (`docs/audits/2026-06-03_unifying-alignment-audit.md`, status: draft) produced
  via a multi-agent workflow. Verdict: doctrine/code well-aligned — NO Critical
  drift, no surviving High after adversarial verification. Corrected several
  stale claims surfaced during the audit run.

## CI runs / four-part verify

PR #155 — four-part verify clean:
- state: MERGED · mergedAt: 2026-06-03T18:52:04Z · mergeCommit: 141a5738
- main CI on merge commit: SUCCESS (run 26906081812 — test ✓, secrets ✓)

PR #156 — four-part verify clean:
- state: MERGED · mergedAt: 2026-06-04T01:12:43Z · mergeCommit: 9e4b51b1
- main CI on merge commit: SUCCESS (run 26923636762 — test ✓, secrets ✓)

Per-session local gate before merge:

- pytest: 1305 passed / 0 skipped / 33 deselected
- mypy: 0 errors / 158 source files (Success: no issues found in 158 source files)
- ruff: clean (All checks passed!)
- §30 integration test (live): 2 passed
- audit_picklist_drift --no-emit: 0 findings (6 registered sheets)
- main-branch CI on merge commit: SUCCESS for both PRs

## Decisions made during session

1. **Safety Portal folder placement: new dedicated folder, not Master Databases
   or workspace root.** Alternative considered: placing the two sheets in the
   existing "Master Databases" folder (the obvious home for system-configuration
   sheets). Rejected by Seth: the Safety Portal is a distinct product surface;
   isolating it in its own folder keeps operator navigation clear and
   anticipates future portal-specific sheets. Workspace root was also considered
   and rejected (too flat). Outcome: new "Safety Portal" folder created in
   ITS — Operations workspace.

2. **Flag convention: live-default + `--dry-run`, NOT preview-default + `--commit`.**
   The brief proposed preview-default (`--commit` to apply). Alternative
   confirmed by Seth: match the E1 migration convention established in the
   06-03 session (live-default, `--dry-run` to preview). Rationale: internal
   config-sheet migrations are low-risk, idempotent, and should be consistent
   with the precedent just set rather than introducing a second flag convention.

3. **Job ID as a new kebab-case layer, separate from Project Name.** The portal
   brief §3 specifies Job ID as kebab-case (e.g., `bradley-1`). Existing
   canonical project keys are Title-Case display names ("Bradley 1") that map
   through ITS_Project_Routing to Box folders. Alternative considered: using
   the existing display name as Job ID, accepting the casing mismatch.
   Rejected: the portal needs a stable URL-safe key; the display name carries
   different semantics. Project Name column carries the display name (preserving
   the ITS_Project_Routing lookup path); Job ID is a new portal-specific layer.
   These are two distinct identifiers.

4. **Addresses seeded BLANK on all 6 jobs.** Op Stds §4 forbids inventing
   field data; no structured live source for job addresses exists. Alternative
   considered: sourcing from Smartsheet or Box documents. Rejected: addresses
   are not reliably machine-readable from those systems at this stage. Office PM
   fills them manually; Work Location auto-fill on the portal is empty until
   then. Recorded in `docs/tech_debt.md`.

5. **Workspace-level folder primitives added to `shared/smartsheet_client.py`
   rather than calling the REST API inline in the migration script.** Only
   folder-in-folder primitives existed (`find_folder_in_folder`,
   `create_folder_in_folder`). A workspace-level create requires a different
   REST endpoint. Alternative considered: inline REST call in the migration
   script only. Rejected: the pattern is reusable (future workstreams may need
   workspace-root folders), and `shared/smartsheet_client.py` is the canonical
   home per Op Stds §42. Added `find_folder_by_name_in_workspace` +
   `create_folder_in_workspace` with `@_breaker_guard` + §42 docstrings.

6. **SDK-vs-live pre-verification (Op Stds §30): confirmed `smartsheet.models.Column`
   maps `systemColumnType` (MODIFIED_DATE → DATETIME, MODIFIED_BY →
   CONTACT_LIST) before building.** Alternative considered: trust the SDK docs
   and build without checking. Rejected per §30 discipline — SDK-vs-live
   discrepancies are a recurring failure class; offline verification before
   a live-mutating call is canonical. Confirmed live via the §30 integration
   test in `tests/test_safety_portal_config_sheets_integration.py`.

7. **`picklist_validation.REGISTRY` guard on non-zero sheet IDs for both Active
   columns.** Matches the ITS_Trusted_Contacts precedent established in an
   earlier session. The placeholder-0 window between migration and flip stays
   inert for the live picklist-sync daemon — it does not attempt to sync an
   unregistered sheet. Alternative considered: registering with sheet ID
   immediately (no guard). Rejected: the daemon runs hourly; a brief window
   with a placeholder ID risks a sync attempt against an invalid target.

8. **Audit status: draft, not canonical.** The unifying alignment audit
   (PR #156) is a propose-only artifact; Seth reviews findings before any
   doctrine updates. Still-open items surfaced by the audit that were NOT acted
   on this session: DR-D1 guard-hook self-presence (H1) unfixed; DR-C2
   adversarial Layer 6 attachment screening unbuilt; DR-E1 ops-stds-enforcer
   agent pinned at "Op Stds v13".

9. **CI ghost stuck-check recurrence noted.** A `pull_request`-triggered run
   check stuck at IN_PROGRESS while the PR was MERGEABLE/CLEAN — same pattern
   as prior sessions. Workaround: verify via run-level `conclusion` +
   `mergeStateStatus` rather than waiting for `gh pr checks --watch` to
   resolve. Not escalated to a fix this session (the four-part verify's
   leg 4 is satisfied by the post-merge main-branch CI run, not the PR-level
   check).

## What was NOT touched

- No Invariant 1 or Invariant 2 mechanics changed — PR #155 is purely additive
  (new sheets, new folder, new shared primitives). No send-path or AI-path touched.
- The Safety Portal application code itself (form rendering, HMAC shim, portal
  backend) was not touched — this session only built the two Smartsheet sheets
  the portal reads.
- The 6 Address cells in ITS_Active_Jobs are BLANK by decision; they were not
  filled and are not stubbed.
- Audit findings DR-D1, DR-C2, DR-E1 (surfaced by PR #156) were NOT acted on —
  the audit is propose-only; no doctrine or code changes from those findings in
  this session.
- `lint_doc_conventions.py`'s workstream set was NOT updated to include
  `safety_portal`.
- CLAUDE.md watchdog entry ("6 of 7") was NOT corrected to 11.
- No launchd plists were added for the Safety Portal (portal runs its own
  hosting model; this is a config-sheet-only phase).

## Open items handed off

- **Fill the 6 Address cells** in ITS_Active_Jobs (id 6223950341164932) — PM
  fills these manually from site records. Work Location auto-fill on the portal
  will populate once addresses are present.
- **Add `safety_portal` to `lint_doc_conventions.py`'s workstream set** — the
  new workstream directory should be recognized by the doc conventions linter.
- **Fix CLAUDE.md watchdog "6 of 7" → 11** — the stub/real table entry is stale;
  the watchdog now has 11 checks (Checks A, B, C, D, F, G, I + corrected count).
  Corrected in the audit doc; needs to propagate to CLAUDE.md.
- **Audit follow-ons (propose-only):**
  - DR-D1: guard-hook self-presence test (H1) — hook at
    `.claude/hooks/block-dangerous-git.sh` has no test that the hook file is
    present and executable. Propose adding a CI check.
  - DR-C2: adversarial Layer 6 attachment screening — unbuilt; assigned to
    Email Triage workstream per portal-pivot. Schedule for Phase 1.4 hardening.
  - DR-E1: `ops-stds-enforcer` agent frontmatter cites "Op Stds v13" — should
    be v16. Low-risk (description-only), but should be corrected.
- **CI ghost stuck-check** — the `pull_request`-run stuck-at-IN_PROGRESS pattern
  recurred. No fix was applied. Consider opening a GitHub issue or adding a
  doc note to `docs/operations/pr_merge_discipline.md` on the workaround
  (run-level conclusion + mergeStateStatus).

## Gotchas worth recording for future sessions

- **CI ghost stuck-check workaround.** A `pull_request`-triggered check can
  stick at IN_PROGRESS while the PR is MERGEABLE/CLEAN. Do not wait for
  `gh pr checks --watch` indefinitely. Verify PR merge eligibility via
  `gh pr view --json mergeStateStatus` and leg 4 via the post-merge
  main-branch CI run conclusion directly.
- **Two-layer project identifier.** ITS_Active_Jobs has both `Project Name`
  (Title-Case display name, ITS_Project_Routing lookup key) and `Job ID`
  (kebab, portal URL key). These are intentionally distinct; do not collapse
  them.
- **Workspace-level vs folder-level folder creation.** The Smartsheet SDK
  exposes folder-in-folder only; workspace-level folder creation is a direct
  REST call. The new `shared/smartsheet_client.py` primitives encapsulate this
  distinction; use them rather than re-implementing inline.

## Lessons captured to memory

- Prior-session memory `session-2026-06-03-picklist-e1-state.md` remains the
  current-state anchor; this session's outcome is an addendum (two more PRs
  landed on same date, covered in this log).
- CI ghost stuck-check workaround reinforced — same pattern as prior occurrences;
  the four-part verify discipline is the correct guard (leg 4 = post-merge
  main-branch CI, not the PR-level check).

## Cross-references

- Prior same-day session log:
  [`2026-06-03_phase3a-3b-e1-cutover.md`](2026-06-03_phase3a-3b-e1-cutover.md)
- Op Stds v16 §30 (SDK-vs-Live integration test discipline)
- Op Stds v16 §42/§43 (self-documentation + successor-remediation DoD)
- Op Stds v16 §4 (no invented field data)
- `docs/runbooks/safety_portal_config_sheets.md` — §43 successor-remediation runbook
- `docs/audits/2026-06-03_unifying-alignment-audit.md` — propose-only; status: draft
- `docs/operations/pr_merge_discipline.md` — four-part verify; CI ghost stuck-check
- `docs/tech_debt.md` — Address cells entry (new)
- Safety Portal mission: `../its-blueprint/workstreams/safety-portal/mission.md` v1 §§3, 7
