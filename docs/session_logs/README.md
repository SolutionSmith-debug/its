# Session logs

Durable narrative record of Claude Code sessions that change the repo. Each log
captures the decisions made *during execution* — the context that doesn't
survive in commit messages, doesn't belong in the Claude.ai planning project,
and would otherwise have to be reconstructed by re-reading transcripts months
later.

## Why these exist

Three records describe the project, and each loses something the others keep:

- **Claude.ai planning project** — decisions *about* the system. Foundation
  Mission, Operational Standards, mission files, the Master Checklist. Stable,
  version-numbered, owner-facing.
- **Commit history** — what changed in the code. Atomic, machine-readable, but
  the *why* gets compressed into one or two paragraphs per commit.
- **Session logs** — decisions made *during* execution: which of two valid
  approaches got picked and the reasoning, what was deliberately left
  untouched, what was flagged for the planning project, what failed before
  succeeding. Bridges the planning project (system-level) and the commit
  history (code-level).

A session log is the answer to "why did 2026-05-17 land this particular set of
commits and not the obvious alternative." Re-reading a transcript six months
later is expensive; re-reading a 50-line log is cheap.

## When to write one

Write a session log when **both** are true:

1. The session lands ≥1 commit.
2. The session involved at least one non-obvious decision — a choice between
   valid alternatives, a deliberate carveout from a project rule, an item
   handed off to the planning project, or a diagnosis that didn't match the
   initial brief.

Don't write one for pure mechanical commits (typo fix, dependency bump,
formatting-only). The commit message is sufficient there.

If unsure: write it. The cost of an unnecessary log is one extra commit; the
cost of a missing log is reconstructing context from transcripts.

## Filename convention

`YYYY-MM-DD_short-slug.md` — date first so chronological sort works in any
file browser; slug short enough to read at a glance.

Examples:
- `2026-05-17_ruff_and_doc_refresh.md`
- `2026-05-20_safety_reports_intake_wiring.md`

If a single date has multiple distinct sessions, append `_2`, `_3`, etc.

## Section ordering

Every log uses this section order. Skip a section only if it would be empty.

1. **Date + session focus** — one sentence at the top, after the H1.
2. **Commits landed** — SHA, title, one-line purpose per commit.
3. **CI runs** — URL, duration, result per run.
4. **Decisions made during session** — one line each. Include the alternative
   that was rejected and the reasoning, not just the choice.
5. **Open items handed off** — anything flagged for the planning project's
   Master Checklist, future sessions, or external systems. Include the
   suggested wording when possible so the recipient can copy-paste.
6. **What was NOT touched** — explicit list. The negative space matters: it
   documents that the omissions were deliberate, not oversights.
7. **Lessons captured to memory** — which memory files were updated and what
   the takeaway was. Cross-references the persistent-memory system so future
   sessions can find the rule even without re-reading this log.

## Planning project vs. session log — what goes where

**Planning project (Claude.ai)** — decisions about the system itself:
canonical doc versions, invariants, architectural choices, workstream missions,
Master Checklist items, schemas, the things that outlive any one session.

**Session log (this directory)** — decisions made during a session, including
the ones that *didn't* reach the planning project: which lint rule to suppress,
which precedent to mirror, why option (b) beat option (a) on a doc link, what
the regex bug was that caused a tally miss. If the decision is about a
specific commit or set of commits, it lives here.

If a session-log decision turns out to be load-bearing for the system
(e.g., "we now have a session-log convention"), the next session that has a
natural reason to touch the planning surface (CLAUDE.md, an Op Std, a mission
file) carries the decision up. Session logs are the staging area; the
planning project is canonical.

## Auto-generated index

Populated by `scripts/regen_doc_indexes.py`. Operator-edited prose lives
outside the sentinel block.

<!-- BEGIN AUTO-INDEX -->
| Date | Type | Status | Workstream | Title | PRs |
|------|------|--------|------------|-------|-----|
| 2026-05-29 | session_log | closed | docs | [2026-05-29 — Exec-side ledger cleanup: FM v8→v9 + Op Stds v13→v14 doctrine bump](2026-05-29_exec-ledger-cleanup.md) | #125 |
| 2026-05-29 | session_log | closed | security | [2026-05-29 — F02 (network-capability allowlist) + F22 (approval-attestation verification)](2026-05-29_f02-f22-capability-approval.md) | #118 |
| 2026-05-29 | session_log | closed | ci | [2026-05-29 — Integration tests silently broken by autouse keychain stub; token-leak redaction](2026-05-29_integration-keychain-stub-fix.md) | #123 |
| 2026-05-29 | session_log | closed | infrastructure | [2026-05-29 — Worktree-isolation fix + agent/workflow optimization audit](2026-05-29_worktree-discipline-and-audit.md) | #121 |
| 2026-05-28 | session_log | closed | infrastructure | [2026-05-28 — Agent-infrastructure follow-ons: session-close-maintainer staleness guard + agent-skills config landed](2026-05-28_agent-infra-followons.md) | #110, #111 |
| 2026-05-28 | session_log | closed | infrastructure | [2026-05-28 — `shared/alert_dedupe.py` → `state_io` migration (PR 2 of Phase 1.4 hardening cluster)](2026-05-28_alert-dedupe-state-io-migration.md) | #104, #88 |
| 2026-05-28 | session_log | closed | docs | [2026-05-28 — Doc-reconciliation: doctrine-version drift + canonical manifest + reconciliation agent](2026-05-28_doc-reconciliation.md) | #101, #103, #106 |
| 2026-05-28 | session_log | closed | infrastructure | [2026-05-28 — F16: wire the external heartbeat ping (Option A)](2026-05-28_f16-heartbeat-ping.md) | #114 |
| 2026-05-28 | session_log | closed | security | [2026-05-28 — Phase 1.4 sweep: F17 (intake_poll watchdog tracking) + F04 (keychain stdin write) + watchdog docstring drift](2026-05-28_f17-f04-docstring-sweep.md) | #113 |
| 2026-05-28 | session_log | closed | security | [2026-05-28 — Forensic-audit remediation (HIGH-1 injection fix + LOW hygiene + HIGH-2 surfacing)](2026-05-28_forensic-audit-remediation.md) | #95, #96 |
| 2026-05-28 | session_log | closed | security | [2026-05-28 — Portal-pivot reconciliation + HIGH-2 supersession](2026-05-28_portal-pivot-reconciliation.md) | #98, #99, #100 |
| 2026-05-28 | session_log | closed | _–_ | [2026-05-28 — Subagent hardening (codeql propose-only + delegation/path/token fixes)](2026-05-28_subagent-hardening.md) | #92, #93 |
| 2026-05-24 | session_log | closed | docs | [2026-05-24 — CC file-based memory consolidation](2026-05-24_cc-memory-consolidation.md) | _–_ |
| 2026-05-24 | session_log | closed | docs | [2026-05-24 — Markdown doc conventions + index generation + lint](2026-05-24_doc_conventions.md) | _–_ |
| 2026-05-24 | session_log | closed | docs | [2026-05-24 — Execution-repo doctrine version drift cleanup](2026-05-24_doctrine-version-drift-cleanup.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-17 — Ruff exemption for box_migration + v5/v6/v7/v4 doc pointer refresh](2026-05-17_ruff_and_doc_refresh.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-17 — Smartsheet workspace restructure (operator vs customer separation)](2026-05-17_smartsheet_workspace_restructure.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — `_alert_critical` Resend wiring + mypy tech-debt closure](2026-05-18_alert_critical_and_mypy_closure.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — box_migration reconcile + parse_subsubject](2026-05-18_box_migration_reconcile.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — restore parse_job v1 + v2 (cascade dependency for v3)](2026-05-18_box_migration_v1_v2_restore.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — doc cleanup: CLAUDE.md doc-version refs and stub/real table](2026-05-18_doc_cleanup.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — error_log Smartsheet write + SDK 404 filter](2026-05-18_error_log_smartsheet_write.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — kill_switch reads ITS_Config; initial seven-row seed](2026-05-18_kill_switch_and_config_seed.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — post-PR-#15 followup (memory + mypy + chore close)](2026-05-18_post_pr15_followup.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — sanity-check sweep](2026-05-18_sanity_check_sweep.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — Sentry triple-fire complete + Phase 1 critical-path unblock](2026-05-18_sentry_and_phase1_unblock.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-18 — smartsheet_client wired against sandbox](2026-05-18_smartsheet_client_wired.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [Session log — 2026-05-19 Cascade Absorb](2026-05-19_cascade_absorb.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-19 — chore sweep + mypy lockdown](2026-05-19_chore_sweep_and_mypy_lockdown.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [Session log — 2026-05-19 Watchdog Session 1](2026-05-19_watchdog_session_1.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [Session log — 2026-05-20 person_tag regex refinement (redo)](2026-05-20_person_tag_regex_refinement_redo.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [Post-Box-Pivot Repo Doc Cascade — 2026-05-20](2026-05-20_post_box_pivot_doc_cascade.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [Session log — 2026-05-20 PTO fetcher wiring](2026-05-20_pto_fetcher_wiring.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [Session log — 2026-05-20 Watchdog Session 2](2026-05-20_watchdog_session_2.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [Alert-routing dedupe ship + picklist sync foundation + V1 fix — 2026-05-21](2026-05-21_alert_dedupe_and_picklist_sync.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-21 — Box 1111A clone cascade](2026-05-21_box_1111a_clone_cascade.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-21 — Intake-test either-path refactor](2026-05-21_intake_test_either_path_refactor.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-21 — R3 Foundation PR](2026-05-21_r3_foundation_pr.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-21 — R3 session 1: intake.py wiring](2026-05-21_r3_session_1_intake_wiring.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-21 — Safety intake heartbeat row writes to ITS_Daemon_Health](2026-05-21_safety_intake_heartbeat.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-21 — Safety intake polling-daemon trigger](2026-05-21_safety_intake_polling_daemon.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-21 — Smartsheet error-translation refactor](2026-05-21_smartsheet_error_translation_refactor.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-22 — Box 1111B blueprint design (1111A forensic + canonical redesign)](2026-05-22_box_blueprint_1111b_design.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-22 — 2026-05-22 cascade absorb (repo-side doc reconciliation)](2026-05-22_cascade_absorb.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-22 — Follow-on fix: transient-404 retry + GENERATION_FAILED placeholder](2026-05-22_followon_404_retry.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-22 — R3 Session 2: safety_reports/weekly_generate.py + WPR pipeline](2026-05-22_r3_session_2_weekly_generate.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-23 — 1111B Box Blueprint Materialization (live build in mirror tenant)](2026-05-23_1111b_materialization.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-23 — CI main-branch keychain fix + four-part verification discipline](2026-05-23_ci_keychain_fix.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-23 — Picklist-hardening pre-Customer-1 (Phase 1.4 #2)](2026-05-23_picklist_hardening.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-23 — Post-1111B canonical cutover (re-clone strategy)](2026-05-23_post_1111b_canonical_cutover.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-23 — R3 Session 3: safety_reports/weekly_send.py + weekly_send_poll.py (closes R3 cycle)](2026-05-23_r3_session_3_weekly_send.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-23 — ITS_Trusted_Contacts + Intake Stage 2 refactor + Header forgery detection](2026-05-23_trusted_contacts_stage2_refactor.md) | _–_ |
| _(no frontmatter)_ | _–_ | _–_ | _–_ | [2026-05-25 — `shared/state_io.py` + atomic-write migration (closes F19 + F23)](2026-05-25_state-io-atomic-write.md) | _–_ |
<!-- END AUTO-INDEX -->

## First entry

[`2026-05-17_ruff_and_doc_refresh.md`](./2026-05-17_ruff_and_doc_refresh.md) —
ruff exemption for `box_migration/*` and the v5/v6/v7/v4 doc pointer refresh
PR #8 left half-done. Also the session that established this convention.
