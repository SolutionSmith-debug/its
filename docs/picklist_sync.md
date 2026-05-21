# Picklist Sync — Operations Runbook

Cross-sheet PICKLIST option sync from master DBs (Vendor / Subcontractor / Equipment Master) to downstream sheets. Closes the gap that Smartsheet has no native cross-sheet picklist sync.

## What it does

Every 15 minutes (launchd cadence), reads `Picklist_Sync_Config` for enabled mappings, then for each mapping:

1. Reads unique values from the **source** sheet's column.
2. Compares against the **target** sheet's PICKLIST column options.
3. **Adds** any source values missing from the target picklist.
4. **Removes** target picklist options that no longer appear in the source — **but only when no live target cell still uses them** (reference-checked removal).
5. Updates `last_run_at` + `last_run_hash` on the mapping row.

Reference-blocked removals route to `ITS_Review_Queue` (`Reason=mismatched-reference`, `Severity=WARN`) so the operator can decide cleanup vs source-data update.

## Files

| Path | Role |
|---|---|
| `shared/picklist_sync.py` | Core sync library — pure-function core + driver |
| `scripts/run_picklist_sync.py` | CLI entry — `--dry`, `--mapping`, `--smoke-test` |
| `scripts/migrations/create_picklist_sync_config.py` | One-shot provisioning (sheet + ITS_Config rows) |
| `scripts/launchd/org.solutionsmith.its.picklist-sync.plist` | launchd template (operator installs at deployment) |
| `tests/test_picklist_sync.py` | Unit + integration tests (all mocked) |

## Config schema

`Picklist_Sync_Config` (sheet ID `7486553185013636`, in ITS — System / 01 — Config).

| Column | Type | Purpose |
|---|---|---|
| `mapping_id` | TEXT_NUMBER (primary) | Stable handle, e.g. `vendor_to_materials_vendor` |
| `source_sheet_id` | TEXT_NUMBER | Master DB sheet ID (string-encoded int) |
| `source_column` | TEXT_NUMBER | Column title on master DB |
| `target_sheet_id` | TEXT_NUMBER | Downstream sheet ID |
| `target_column` | TEXT_NUMBER | Downstream PICKLIST column title |
| `enabled` | CHECKBOX | Toggle without deleting the row |
| `last_run_at` | **TEXT_NUMBER** holding ISO 8601 UTC | Debuggable per-run timestamp. Note: NOT DATE — 15-min cadence needs time-of-day resolution. Column description on the sheet documents this; do not "fix" to DATE. |
| `last_run_hash` | TEXT_NUMBER | SHA-256 of sorted unique source values; idempotency short-circuit. |
| `notes` | TEXT_NUMBER | Free-text |

### Adding a new mapping

1. Identify the source master DB sheet + column title.
2. Identify the downstream PICKLIST column.
3. Insert a row into `Picklist_Sync_Config` with `enabled=true`. Leave `last_run_at` / `last_run_hash` / `notes` blank.
4. Watch `ITS_Errors` for the next 15-min run's INFO summary line to confirm the mapping landed.

### Disabling a mapping

Uncheck `enabled` in the row. The next sync will skip it entirely. No row deletion required — preserves run history.

## ITS_Config tunables

| Setting | Default | Effect |
|---|---|---|
| `picklist_sync.size_warn_threshold` | 200 | WARN to ITS_Errors when proposed options exceed |
| `picklist_sync.size_hard_halt_threshold` | 400 | HARD-HALT-that-mapping when proposed options exceed; ERROR logged; mapping auto-resumes when source returns to ≤ threshold |

Both validated on read: positive ints, `warn < halt`, both ≤ 1000. All-or-nothing fallback to `shared/defaults.py` constants on any invalid configured value, plus single WARN to ITS_Errors naming the offending input.

Missing rows are silent (documented default state). Only invalid configured values trigger a WARN.

## Operational runbook

### Routine

- **Daily**: glance at `ITS_Errors` for `picklist_sync_run_summary` INFO rows. Confirm one row per 15-min cadence (96/day under normal operation).
- **Weekly**: check `ITS_Review_Queue` for `Reason=mismatched-reference` items. Each indicates a master-DB hygiene issue (an option was removed from the master but a live downstream cell still uses it).
- **Monthly**: review picklist sizes per mapping. Any approaching 200 options is a capacity-planning signal.

### Alert response

**ERROR row in ITS_Errors with Script=`shared.picklist_sync` or `scripts.run_picklist_sync`**
- One mapping failed. Read the row's Message for the mapping_id + exception repr.
- Common causes: target column was renamed (mapping points at a stale title); source sheet permissions revoked; PICKLIST column converted to TEXT_NUMBER manually.
- Fix: update the mapping row in `Picklist_Sync_Config` to match the new shape, then the next cron run picks it up.

**CRITICAL row + Resend email + Sentry event**
- ≥3 mappings failed in one run. Triggers the triple-fire path with correlation_id threading.
- This usually indicates a systemic issue (Smartsheet API outage, expired token, mass column rename).
- Triage: check the Resend email for the count, then ITS_Errors for the per-mapping detail.

**ITS_Review_Queue row with Reason=mismatched-reference**
- A removal was blocked because live cells still use the value being removed.
- Payload includes `mapping_id`, `option_text`, `in_use_count`, and the source/target sheet+column.
- Decision tree: either (a) clean up the live cells (then next sync removes the option), (b) re-add the value to the source master DB (then next sync re-adds it cleanly), or (c) accept the option staying.

### CLI usage

```bash
# Default — runs every enabled mapping
python3 scripts/run_picklist_sync.py

# Dry-run — compute diffs, log proposed changes, no API writes
python3 scripts/run_picklist_sync.py --dry

# Single mapping (overrides enabled filter)
python3 scripts/run_picklist_sync.py --mapping vendor_to_materials_vendor

# Smoke test — bootstrap sandbox + add/remove flow + teardown.
# Creates two temporary sheets in ITS — System / 01 — Config, exercises
# the full lifecycle, then deletes everything.
python3 scripts/run_picklist_sync.py --smoke-test
```

## Activation checklist

The code can ship before the form-and-clone cascade because `picklist_sync` no-ops with zero enabled mappings. To activate:

1. Form-and-clone cascade lands → downstream sheets have PICKLIST columns.
2. For each downstream PICKLIST column, decide: which master DB feeds it?
3. Insert a `Picklist_Sync_Config` row per mapping; set `enabled=true`.
4. Run `--smoke-test` once to confirm the harness is healthy.
5. Manually set the downstream column to "Restrict to dropdown values only" in the Smartsheet UI (this toggle is UI-only — not exposed via API, per `docs/tech_debt.md` Smartsheet UI-only constraints entry).
6. Install the launchd plist:
    ```bash
    cp scripts/launchd/org.solutionsmith.its.picklist-sync.plist ~/Library/LaunchAgents/
    launchctl load -w ~/Library/LaunchAgents/org.solutionsmith.its.picklist-sync.plist
    ```
7. Watch `ITS_Errors` for the first INFO run-summary row.

## Per-customer-repo invariant

Per Foundation Mission v7.1, this customer's sheet IDs are baked into `shared/sheet_ids.py`. When the blueprint forks for a new customer, the four IDs that change here:

- `SHEET_PICKLIST_SYNC_CONFIG` — new fork's Picklist_Sync_Config
- `SHEET_VENDOR_DB`, `SHEET_SUBCONTRACTOR_DB`, `SHEET_EQUIPMENT_MASTER` — new fork's master DBs

The migration script `scripts/migrations/create_picklist_sync_config.py` is re-runnable in the fork to provision the new customer's Picklist_Sync_Config sheet from scratch.

## Cross-references

- `shared/picklist_sync.py` — module docstring + per-function docstrings
- `shared/smartsheet_client.py` — `list_columns_with_options`, `update_column_options`, `find_sheet_by_name_in_folder`, `create_sheet_in_folder` (added PR #45)
- `shared/review_queue.py` — `ReviewReason.MISMATCHED_REFERENCE`
- `shared/defaults.py` — `PICKLIST_SIZE_WARN_THRESHOLD`, `PICKLIST_SIZE_HARD_HALT_THRESHOLD`, `PICKLIST_SIZE_THRESHOLD_MAX`
- `docs/tech_debt.md` — Smartsheet UI-only constraints (Restrict-to-dropdown, Forms, Conditional Formatting, Filter Views)
- Op Stds v9 §3 (push-vs-record), §22 (MCP-gap REST fallback), §27 (failure isolation)
