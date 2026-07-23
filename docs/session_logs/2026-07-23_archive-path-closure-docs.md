---
type: session_log
date: 2026-07-23
status: closed
workstream: null
related_prs: [678]
tags: [archive-on-closure, project-closure, section51, runbook, troubleshooting-tree, forensic-verification]
---

# 2026-07-23 — Brief B: project-archive path (§51 closure) — verification + doc deliverables

## Purpose

Execute Brief B against the 2026-07-23 archive-path audit dossiers
(`logs/reviews/2026-07-23_arch_*.json`): prove/prepare the §51 archive-on-closure slice, deliver
a trigger-semantics decision memo for Seth, file a closure-policy proposal for planning-layer
ratification, and land a current-truth closure runbook.

## Pre-flight findings (8-agent parallel verification workflow)

Every dossier claim was re-verified against live HEAD + a read-only live-tenant probe before any
doc was written, rather than trusted:

- **All dossier core claims CONFIRMED at live HEAD**, with two corrections: `move_sheet_to_folder`
  is breaker-guarded but deliberately NOT retry-enrolled (`test_smartsheet_retry.py:649` enforces
  the exclusion); the §30 live-move smoke pins NO stale ids post-rebuild (a whole-file numeric
  sweep found zero hits — the sandbox parent `FOLDER_SYSTEM_CONFIG` resolves live), so no fixture
  fix was needed.
- **Read-only live probe**: `FOLDER_ARCHIVE_CLOSED_PROJECTS` (`4545207418021764`) resolves, 0
  sheets — the destination has never received a write. Per-job progress folders show trackers only
  where post-rebuild mirror passes re-created them.
- **its#462 was closed manually on mocked tests.** PR #465's own body defers the live smoke as an
  "operator pre-reliance step"; zero session-log run records exist. The prove-the-control-bites
  debt is real and was **NOT retired this session** — the live steps are attended-only per the
  brief.
- **NEW finding**: `/hours-pending` has no origin filter while the lifecycle route writes
  `WHERE origin='portal'` — a legacy sheet-origin job that crews clock into accumulates trackers
  that can never auto-archive.
- **SPA display quirk confirmed** (`FieldOpsJobTracker.tsx:544-547`): an Archived job's lifecycle
  selector re-seeds to "Inactive" on reload.
- **Runbook drift**: `hours_log_sync.md` enumerated 2, then 3, trackers; code moves 4 (Material
  Incidents was missing everywhere in that runbook, including its tags).
- **Job creation is portal-only today**: a hand-added `ITS_Active_Jobs` row has no Job ID and
  nothing back-fills one (`active_jobs.py:216-221` drops it) — the old Task A sheet-add flow
  documented in `safety_portal_job_management.md` could never have completed.

## PRs landed

### PR #678 — project-closure runbook + closure-policy proposal (squash-merged `7de61d9`)

Docs-only, four branch commits (`26cf041`/`f390da6`/`da6cd1b`/`7d5aa7a`, plus a sha-record fix
after the CI incident below):

1. **NEW `docs/runbooks/project_closure.md`** — current-truth §43 closure runbook. Both lifecycle
   levers documented with the origin split + a split-brain warning; **Inactive** = passive
   drop-out including the per-row `filed_at`+30d prune clock under watchdog Check V; **Archived**
   = the real 4-tracker move, with the portal-origin-only / no-retry / post-archive-recreation
   caveats plus a dated never-fired-live observation; a retained-surfaces table; and the
   destructive 3-system nuke procedure with HOUSE_REFLEXES §7 row-first ordering. Tree-exempt
   (`docs/troubleshooting/tree.yaml`) — the exemption was proven to bite via inject-confirm-revert
   on `test_every_runbook_referenced_or_exempt`.
2. **NEW `docs/reports/2026-07-23_project_closure_policy_proposal.md`** — dispositions for ~45
   per-job surfaces (23 Smartsheet / 7 Box / 15 D1; 26 currently have no end-of-life), marked
   pending planning-project ratification per §51. Discharges the 2026-06-28 scaling eval's
   never-created `sheet_archival_strategy.md` directive.
3. **`hours_log_sync.md`** — 4-tracker enumeration fixed across 3 stanzas + tags, plus cross-refs;
   **`fieldops_sync.md`** — new Symptom-F cross-ref.
4. **`safety_portal_job_management.md`** — Purpose rewritten, Task A (portal-only creation; the
   portal-owned column set verified against `active_jobs_writer.py`), Task B (the origin split),
   Task D (historical — `portal_poll` went live 2026-06-08, its tombstone was deleted 2026-07-03,
   the uninstall script is gone).
5. **`shared/smartsheet_client.py`** — `move_sheet_to_folder` docstring update, dropping the stale
   "today only the Hours Log" and pointing at the sole live caller. The only non-doc line in the
   PR.
6. **`documentation_index.md`** — runbook count 40→45, enablement manifest sha re-recorded;
   `runbooks/README.md` shape-claim qualified; `tree.yaml` exemption added; indexes regenerated.

**Adversarial review (pre-push, two lenses).** `ops-stds-enforcer` returned 6 findings, 3 fixed
in-PR: a dangling cross-branch citation (resolved by folding the branches into one PR), prune-clock
wording, and the `smartsheet_client` docstring as a 4th stale-enumeration surface; finding 4 noted
the Tier-2 manual-drag classification isn't literally in §44's five-verb low-class set — a
pre-existing pattern ratified in PR #465, flagged for operator awareness and deliberately not
changed. A second, independent accuracy-attacker pass returned 5 defects, all fixed: "all
reversible" vs. pruned D1 rows; the impossible Task A flow; sheet-side Archived being the full
Inactive effect set, not "nothing else"; the split-brain window named explicitly; and the
jobs-row delete guard corrected from "no records at all" to the 8 named tables.

**CI incident.** The first push failed the `test` job: `test_docs_pdf` caught a
`documentation_index.md`-vs-`docs/enablement/manifest.yaml` sha drift (the known enablement-manifest
coupling). `documentation_index.md` entered the diff later as a review fix and the pre-push grep,
which had swept only the runbook files, never re-checked it. Fixed by re-recording the sha
(`build_docs_pdfs --check`: all 22 entries current).

**Local verification**: `test_troubleshooting_tree` 12 passed; `lint_doc_conventions` clean on
touched files; `regen_doc_indexes --check` clean; ruff + mypy clean on `shared/smartsheet_client.py`;
troubleshooting-guide regen byte-identical. Main CI ran the full suite (`test`/`portal`/`secrets`)
green on the merge commit.

Four-part verify (`pr-landed-verifier`, quoted verbatim):

> PR #678 — four-part verify clean
> - state: MERGED
> - mergedAt: 2026-07-23T16:37:21Z
> - mergeCommit: 7de61d93f8a1a0c0a54afd1e943a1a472465ac0c
> - main CI on merge commit: SUCCESS — `ci` workflow (run 30025905069, jobs: test/portal/secrets all success) + `CodeQL` workflow (run 30025904867, success)

## Decisions made during session

1. **Deliver items 1 and 2 as prepared plans / a memo, not as executed live actions.** The §30
   move smoke and the end-to-end archived-job proof (item 1) and the trigger-semantics decision
   (item 2) both require attended operator judgment or a Seth call — the session prepared a full
   plan for each (a disposable portal job → time entry + expected-materials line → trackers →
   Archived flip → verify move + idempotency, with a cleanup recommendation to leave the moved
   trackers as standing evidence; and three trigger-semantics options for item 2) rather than
   running them unattended.
2. **its#462's prove-the-control-bites debt was left open, not silently retired.** The dossier
   pre-flight confirmed the underlying live smoke never ran; closing the issue without that
   evidence would have reproduced the exact PR #34-ghost failure class this repo's discipline
   exists to prevent.
3. **Item 3 (the closure-policy proposal) shipped as an issue + a `docs/reports/` doc, not as
   implemented code.** Per §51, disposition of ~45 per-job surfaces is a planning-layer ratification
   decision; the session's job was to enumerate and propose, not to act unilaterally on a
   cross-system data-retention policy.
4. **Trigger-semantics options were presented, not chosen.** Option A (status-quo-documented),
   Option B (two-step + origin-agnostic watchdog nag — recommended, sequenced after the live
   proof), and Option C (archive-on-inactive-after-grace — not recommended) were all delivered
   in-chat for Seth; none was implemented this session.
5. **Pre-existing drifts found outside this session's lane were flagged, not fixed.**
   `doc_conventions.md:296-298` claims a watchdog `doc_index_regen` `TRACKED_JOBS` wiring that
   doesn't exist; `CLAUDE.md` + `MEMORY.md` cite issue #336 as open when it is actually CLOSED
   (2026-06-29); the 2026-07-04 session log's frontmatter `status: complete` is non-canonical
   (grandfathered under the retrofit policy, not retouched here).

## Open items handed off / next session

1. **(Attended) Run the §30 move smoke**: `cd ~/its && .venv/bin/pytest -m integration -k
   move_sheet_to_folder` → expect "1 passed, N deselected" (possible rerun for eventual
   consistency); then run the end-to-end proof per the prepared plan in-chat.
2. **Seth decides item 2's trigger-semantics option** (A / B / C, memo delivered in-chat).
3. **Planning-layer ratifies/adjusts issue #682's dispositions**
   (`docs/reports/2026-07-23_project_closure_policy_proposal.md`).
4. **After the live proof lands**: comment the evidence on its#462 (retiring the
   prove-the-control-bites debt) and update `project_closure.md`'s dated never-fired-live
   observation.
5. **The `/hours-pending` origin-filter gap** (a legacy sheet-origin job's trackers can never
   auto-archive) is a new finding, not yet filed as its own issue — candidate follow-up.

## What was NOT touched

- No code implementing archive-on-closure semantics beyond the existing `move_sheet_to_folder`
  docstring correction — the slice itself (§30 live smoke, end-to-end proof) is attended-only and
  deliberately deferred to the operator.
- No `ITS_Config` gate flips, no External Send Gate actions, no secret/Keychain writes.
- No doctrine (`~/its-blueprint`) edits — the closure-policy proposal targets planning-layer
  ratification but was not itself written as a doctrine change.
- The pre-existing drifts noted in Decision 5 (`doc_conventions.md` watchdog-wiring claim,
  `CLAUDE.md`/`MEMORY.md` stale #336 reference, the 2026-07-04 log's non-canonical status field) —
  flagged, not fixed; out of this session's lane.
- its#462 was not closed — see Decision 2.

## Cross-references

- `docs/runbooks/project_closure.md` — the new §43 closure runbook this session produced.
- `docs/reports/2026-07-23_project_closure_policy_proposal.md` — the closure-policy proposal
  (issue #682) awaiting planning-layer ratification.
- `logs/reviews/2026-07-23_arch_*.json` — the audit dossiers this session's pre-flight verification
  ran against.
- `its#462` — P7 archive-on-closure follow-up; still open, prove-the-control-bites debt not yet
  retired.
- `docs/session_logs/2026-07-04_smartsheet-verify-hours-smoke-archive-on-closure.md` — the session
  that built + merged the archive-on-closure slice (PR #465) this session audited.
- `docs/HOUSE_REFLEXES.md` §1 (trust live code, never the claim) and §2 (prove the control bites) —
  both directly exercised by the pre-flight verification workflow and the item-1 deferral.
- `docs/operations/pr_merge_discipline.md` — the four-part landing-verify definition applied above.
