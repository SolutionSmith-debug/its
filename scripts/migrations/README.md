# ITS one-shot migration scripts

This directory holds operator-invoked migration scripts — each runs once during
a PR's creation to land the live-data side of a code change (Smartsheet rows,
Box folders, etc.). All scripts here MUST be idempotent: re-running with the
target state already in place makes zero writes and exits 0.

## Scripts

### `box_clone_1111a_to_projects.py`

Clones the `1111A (Copy for new projects)` Box template into the 6 Forefront
project folders (Bradley 1/2, Brimfield 1/2, Huntley, Rockford) under `ITS DATA`.
Closes the last Box-side prerequisite for R3 session 1 (intake.py wiring) by
materializing the Box-side targets referenced from `shared.defaults.BOX_PROJECT_FOLDERS`.
Ran 2026-05-21.

The Box source-folder lock gotcha motivated the script's retry pattern: Box's
async deep-copy holds a server-side lock on the source folder for the duration
of the operation; subsequent copies (UI or API) from the same source fail with
HTTP 500 + a "locked" message until the lock clears. Lock duration is variable
(observed ~30s to several minutes for the 269-file / 14-subfolder template).
`copy_with_lock_retry` waits 30s between attempts up to 40 attempts (20-minute
total budget per copy); hammering the queue does not speed it up. The 6
resulting folder IDs are committed in `shared.defaults.BOX_PROJECT_FOLDERS`.

Idempotent: a re-run with all 6 folders present makes zero copy calls, prints
6 `EXISTS` lines, exits 0.

### `seed_safety_intake_config.py`

Seeds 5 `safety_reports.intake.*` rows in `ITS_Config` (workstream `safety_reports`)
that `safety_reports/intake.py` reads at runtime: `allowed_senders` (JSON list),
`classification_model` (Anthropic model ID), `box_filing_enabled` (capability flag),
`review_queue_on_low_confidence` (behavior flag), and `confidence_threshold` (float).
Ran 2026-05-21. Idempotent per row.
