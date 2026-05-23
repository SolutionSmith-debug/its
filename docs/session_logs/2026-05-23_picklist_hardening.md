# 2026-05-23 — Picklist-hardening pre-Customer-1 (Phase 1.4 #2)

Second deliverable of the Phase 1.4 pre-Customer-1 security hardening cluster per V&R v7.2 + Op Stds v11 §35. Ships the code-side picklist registry + write-path validation + drift audit; the operator-side UI conversion work is checklist-driven in `docs/picklist_hardening_audit.md`.

Branch: `feat/picklist-hardening` off `main` at `4b239fc` (PR #72 close).

## Purpose

Two-layer enforcement of bounded-enum Smartsheet columns:

1. **Client-side** (this PR): `shared/picklist_validation.py` composes the allowed sets from existing StrEnums (`Severity`, `ReviewReason`, `QuarantineReason`, `ContactStatus`, etc.) and validates every `add_rows` / `update_rows` payload BEFORE the API call. Code-side renames propagate automatically.
2. **Server-side** (operator UI work, tracked in `docs/picklist_hardening_audit.md`): Smartsheet "Restrict to picklist values only" toggle ON the columns. Catches writes that bypass `shared.smartsheet_client` (manual edits, third-party integrations, legacy migration scripts).

## Pre-flight findings

- Baseline test count: **949** (matches brief estimate — PR #72 left it there).
- HEAD `main = 4b239fc` (PR #72 close); no PRs landed between brief and execution.
- StrEnum classes available for REGISTRY composition: `Severity`, `ReviewReason`, `SlaTier`, `ReviewStatus`, `QuarantineReason`, `ContactStatus`, `HeaderVerdict`, `SystemState`. All 8 visible from `grep -rn "^class.*StrEnum" shared/`.
- Per-project sheets (Daily Reports + Weekly Rollups) are discovered DYNAMICALLY by `safety_reports.week_folder.ensure_current_week_folder` — no `DAILY_REPORTS_SHEET_BY_PROJECT` / `WEEKLY_ROLLUP_SHEET_BY_PROJECT` constants exist in `shared/sheet_ids.py`. Brief anticipated this; registry's `_build_per_project_entries()` returns empty (opt-in semantics handle the absent case).
- `SHEET_TRUSTED_CONTACTS` is still the placeholder `0` from PR #72 (operator hasn't pasted the real ID post-build-migration). Registry skips it conditionally to avoid spurious violations against unrelated sheet IDs.

## Substance

### `shared/picklist_validation.py` (~210 lines)

Public surface:
- `PicklistViolationError` (subclass of `ValueError`) — carries `sheet_id`, `column`, `value`, `allowed` for diagnostics.
- `REGISTRY: dict[int, dict[str, frozenset[str]]]` — composed at module load from StrEnums + literal frozensets for non-enum picklists (Workstream variants, WPR Send Status, Quarantine Disposition, Trusted Contacts Role).
- `validate_cell(sheet_id, column, value)` — pass-through for unregistered (sheet, column), `None`, and `bool`; raises `PicklistViolationError` on string-domain mismatch.
- `validate_row(sheet_id, row)` — applies `validate_cell` to every cell skipping `_`-prefixed meta keys.
- Re-exports the StrEnum classes for caller introspection.

Two workstream variants registered separately:
- `_WORKSTREAM_VALUES_GLOBAL` — used by ITS_Errors + ITS_Review_Queue (catch-all is `global`).
- `_WORKSTREAM_VALUES_OTHER` — used by ITS_Quarantine (catch-all is `other`; verified live 2026-05-18 — picklist drift documented in `shared/quarantine.py::VALID_WORKSTREAMS`).

The `ITS_Quarantine.Reason` column doesn't yet exist on the live sheet (PR #72 graceful-degraded `QuarantineReason` into `Notes` as `[reason: <code>]`). REGISTRY entry omits `Reason` for now; audit doc tracks the operator-side column-add as a pending action.

### `shared/smartsheet_client.py` integration

`add_rows` and `update_rows` both gained:

```python
from . import picklist_validation
for row_dict in rows:
    picklist_validation.validate_row(sheet_id, row_dict)
```

Late-import inside the function (not module top-level) avoids the picklist_validation → kill_switch → smartsheet_client circular cycle. Python caches modules so per-call import cost is negligible after the first call.

### `scripts/audit_picklist_drift.py` (~170 lines)

Programmatic registry-vs-live comparison. Three drift categories:
1. Column type wrong (TEXT_NUMBER instead of PICKLIST/MULTI_PICKLIST).
2. Allowed-set mismatch (Smartsheet has values registry doesn't, or vice versa).
3. Column missing in live sheet.

CLI: `python -m scripts.audit_picklist_drift [--update-audit-doc] [--no-emit]`. Writes `~/its/.watchdog/safety_picklist_audit.last_run` marker; emits one ITS_Errors WARN per finding (or INFO when clean). `--update-audit-doc` is a placeholder — auto-update of audit doc emojis not yet implemented; flag prints TODO and exits cleanly alongside the regular report.

### `scripts/watchdog.py::TRACKED_JOBS`

Added `safety_picklist_audit` with `timedelta(days=8)` freshness window (matches `safety_weekly_generate` pattern — weekly cadence with one-cycle tolerance).

### `docs/picklist_hardening_audit.md`

Operator's UI conversion checklist. One table per sheet (ITS_Config, ITS_Errors, ITS_Review_Queue, ITS_Quarantine, ITS_Trusted_Contacts, WPR_Pending_Review, per-project sheets) listing every bounded-enum column with target type + values + status emoji (⬜ ✅ ⚠️ 🟦). Doc lives until Phase 1.5 cutover; `audit_picklist_drift.py` keeps the registry honest with the live state once operator converts.

Summary: ~21 operator UI actions pending (14 toggle-verifies + 1 ITS_Quarantine column-add + 6 per-project template conversions). After all rows show ✅, two-layer enforcement is live and the watchdog's drift WARN flips to ERROR.

### `shared/kill_switch.py` — Phase 3 was a no-op

The brief's Phase 3 proposed adding `_VALID_STATES = frozenset({"ACTIVE", "PAUSED", "MAINTENANCE"})` and a `_read_state()` helper that returns "PAUSED" on unknown values. Existing code already has:
- `SystemState` StrEnum (the registry equivalent).
- `check_system_state()` with try/except returning ACTIVE on every fail-open mode per Op Stds v8 §1 ("never silently halt the system").

The brief's suggested "return PAUSED on invalid" would have INVERTED the documented fail-open behavior. Preserved existing per "Verify CI diagnoses" + Op Stds v8 §1. No code change to kill_switch this session; existing `tests/test_kill_switch.py` (7 tests) already covers all 3 fail-open modes.

## Tests

- `tests/test_picklist_validation.py` — 20 tests (happy paths, pass-throughs for unregistered/None/bool, registry composition checks, error-message formatting, numeric-cast safety, module-surface introspection).
- `tests/test_smartsheet_client_picklist_integration.py` — 8 tests (add_rows/update_rows reject invalid values BEFORE API call; unregistered sheets pass-through; empty batches no-op).
- `tests/test_audit_picklist_drift.py` — 8 tests (happy path, TEXT_NUMBER finding, allowed-set mismatch finding, missing-column finding, unreadable-sheet finding, MULTI_PICKLIST acceptance, aggregation across registered sheets, empty-entry skip).
- `tests/test_kill_switch.py` — unchanged; existing 7 tests already cover Phase 3's intent.

Baseline 949 → final 1004 (+55). All pass.

## Verification gates

- `pytest -q` — 1004 collected, all pass.
- `mypy shared/picklist_validation.py shared/smartsheet_client.py scripts/audit_picklist_drift.py scripts/watchdog.py` — `Success: no issues found in 4 source files`.
- `ruff check` — clean across all touched files. One auto-fix (import sort in `test_picklist_validation.py`). One rename: `PicklistViolation` → `PicklistViolationError` per codebase convention (existing exception classes all use `*Error` suffix; ruff N818 enforces). Brief used `PicklistViolation`; rename is mechanical.
- `tests/test_capability_gating.py` — still passes (no new send capability introduced).
- Migration scripts import cleanly.

## Operator-side actions remaining

Per `docs/picklist_hardening_audit.md`:

1. Walk each ⬜ row in the audit doc. For each column:
   - Open Smartsheet column properties
   - Change type to PICKLIST (or CHECKBOX for booleans, or add column for missing ITS_Quarantine `Disposition` / `Reason`)
   - Enter target values from the audit doc
   - Toggle ON "Restrict to picklist values only"
   - Save
2. After each batch, run `python -m scripts.audit_picklist_drift --update-audit-doc` (placeholder today — prints TODO; manual emoji update in the doc until auto-update lands).
3. Subsumes PR #72 leftover step #2 — the three new `ITS_Review_Queue.Reason` values (`header-soft-fail-trusted`, `sender-pending-verification`, `project-out-of-scope`) are included in this audit.
4. Once all rows show ✅, optionally flip the watchdog's drift alarm threshold from WARN to ERROR (manual code edit) so future regressions surface as real alarms.

Optional follow-ons (no separate tech-debt entries needed — they're naturally driven by the audit):
- Adding the `ITS_Quarantine.Reason` column → register in REGISTRY (one-line edit).
- Adding the `ITS_Quarantine.Disposition` column → already in REGISTRY, just needs the column.
- Pasting the real `SHEET_TRUSTED_CONTACTS` ID into `shared/sheet_ids.py` after PR #72's build migration → registry auto-picks-up.

## Out-of-scope (per brief, restated)

- Trusted-contacts column hardening (already PICKLIST-shaped from PR #72).
- Attachment screening (Phase 1.4 #3 — separate session). The QuarantineReason additions for attachment-based disposition (`malware_detected`, `suspicious_attachment`, `disallowed_filetype`) come with that session.
- Multi-PICKLIST graduation for Project Scope / Workstream Scope columns in ITS_Trusted_Contacts (own tech-debt entry from PR #72).
- Auto-conversion via Smartsheet API (column type changes have limited API support; operator does these manually).
- Cross-sheet picklist sync changes (PR #45-51 daemon handles keep-in-sync separately).

## Notes / gotchas surfaced this session

- **Circular import risk**: `picklist_validation` imports `kill_switch` (for `SystemState`) which imports `smartsheet_client` which (would-have-been) imports `picklist_validation`. Resolved with late (function-level) import in `add_rows` / `update_rows`. Standard Python idiom; per-call cost is one cached-module lookup.
- **Workstream picklist drift**: ITS_Errors / ITS_Review_Queue use `global` as catch-all; ITS_Quarantine uses `other` (verified live 2026-05-18). Registry has both as separate frozensets to keep the live sheets accurate.
- **`PicklistViolation` → `PicklistViolationError` rename**: brief used the shorter name but codebase convention (and ruff N818) is `*Error` suffix. Mechanical rename across module + 3 test files + audit doc + smartsheet_client docstring.
- **Phase 3 no-op**: brief's suggested kill_switch change (return PAUSED on unknown state) inverted the documented Op Stds v8 §1 fail-open behavior. Preserved existing per "Verify CI diagnoses" memory.
- **Per-project sheets dynamic**: `_build_per_project_entries()` is a shell-returning-empty until `DAILY_REPORTS_SHEET_BY_PROJECT` / `WEEKLY_ROLLUP_SHEET_BY_PROJECT` constants land. Operator-side template conversion still works (per the audit doc's "Per-project sheets" section) — convention propagates forward when `ensure_current_week_folder` clones the template.
- **`--update-audit-doc` flag**: placeholder; prints TODO + exits 0. The table-rewrite heuristic is non-trivial (parsing markdown + diffing emojis without clobbering manual operator notes); deferred to a follow-on if/when the operator finds the manual-update workflow too friction-heavy.
- **Pre-existing `test_weekly_send_poll.py` CI failures** noted from PR #72 session log: still present on `main`. Not addressed this PR. Whoever owns weekly_send_poll fixtures next should fix the Linux-no-Keychain pattern there.
