# 2026-05-21 — Box 1111A clone cascade

PR: [#56](https://github.com/SolutionSmith-debug/its/pull/56) — squash-merged at 2026-05-21T16:22:03Z. Merge commit `30bbaa58e7c692b1c8976bea614f497007767d60`. Three-assertion verify clean.

Closes the last Box-side prerequisite for R3 session 1 (intake.py wiring). Materializes the 6 Forefront project folders as deep-copy clones of the `1111A (Copy for new projects)` template under `ITS DATA`, then fills `BOX_PROJECT_FOLDERS` in `shared/defaults.py` (which PR #54 had landed as empty-string skeleton with explicit loud-fail-on-first-use intent).

## What landed

- `scripts/migrations/box_clone_1111a_to_projects.py` (new) — idempotent migration with lookup-by-name + lock-aware copy retry + deep-copy completeness poll. 40 attempts × 30s lock budget per copy (20-minute ceiling); 10-minute deep-copy timeout per destination.
- `shared/defaults.py` — `BOX_PROJECT_FOLDERS` 6 empty-string values replaced with real Box folder IDs. Docstring updated to cite the cascade origin + run date; per-customer-repo invariant note preserved.
- `scripts/migrations/README.md` (new) — first entry in this directory's README. One paragraph on the script covering what it does, when it ran, idempotency, and the source-folder-lock gotcha.
- `tests/test_box_clone_migration.py` (new) — one sanity test pinning set-equality between the script's `PROJECTS` constant and `shared.sheet_ids.PROJECT_NAME_BY_FOLDER_ID.values()`. Catches future drift where a 7th project is added Smartsheet-side without a matching Box-side cascade entry.

## The 6 IDs as audit trail

| Project | Status | Box folder ID | Subfolders |
|---|---|---|---|
| Bradley 1 | exists | `383299029178` | 14/14 |
| Bradley 2 | exists | `383298229322` | 14/14 |
| Brimfield 1 | exists | `383303174342` | 14/14 |
| Brimfield 2 | created | `383303695163` | 14/14 |
| Huntley | created | `383302259414` | 14/14 |
| Rockford | created | `383305112425` | 14/14 |

## Partial-state recovery (3 + 3)

Pre-PR state: Bradley 1, Bradley 2, Brimfield 1 had already been cloned earlier today via mixed MCP+UI work (the brief surfaced their IDs verbatim). The script treats those as immutable starting state: lookup-by-name returns the existing folder ID, the deep-copy completeness check confirms 14/14 subfolders (already settled from the earlier copies), and the script logs `EXISTS:` for each. Only Brimfield 2, Huntley, and Rockford needed new clones.

This is exactly the idempotency contract the brief specified, and it's the load-bearing property for re-runs: any future operator can re-invoke the script and either confirm steady state or pick up where a partial run left off, without manual triage.

## The lock gotcha — and why we didn't hit it

Box's async deep-copy holds a server-side lock on the source folder for the duration of the operation. Subsequent copies (UI or API) from the same source fail with HTTP 500 + a "locked" message until the lock clears. Lock duration is variable — observed ~30s to several minutes for the 269-file / 14-subfolder template.

`copy_with_lock_retry` handles this: distinguishes a lock 500 (retryable, by checking for "lock" in the error message) from a generic 500 (bail), waits 30s between attempts, 40-attempt budget = 20-minute ceiling per copy. Errors outside that pattern (4xx name conflicts, perm denials) propagate immediately.

**Lock retries did NOT fire during this run.** Box's queue cleared between consecutive copies fast enough that each new copy succeeded on first attempt; the deep-copy poll (10-second interval, 10-minute timeout per destination) was the only meaningful wait per project. The retry machinery stays in the script because the lock IS real and IS server-side — the parallel chat session that cloned the first three earlier today reportedly hit it — but this particular cc-driven run got the happy-path branch through all three new copies.

## Total runtime

Script invoked 2026-05-21 12:07 EDT, completed at ~16:22 UTC (the merge commit timestamp). The bulk of the time was Brimfield 2's deep-copy poll — Box's async queue took several minutes to populate the 14 subfolders, and the script's `wait_for_deep_copy_complete` is silent during the 10-second-interval poll loop. The two subsequent copies (Huntley, Rockford) completed faster as the queue stayed warm.

Brief's runtime estimate was 10-25 minutes; actual was within that range.

## Subtle decision: deep-copy poll output is silent during the wait

`wait_for_deep_copy_complete` prints start ("Polling deep-copy ...") and end (the CREATED/EXISTS line), nothing in between. The 10-second-interval poll runs without progress updates. Considered adding per-poll `[{n}/{expected}]` lines but rejected: the operator running this with `tee` already sees the destination start-time and the eventual completion, and adding lines per 10-second tick would clutter the log without informing decisions. The lock-retry path DOES print per-attempt (with the wait countdown), because there the operator might genuinely want to know "is the lock still held or is the script frozen."

## Baseline delta

- pytest: 683 → 684 pass / 2 skip / 6 deselected.
- mypy: 0 issues / 85 → 87 source files (+2: new script + new test).
- ruff: clean.

## What's next

R3 session 1 (intake.py wiring) is unblocked. The remaining ITS_Config-side prerequisite — the `safety_reports.recipients.*` per-job entries — was seeded as empty `[]` lists in PR #54 (`scripts/migrations/seed_safety_recipients_config.py`); the operator fills those via sheet edit after a Teala email, separately from any cc-driven PR.
