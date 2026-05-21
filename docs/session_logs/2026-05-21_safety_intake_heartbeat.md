# 2026-05-21 — Safety intake heartbeat row writes to ITS_Daemon_Health

PR: [#60](https://github.com/SolutionSmith-debug/its/pull/60) — squash-merged at 2026-05-21T19:16:26Z. Merge commit `7397b077f31bc8f06319d2736c4a15fa16317f37`. Three-assertion verify clean (state=MERGED, mergedAt non-null, mergeCommit.oid present).

Logical predecessor: PR #59 (polling-daemon trigger, merged earlier the same day). Brief originally numbered this work "PR #59.5"; GitHub assigned the next sequential number (#60). The shared/runner.py extraction the previous brief flagged as "PR #60 territory" remains future work — defer until the second polling consumer ships (preservation-over-refactor).

Wires `safety_reports.intake_poll` to write a heartbeat row to `ITS_Daemon_Health` on every poll cycle. Operator gains a single canonical Smartsheet-row view of daemon liveness, last-cycle status, items processed, lifetime cycle count, and last-error correlation. No Smartsheet schema changes — the live schema (sheet `4529351700729732`) is already correct as-is; only Python writer + state-file semantics shipped.

## What shipped

- **`shared/sheet_ids.py`** — `FOLDER_SYSTEM_DAEMONS`, `SHEET_DAEMON_HEALTH`, and `DAEMON_HEALTH_COLUMNS` dict (12 column IDs from the schema doc). Column IDs are the authoritative reference so writes survive column-title renames.
- **`shared/smartsheet_client.py`** — two new helpers:
  - `find_row_by_primary(sheet_id, primary_column_id, value)` returns the first row whose primary column equals `value`. Returns title-keyed `{_row_id, <title>: value, ...}` dict or `None` on miss. Iterates the full sheet client-side; only safe on bounded sheets (ITS_Daemon_Health is one row per daemon).
  - `update_row_cells_by_id(sheet_id, row_id, cells_by_column_id)` — direct ID-based row updates (no title-cache lookup). Raises `SmartsheetNotFoundError` on 404 so callers can invalidate their row-id cache.
- **`safety_reports/intake_poll.py`** —
  - State-file helpers (`_load`, `_persist`, `_invalidate`, `_resolve_heartbeat_row_id`) backing a JSON cache at `~/its/state/heartbeat_row_ids.json` shaped `{daemon_name: {row_id, total_cycles}}`.
  - `_write_heartbeat_row(status, items_processed, error_summary, correlation_id, notes)` — inlined per preservation-over-refactor (NOT extracted to `shared/heartbeat.py` — that lives at the second consumer's PR).
  - `poll_once` calls `_write_heartbeat_row` after the for-loop in a belt-and-suspenders outer catch-all; the function itself already swallows `SmartsheetError` and `Exception` and logs `daemon_health_write_failed`.
- **Tests** — `test_smartsheet_client.py` +6 unit tests, `test_smartsheet_client_integration.py` +2 SDK-vs-Live tests (per Op Stds v10 §30), `test_intake_poll.py` +21 tests + 1 gated integration test against live row 7461022174478212.
- **`safety_reports/README.md`** — operator visibility section explaining how to read the row, the canonical-gate-vs-filter-flag distinction (ARCH-1), and the cache file semantics.
- **`.gitignore`** — `logs/**/*.log` to exclude the launchd-created log directory from PR #59.

## Three architectural refinements (override schema doc §4 / §8)

- **ARCH-1 — `Enabled` is report-filter metadata, NOT a runtime gate.** The runtime on/off decision stays in `safety_reports.intake.polling_enabled` (ITS_Config). Documented in `_write_heartbeat_row`'s docstring + the README. Avoids the four-state nonsense matrix (Enabled=true + polling_enabled=false → false STALE alerts; Enabled=false + polling_enabled=true → errors invisible).
- **ARCH-2 — Row-id lookup via persistent JSON state file.** launchd-poll-once = fresh process per cycle; in-memory cache would not survive. State file eliminates one `find_row_by_primary` round trip per cycle and survives process restarts. Auto-invalidates on 404 (row deleted/re-seeded).
- **ARCH-3 — `Total Cycles Today` becomes lifetime monotonic.** Persisted alongside row_id in the same state file. Read + increment + write per cycle, never reading the column back from Smartsheet. Smartsheet column title unchanged in this PR; semantics-only change documented in code + README. UI-only column rename is a separate cleanup.

## Test count delta

| Metric | Pre-PR (PR #59 close) | This PR |
|---|---|---|
| pytest pass | 754 | 781 (+27) |
| pytest skip | 1 | 1 |
| pytest deselected | 7 | 10 (+3 — 3 new gated integration tests) |
| mypy source files | 93 | 93 (unchanged — new code lives in existing modules) |
| mypy issues | 0 | 0 |
| ruff | clean | clean |

## Live verification

The daemon installed by PR #59 picked up the heartbeat code naturally during its 60s cycles (launchd spawns a fresh process per cycle and imports from the working tree — no separate install for code changes). At PR-commit time, the state file showed:

```json
{
  "safety_reports.intake_poll": {
    "row_id": 7461022174478212,
    "total_cycles": 56
  }
}
```

Last heartbeat file: `2026-05-21T19:13:30.133596+00:00`. Live row 7461022174478212 updated through 56 lifetime cycles, no `daemon_health_write_failed` entries in ITS_Errors. ARCH-2 cache + ARCH-3 lifetime counter both confirmed working end-to-end before merge.

## Subtleties found mid-implementation

- **Daemon running my unmerged code during the build.** The launchd job (loaded post-PR-#59) ran each cycle out of the working tree. While I was actively editing `intake_poll.py`, the daemon was importing whichever version was on disk at cycle-spawn time. Could have been awkward if an intermediate save state was broken; in practice the heartbeat function's internal catch-all (logs `daemon_health_write_failed`, never raises) covered the failure mode. Worth remembering: launchd-poll-once architecture means the working tree IS the deployment.
- **`.gitignore` missed `logs/launchd/`.** Existing rule was `logs/*.log` which only matches top-level. PR #59 introduced `logs/launchd/safety_intake_poll.{err,out}.log` (nested). Added `logs/**/*.log` in this PR rather than a follow-on cleanup since the launchd log directory is the direct consequence of the daemon shipping.
- **Smartsheet integer vs. float read-back.** In the live integration test, `Total Cycles Today` may come back as a float from Smartsheet (numbers stored without a decimal but returned as `float`). The integration test normalizes via `int(...)` before asserting the increment.
- **`_resolve_heartbeat_row_id` persistence convention.** On first lookup the function persists `total_cycles=0` (since this IS the first cycle the daemon has seen this row). The actual increment to 1 happens in `_write_heartbeat_row` immediately after, so the first row write lands with `Total Cycles = 1`. Verified live.

## Operator-side actions remaining

- **None for this PR.** The heartbeat row was already live-updating from the working tree before merge. After merge: the daemon continues to run the same code paths against the now-canonical merged-main version. No re-install needed; no migration script needed; no manual touch needed.
- **Future**: Watchdog Check F retrofit to read the heartbeat row's freshness instead of mailbox-idle. Separate PR.

## What's NOT touched

- `shared/runner.py` / `shared/heartbeat.py` abstractions — defer to the second polling consumer.
- Watchdog Check F repurpose — separate PR after this lands.
- `shared/picklist_sync` heartbeat retrofit — separate PR.
- ITS_Errors duplicate-sheet cleanup (the 5-sheet bootstrap drift) — separate PR.
- ITS_Daemon_Health schema modifications (column title rename, etc.) — out of scope; live schema correct.
- `weekly_generate.py` / `weekly_send.py` — R3 sessions 2 + 3.

## Baseline state at session close

- Main at `7397b07` (PR #60 merge commit).
- pytest 781 / 1 / 10. mypy 0/93. ruff clean.
- Daemon: running, healthy, writing to ITS_Daemon_Health row 7461022174478212 every 60 seconds, lifetime counter past 56 and climbing.
- Two heartbeat layers operational: local file at `~/its/state/safety_intake_heartbeat.txt` (cheap, always works) + sheet row (operator-visible).
- R3 session 2 prerequisites: zero remaining. `weekly_generate.py` (the next Anthropic generation script + WPR pipeline) is the immediate-next critical path.
