---
type: session_log
date: 2026-07-04
status: closed
workstream: progress_reporting
related_prs: [459, 461, 463]
tags: [session_log, progress-reporting, field-ops, compile-now-poll, parameterize-not-clone, picklist-registry-parity, p7-hours-log, section51-rider, doctrine-rider, ops-stds-enforcer, ci-billing-incident, held-pr, four-part-verify, README-recovery, single-standing-sheet, row-cap-split]
---

# Session — Progress-Reporting go-live (Track 0) + P7 Slice 1 Hours Log up-sync held on §51, cleared via v19.x rider (exec #459/#461/#463, blueprint #58)

Spans 2026-07-03 evening → 2026-07-04. Two tracks: **Track 0** closed out the Progress-Reporting
go-live gap left from the 2026-06-30 P4/P5 sessions (a missing config row was silently skipping
progress rollups, and a latent picklist-parity bug meant any progress-workstream Review-Queue
`add()` would have raised at write time), landing one parameterize-not-clone daemon consolidation.
**Track 2 P7 Slice 1** (per-job Hours Log up-sync, extending the working `fieldops_sync.py` job-identity
mirror) was fully built, then **HELD at merge time** by `ops-stds-enforcer` on a real §51 conflict — the
single-standing-sheet design it shipped satisfies §51's period-split *intent* but not its *literal
never-delete-rows-and-archive-on-closure* text. The operator chose to ratify a v19.x doctrine rider
rather than rework to calendar period-split (the anti-sheet-proliferation call, per the 20×20 scaling
eval's #1 finding), and the slice landed dark once the rider cleared. A mid-session GitHub Actions
org-wide billing outage stalled both merges for several hours — diagnosed as non-code (job annotation
named a SolutionSmith-debug spending-limit exhaustion), not chased as a CI regression. A stale
top-level `README.md` was also rewritten and PR #324's forensic scaling-eval report recovered onto
latest main.

## Arc 1 — Track 0: Progress-Reporting go-live gap closure

The 2026-06-30 P4/P5 sessions built the progress compile/send pipeline but left it dark behind two
undiscovered gaps, both found and closed this session:

- **`ITS_Config` — duplicated `safety_reports.portal.worker_base_url` under `Workstream=progress_reports`**
  (live edit via MCP, no code change). `progress_weekly_generate.py`'s `_resolve_rollup_creds` was
  returning `None` on the missing workstream-scoped row — the rollup page was silently skipping every
  cycle, consistent with the "`ITS_Config` reads are workstream-scoped" reflex (`docs/HOUSE_REFLEXES.md`
  §5). Also set `progress_reports.progress_send.from_mailbox=progress@evergreenmirror.com`.
- **Latent picklist-parity bug, found and fixed**: `ITS_Review_Queue`'s Workstream picklist (column
  `4163625765080964`) was updated to include `progress_reports`, and — the load-bearing half —
  `shared/picklist_validation._WORKSTREAM_VALUES_GLOBAL` was updated to match. The REGISTRY set had
  lacked `progress_reports` while `review_queue.VALID_WORKSTREAMS` already had it, so any progress
  compile's per-job fence or capacity-breach path calling `review_queue.add(workstream="progress_reports")`
  would pass its own local check and then raise `PicklistViolationError` inside `add_rows` — `add_rows`
  is gated by `validate_row` exactly like `update_rows`. A parity test was added. Confirms the
  "picklist REGISTRY must include all daemon values" memory lesson generalizes beyond `update_rows`.
- **§46 resolved, no action needed.** `list_workspace_share_emails` (`?includeAll=true`) showed both the
  safety workspace (`194283417429892`) and the progress workspace (`5988851429730180`) shared to
  `seths@evergreenmirror.com` only, who is OWNER of the progress workspace — a non-empty approver set,
  so WPR sends are approvable. The audit's "empty-share fails-closed" risk never actually applied.

**#459 `cb58ca8` — `compile_now_poll` generalized (§14 parameterize-not-clone).** One daemon now
iterates `COMPILE_CONFIGS = (SAFETY_GENERATE_CONFIG, PROGRESS_GENERATE_CONFIG)` via
`generate_core._compile_job_week` / `_safe_review_queue`, gated per-workstream by
`<ws>.compile_now_poll.polling_enabled`, with one plist / lock / heartbeat / Check-C marker. The leaf
daemon imports `progress_weekly_generate` directly — no import cycle; the capability gate is enforced
per-file via AST, unaffected. New §43 runbook `docs/runbooks/compile_now_poll.md`. Both
`ops-stds-enforcer` and a skeptic correctness reviewer returned CLEAN. Deployed: operator pulled `~/its`
to `cb58ca8` and kickstarted `compile-now-poll`; deploy smoke `poll_once()` returned
`jobs_scanned=6` (3 safety + 3 progress), 0 errors.

PR #459 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-04T03:45:58Z
- mergeCommit: cb58ca8a155b6e378b2096301b179bd3764ca9f8
- main CI on merge commit: SUCCESS (run 28693939984)

Live validation after deploy: `progress_weekly_generate` ran against 3 real jobs (JOB-000017/18/27),
0 errors, 0 Review-Queue drain, confirmed idempotent-skip on re-run. The new consolidated daemon was
live-smoked end to end by forcing a triggered progress compile through to a PENDING
`WPR_human_review` row with `Workstream=progress` clearing the live picklist gate — the trigger was
then cleared and the smoke rows deleted.

**Operator follow-up filed, not built this session**: `its#460` — create the
`progress@evergreenmirror.com` mailbox and add it to the Entra app's Application Access Policy
(Mail.Send). Progress sends are HELD-at-approval, never silently drop, until that lands.

## Arc 2 — Track 2 P7 Slice 1: per-job Hours Log up-sync (built → §51 BLOCK → operator Path B → landed dark)

A design digest of `~/.claude/plans/ok-we-are-going-scalable-flamingo.md` against
`~/its-blueprint/workstreams/progress-reporting/mission.md` §11–16 corrected a stale claim in the
flamingo plan: `field_ops/fieldops_sync.py` is **already a working job-identity mirror** (PRs
#387/#389), not a skeleton — P7 extends it with additional per-tracker mirror passes rather than
building a new daemon.

**Built (#461, merge `71feb62`):**

- Migration `0038_time_entries_mirror.sql` — `time_entries.mirrored_at` per-row watermark + a partial
  pending index. A per-row flag, chosen over a high-watermark cursor specifically so an amend/void is
  amend-correct (a later correction to an already-mirrored row re-queues cleanly).
- Worker `GET/POST /api/internal/fieldops/hours-{pending,mark-mirrored}` — `requireFieldopsToken`, bound
  SQL, one atomic `db.batch` + one summary audit row per call, idempotent `UPDATE … WHERE mirrored_at IS
  NULL`, `INNER JOIN jobs` for project name, `LEFT JOIN personnel` for the display-name-only WHO field.
- `progress_reports/hours_log.py` — per-job Hours Log find-or-create, an A1 capacity margin-check,
  idempotent upsert-by-Entry-UUID, amend-supersede, and `check_row_cap` — a SoR-safe row-cap WARN
  watchdog (never a delete).
- `field_ops/fieldops_sync.py::_mirror_hours_pass` — runs inside the existing daemon (one lock, one
  heartbeat), gated OFF by default via `field_ops.fieldops_sync.hours_enabled`; per-entry and per-job
  fences route permanent failures to Review Queue (`workstream=progress_reports` — exercising the same
  parity fix from Arc 1); `mark-mirrored` fires LAST so a mid-pass crash is idempotent-safe on retry.
- `shared/portal_client.py` — `get_fieldops_pending_hours` / `mark_fieldops_hours_mirrored`.
- §43 runbook `docs/runbooks/hours_log_sync.md`; `safety_portal/README.md` migration punch-list row +
  activation section.

**Decision made during build**: single standing sheet per job + row-cap-split (not calendar
period-split), progress workspace only, single destination. This was the default I selected as the
recommended path when an `AskUserQuestion` on the sheet-storage model went unanswered within the
60-second window — not an operator-confirmed choice at build time.

**§51 BLOCK.** `ops-stds-enforcer` returned BLOCK on the built slice: Op Stds v19 §51 (live text,
line 847) names "accumulating logs are period-split + archived-on-closure, never `delete_rows`, under
an A5 row-cap watchdog" as required definition-of-done for exactly this class of sheet. The slice as
built implements never-delete and the row-cap watchdog, but the single-standing-sheet design directly
conflicts with §51's *period-split* requirement — verified against live doctrine text, not a stale
citation. The slice was HELD (opened as a DRAFT PR) rather than merged past the BLOCK, and the two
paths were surfaced to the operator: (A) rework to calendar period-split per §51 as literally written,
or (B) ratify a v19.x doctrine rider narrowing §51's period-split requirement for this class of
low-volume log.

**Operator chose Path B** — ratify the rider, don't rework to calendar period-split. Rationale: sheet
proliferation is the #1 finding of the 2026-06-28 20×20 scaling eval (`project_scaling-eval-20x20`
memory), and calendar period-split multiplies sheet count in exactly the direction that audit flagged
as the top scaling risk; a single standing sheet with a row-cap-triggered split is the anti-proliferation
posture for a genuinely low-volume log.

**Path B executed — blueprint #58 `1202418`.** A v19.x amendment rider (frontmatter `version: 19`
unchanged — no §N added, removed, or renumbered, and the protective claim is unchanged) following the
2026-07-03 Sentry-reclassification rider's precedent and its own "why this isn't a v20 bump" reasoning.
The rider clarifies that §51's period-split requirement, for a **low-volume accumulating log**, is
satisfied by a single standing sheet whose split is *triggered* by the A5 row-cap WARN watchdog rather
than pre-emptively calendar-split. `check_row_cap` (already built above) implements the trigger: WARN +
a Review-Queue row proposing an operator-run period-split as the sheet nears the ~20k-row cap — never a
`delete_rows` call.

- **`its#462`** — the one still-committed §51 follow-up: archive-on-closure needs a new
  `smartsheet_client` move-sheet method (§30 integration discipline). The stranded-sheet exposure this
  leaves open was corrected during review from an initial "distant" framing to a **bounded, recoverable**
  one — the archived-lifecycle trigger (job closure) is already live via the portal admin surface, so the
  gap is "closed jobs' Hours Log sheets sit un-archived in the live workspace until #462 lands," not an
  unbounded risk. Must land before any archival activity actually runs.
- `ops-stds-enforcer` re-review after the rider: BLOCK cleared, verdict **WARN** with two residual
  judgment calls both explicitly handled — merge blueprint #58 *before* exec #461 (doctrine-then-code
  ordering), and the archive-on-closure gap is doc-tracked (not code-gated) against a live trigger.

PR #461 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-05T02:05:57Z
- mergeCommit: 71feb62fcaadc89bfbef90b5a01e3867c134f9e4
- main CI on merge commit: SUCCESS (run 28726487600)

Blueprint PR #58 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-05T02:03:54Z
- mergeCommit: 12024188989d17fb7c35fb6c6a479807708d0e1e
- main CI on merge commit: SUCCESS (run 28726435827)

Note on #58's status history: the blueprint repo's `statusCheckRollup` shows an earlier `lint` run
`FAILURE` (completed 2026-07-04T16:38:40Z) before the `SUCCESS` run that actually gates the merge
commit (completed 2026-07-05T01:57:43Z) — this is the CI billing incident below, not a doctrine-content
lint failure; the rider content itself was never rejected by lint.

## Arc 3 — CI billing incident (mid-landing, resolved, not code)

Mid-way through landing the §51 rider + P7 slice, GitHub Actions failed **org-wide across both repos**
with a job annotation reading "job was not started because recent account payments have failed or your
spending limit needs to be increased" — a `SolutionSmith-debug` billing/spending-limit exhaustion, not
a code failure. All linters and tests had passed locally throughout; this was diagnosed from the job
annotation, not chased as a regression. The operator updated payment and subscription level, CI was
rerun green, and the two PRs were merged in the correct order — blueprint #58 first, exec #461 second
— per the ops-stds re-review's ordering call above.

## Arc 4 — Docs: stale README rewrite + #324 recovery

**#463 `254e121`** — the top-level `README.md` predated the Safety Portal's Cloudflare
Worker/D1/SPA, `progress_reports`, `field_ops`, the 11 launchd daemons, and the current invariants;
rewritten to current state. Also recovered PR #324's content onto latest main: the 1354-line forensic
20×20 scaling-eval report and its 65-line session log, both carried over **verbatim**, plus the
tech-debt Tier-A section verbatim with an added provenance/status note (most of those items have since
shipped across #326/#327/#345/#346/#349/#437). Doc indexes regenerated. PR #324 itself was CLOSED as
superseded rather than merged.

PR #463 — four-part verify clean
- state: MERGED
- mergedAt: 2026-07-05T02:32:48Z
- mergeCommit: 254e12126a400b5a471088b32fb4ddc8632d0390
- main CI on merge commit: SUCCESS (run 28727058896)

## Final gate (P7 #461, the largest PR this session)

- pytest: 2391 passed / 47 deselected
- mypy: clean / 246 source files
- ruff: clean
- worker vitest: 780
- doc-index: clean
- blueprint linters: clean
- main-branch CI on merge commit: SUCCESS

## Decisions made during session

1. **Path B — ratify a v19.x §51 rider rather than rework P7 Slice 1 to calendar period-split**
   (operator). *Rejected alternative*: rework the built slice to calendar-period sheets as §51's literal
   text requires. *Rationale*: the 20×20 scaling eval named sheet proliferation the #1 scaling risk;
   calendar period-split for a low-volume log multiplies sheets in exactly that direction, while a
   single standing sheet with an A5 row-cap-triggered split satisfies the same protective intent
   (never unbounded growth, always recoverable) without the proliferation cost.
2. **Single standing sheet + row-cap-split, progress workspace only, single destination** — the
   default I selected when the sheet-storage-model `AskUserQuestion` went unanswered within 60 seconds.
   Not an operator-confirmed choice at design time; it is the design that subsequently triggered the
   §51 BLOCK and was ratified retroactively via Path B, rather than picked to conform to doctrine
   up front.
3. **Held the slice at the §51 BLOCK rather than merge past it.** `ops-stds-enforcer`'s BLOCK cited
   live doctrine text (§51, line 847), not a stale or paraphrased claim — verified before acting on it
   per the "trust the live code" reflex. Doctrine is Seth-owned under the §44 both-rule; the slice was
   opened as a DRAFT PR and both paths (rework vs. rider) were surfaced rather than either silently
   reworking or silently merging past a doctrine conflict.
4. **archive-on-closure (its#462) deliberately left as a follow-up, not built this session** — it needs
   a new `smartsheet_client` move-sheet method (§30 integration discipline), which is its own scoped
   piece of work. The exposure this leaves was explicitly reframed during review from an unbounded
   "distant risk" to a bounded/recoverable one (the archival trigger is already live; only the actual
   move-sheet action is missing) — a corrected risk statement, not a downgrade of urgency.
5. **Calendar period-split for Hours Log was NOT built and is not planned** — the row-cap-triggered
   single-standing-sheet model is the accepted permanent design for this log, formalized by the §58
   rider, not an interim state pending a future period-split build.
6. **CI billing failure diagnosed as non-code and not chased as a regression.** The job annotation
   named a payments/spending-limit cause explicitly; local lint/test runs had been green throughout the
   incident window, so no code investigation was opened — the operator resolved it as an account-level
   fix and CI was simply rerun.
7. **Doctrine-then-code merge ordering (blueprint #58 before exec #461)** — carried over from the
   ops-stds re-review's residual judgment call; the code that depends on the rider's clarified §51
   reading should not land ahead of the rider itself.

## Open items / next session

- **`its#460`** — create the `progress@evergreenmirror.com` mailbox and add it to the Entra
  Application Access Policy (Mail.Send). Progress sends stay HELD-at-approval (never silent) until
  this lands; no code change needed on the ITS side.
- **`its#462`** — archive-on-closure for standing per-job Hours Log sheets. Needs a new
  `smartsheet_client` move-sheet method per §30 integration discipline; must land before any archival
  activity runs against a closed job's Hours Log sheet. The archival *trigger* (job closure via the
  portal admin surface) is already live — only the move-sheet action itself is missing.
- **Calendar period-split for Hours Log — deliberately not queued.** The row-cap-triggered
  single-standing-sheet model (§58 rider) is the permanent design; no follow-up work is expected here
  unless the row-cap watchdog itself proves insufficient in practice.
- **`field_ops.fieldops_sync.hours_enabled` is OFF by default** — the mirror pass ships dark;
  activation is an explicit operator step per the punch-list added to `safety_portal/README.md`.
- Worktree cleanup for the `compile_now_poll` generalization, P7 Slice 1, and README-rewrite branches.

## What was NOT touched

- **No external send path changed.** Invariant 1 intact — `progress_weekly_generate` /
  `compile_now_poll` are generation-side only; progress sends still route through the unchanged,
  human-approved `weekly_send` path once #460's mailbox lands.
- **`review_queue.VALID_WORKSTREAMS` itself was not restructured** — only `_WORKSTREAM_VALUES_GLOBAL`
  was brought into parity with it; the fix closes a registry gap, not a design change.
- **Calendar period-split was not built** — see Decisions #5 and Open items above; the row-cap-split
  design is the accepted permanent answer, not a placeholder.
- **`its#462` (archive-on-closure) was scoped and filed but not built this session.**
- **No rework of the P7 Slice 1 design was performed** — Path B ratified the design as originally
  built; the code shipped unchanged from before the §51 BLOCK, only the doctrine text around it moved.
- **No other Track 2 P7 slices were started** — this session covered Slice 1 (Hours Log) only.

## Lessons captured to memory

- **`project_fieldops-portal-program.md`** — updated with the full Track 0 go-live arc, the P7 Slice 1
  build, the §51 BLOCK and Path B resolution, the CI-billing incident, and the README/#324 recovery.
- **`reference_picklist-registry-must-include-all-daemon-values.md`** — extended with this session's
  instance: `review_queue.VALID_WORKSTREAMS` had `progress_reports` while
  `picklist_validation._WORKSTREAM_VALUES_GLOBAL` did not, and the REGISTRY structure note was
  corrected to `dict[int, dict[str, frozenset]]`. Confirms the lesson generalizes to `add_rows`, not
  just `update_rows`.
- **A design default picked while the operator is away can violate live doctrine — adversarial review
  is what caught it, not the build itself.** The single-standing-sheet default was chosen under a
  60-second `AskUserQuestion` timeout with no operator present; `ops-stds-enforcer`'s pre-merge review
  found the §51 conflict before it reached main. This is the "adversarial review is definition-of-done
  on any trust-boundary surface" house reflex extending naturally to doctrine-conflict surfaces, not
  just security surfaces.
- **A CI-wide outage with a payments/billing annotation is an account issue, not a code regression** —
  worth a one-line addition to house reflexes if it recurs, not yet added given this is a first
  occurrence.

## Cross-references

- Prior session (Progress-Reporting P4/P5 compile+send): `docs/session_logs/2026-06-30_p4-progress-compile-and-workflow-selector.md`,
  `docs/session_logs/2026-06-30_p5-progress-send-and-operability-guards.md`
- Prior session (2026-06-28 forensic scaling eval, the #1 sheet-proliferation finding that shaped
  Path B): `docs/session_logs/2026-06-28_forensic-scaling-eval-20x20.md`
- Prior session (Sentry reclassification v19.x rider precedent this session's rider follows):
  `docs/session_logs/2026-07-03_complete-state-growth-audit-design-table.md`
- Op Stds v19 §14 (parameterize-not-clone — `compile_now_poll` generalization), §30 (integration
  discipline — the archive-on-closure move-sheet method, `its#462`), §44 (both-rule — doctrine held for
  Seth's ratification rather than merged past), §50/§51 (SoR write-back / accumulating-log discipline —
  this session's rider), §46 (workspace-share approver-set check, resolved this session)
- Doctrine: `~/its-blueprint/doctrine/operational-standards.md` §51 (as amended by the v19.x rider,
  blueprint PR #58)
- PR merge discipline: `docs/operations/pr_merge_discipline.md`; verifier agent:
  `.claude/agents/pr-landed-verifier.md`
- Tech-debt: `docs/tech_debt.md` — no new entries this session beyond the two filed issues (#460, #462)
- Memory: `project_fieldops-portal-program.md` (Track 0 + P7 S1 sections appended),
  `reference_picklist-registry-must-include-all-daemon-values.md` (extended),
  `project_scaling-eval-20x20.md` (the #1 finding cited as Path B's rationale, unchanged)
