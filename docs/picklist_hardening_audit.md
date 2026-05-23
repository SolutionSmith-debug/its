# Picklist-Hardening Audit (Op Stds v11 §35)

Source of truth for the operator's UI conversion checklist. Every bounded-enum
Smartsheet column on the five hardening targets (ITS_Config, ITS_Errors,
ITS_Review_Queue, ITS_Quarantine, ITS_Trusted_Contacts, WPR_Pending_Review,
per-project Daily Reports + Weekly Rollups) should be PICKLIST (or CHECKBOX
for booleans) with "Restrict to picklist values only" toggled ON.

Two-layer enforcement:

1. **Client-side** (this repo): `shared/picklist_validation.py::REGISTRY`
   composes the allowed sets from the source-of-truth StrEnum classes
   (`Severity`, `ReviewReason`, `QuarantineReason`, `ContactStatus`, etc.).
   `shared/smartsheet_client.py::add_rows` and `update_rows` call
   `validate_row` before any payload construction; a disallowed value raises
   `PicklistViolationError` BEFORE the API call. Adding a new column to the
   hardening is a code edit to `REGISTRY`.

2. **Server-side** (operator UI work, tracked here): Smartsheet column type
   change from `TEXT_NUMBER` → `PICKLIST` plus "Restrict to picklist values
   only" toggle ON. Catches writes from outside the codebase (manual edits,
   third-party integrations, legacy migration scripts that bypass
   `shared.smartsheet_client`).

Run `python -m scripts.audit_picklist_drift [--update-audit-doc]` after each
operator UI conversion pass to refresh the status emojis automatically.

## Conversion status legend

- ⬜ Pending operator UI conversion (column is still TEXT_NUMBER or PICKLIST
  without the "Restrict to picklist values only" toggle)
- ✅ Converted (column is PICKLIST/CHECKBOX with strict enforcement ON, and
  the server-side allowed set matches `REGISTRY`)
- ⚠️ Drift detected (`REGISTRY` and the live Smartsheet allowed set disagree
  — either remove the stray value from Smartsheet or add it to the code-side
  enum + the registry)
- 🟦 N/A (free-form column, intentionally not bounded-enum)

## ITS_Config (sheet_id 3072320166907780)

ITS_Config rows use a tall key/value layout (Setting / Workstream / Value /
Description). The Value column is intentionally free-form because its
semantics depend on Setting — `system.state` is enum-domain, but
`safety_reports.intake.confidence_threshold` is a float, etc. Per-key
validation is handled at the call site:

- `system.state` → `shared/kill_switch.py::check_system_state` validates via
  the `SystemState` StrEnum (try/except with fail-open WARN→ACTIVE per
  Op Stds v8 §1).
- Other enum-domain rows: validation TBD per-consumer.

| Column | Current Type | Target Type | Target Values | Status |
|---|---|---|---|---|
| Setting | TEXT_NUMBER | TEXT_NUMBER | (free-form key) | 🟦 |
| Workstream | PICKLIST | PICKLIST | safety_reports / po_materials / subcontracts / email_triage / ai_employee / system | ⬜ (verify "Restrict to" toggle ON) |
| Value | TEXT_NUMBER | TEXT_NUMBER | (per-key; see above) | 🟦 |
| Description | TEXT_NUMBER | TEXT_NUMBER | (free-form) | 🟦 |

## ITS_Errors (sheet_id 27291433258884)

Registered in `picklist_validation.REGISTRY[SHEET_ERRORS]`:
- `Severity` — INFO / WARN / ERROR / CRITICAL (from `shared.error_log.Severity`)
- `Workstream` — safety_reports / po_materials / subcontracts / email_triage / ai_employee / global

| Column | Current Type | Target Type | Target Values | Status |
|---|---|---|---|---|
| Severity | TEXT_NUMBER | PICKLIST | INFO / WARN / ERROR / CRITICAL | ⬜ |
| Workstream | TEXT_NUMBER | PICKLIST | safety_reports / po_materials / subcontracts / email_triage / ai_employee / global | ⬜ |

## ITS_Review_Queue (sheet_id 7243317526876036)

Registered in `picklist_validation.REGISTRY[SHEET_REVIEW_QUEUE]`:
- `Reason` — 12 values per `shared.review_queue.ReviewReason` (PR #72 added
  3 new values: header-soft-fail-trusted / sender-pending-verification /
  project-out-of-scope — these are subsumed into this audit and replace
  PR #72's leftover operator-side step #2)
- `SLA Tier` — 4h / 24h / 48h
- `Workstream` — same global set as ITS_Errors
- `Status` — PENDING / IN_REVIEW / APPROVED / REJECTED / ESCALATED
- `Severity` — INFO / WARN / ERROR / CRITICAL

| Column | Current Type | Target Type | Target Values | Status |
|---|---|---|---|---|
| Reason | PICKLIST | PICKLIST | low-confidence-extraction / ambiguous-classification / structured-output-edge / zero-data-window / mismatched-reference / security-trigger / policy-edge / manual / other / header-soft-fail-trusted / sender-pending-verification / project-out-of-scope | ⬜ (3 new values from PR #72 not yet in live picklist; existing 9 already PICKLIST per 2026-05-18 schema verify) |
| SLA Tier | PICKLIST | PICKLIST | 4h / 24h / 48h | ⬜ (verify "Restrict to" toggle ON) |
| Workstream | PICKLIST | PICKLIST | (same global set as ITS_Errors) | ⬜ (verify toggle) |
| Status | PICKLIST | PICKLIST | PENDING / IN_REVIEW / APPROVED / REJECTED / ESCALATED | ⬜ (verify toggle) |
| Severity | PICKLIST | PICKLIST | INFO / WARN / ERROR / CRITICAL | ⬜ (verify toggle) |
| Security Flag | CHECKBOX | CHECKBOX | true / false | ✅ |

## ITS_Quarantine (sheet_id 8687740798324612)

Registered in `picklist_validation.REGISTRY[SHEET_QUARANTINE]`:
- `Workstream` — superset of ITS_Errors with `other` replacing `global`
  (verified live 2026-05-18 — picklist drift documented in
  `shared/quarantine.py::VALID_WORKSTREAMS`).
- `Disposition` — RELEASE / DELETE / ESCALATE (operator review action;
  not yet a column in the live sheet — added to REGISTRY pre-emptively
  so writes from `shared/quarantine.py` are validated when the disposition
  write path lands)

The `Reason` column does NOT exist on the live sheet — PR #72 graceful-
degraded `QuarantineReason` into the `Notes` cell as `[reason: <code>]`.
When the operator adds a dedicated `Reason` column, register it in
REGISTRY (one-line edit) and add a row to this table.

| Column | Current Type | Target Type | Target Values | Status |
|---|---|---|---|---|
| Workstream | PICKLIST | PICKLIST | safety_reports / po_materials / subcontracts / email_triage / ai_employee / other | ⬜ (verify toggle) |
| Disposition | (column not present) | PICKLIST | RELEASE / DELETE / ESCALATE | ⬜ (operator add column) |
| Reason | (column not present; degraded into Notes) | PICKLIST | unknown_sender / sender_disabled / workstream_out_of_scope / header_forgery_suspected / legacy_allowlist_miss | ⬜ (operator add column; backfill Notes-encoded values into the new column) |

## ITS_Trusted_Contacts (sheet_id placeholder 0 — operator pastes real ID after PR #72 migration)

PR #72 built the sheet with PICKLIST columns for `Status` and `Role`
directly (`build_its_trusted_contacts_sheet.py`). Both are already
PICKLIST-typed at creation; the only operator action is verifying the
"Restrict to picklist values only" toggle is ON.

Registry registration is conditional on `SHEET_TRUSTED_CONTACTS != 0` —
the placeholder is skipped to avoid spurious violations against unrelated
sheet IDs. Once operator pastes the real ID, the registry picks it up.

| Column | Current Type | Target Type | Target Values | Status |
|---|---|---|---|---|
| Email | TEXT_NUMBER (primary) | TEXT_NUMBER (primary) | (free-form key) | 🟦 |
| Display Name | TEXT_NUMBER | TEXT_NUMBER | (free-form) | 🟦 |
| Role | PICKLIST | PICKLIST | Field PM / Safety Officer / Subcontractor PM / Site Supervisor / Operator / Other | ⬜ (verify toggle) |
| Project Scope | TEXT_NUMBER | TEXT_NUMBER | (JSON list — see multi-PICKLIST graduation tech-debt entry) | 🟦 |
| Workstream Scope | TEXT_NUMBER | TEXT_NUMBER | (JSON list — same) | 🟦 |
| Status | PICKLIST | PICKLIST | ACTIVE / DISABLED / PENDING_VERIFICATION | ⬜ (verify toggle) |
| Added By / Added Date / Last Verified / Notes | TEXT_NUMBER / DATE / DATE / TEXT_NUMBER | (no change) | (free-form / DATE-typed) | 🟦 |

## WPR_Pending_Review (sheet_id 3096105695793028)

Registered in `picklist_validation.REGISTRY[SHEET_WPR_PENDING_REVIEW]`:
- `Send Status` — PENDING / SENT / FAILED / HELD (per PR #68 schema-drift
  finding — brief said `SEND_FAILED` but live picklist enforces `FAILED`)

| Column | Current Type | Target Type | Target Values | Status |
|---|---|---|---|---|
| Send Status | PICKLIST | PICKLIST | PENDING / SENT / FAILED / HELD | ⬜ (verify toggle) |
| Approved for Send | CHECKBOX | CHECKBOX | true / false | ✅ |
| Approved By | CONTACT_LIST | CONTACT_LIST | (contact picker; not picklist-validated) | 🟦 |

## Per-project sheets (Daily Reports + Weekly Rollups)

Per-project sheet IDs are not yet pre-wired in `shared/sheet_ids.py` —
`safety_reports.week_folder.ensure_current_week_folder` discovers them
dynamically per week from the project's Field Reports folder. Until
`DAILY_REPORTS_SHEET_BY_PROJECT` / `WEEKLY_ROLLUP_SHEET_BY_PROJECT`
constants land, the registry's `_build_per_project_entries()` returns an
empty dict — un-registered sheet IDs pass-through validation.

Operator-side conversion still applies to the per-week template sheets
that get cloned forward by `ensure_current_week_folder`:

- Daily Reports template (per project; canonical = Bradley 1 "Week of
  2026-03-09" daily reports sheet)
  - `Report Category` PICKLIST: Daily JHA / Tool Box Talk / Equipment
    Check Sheets / Safe Work Observation / Other → ⬜ verify toggle
- Weekly Rollup template (per project; same canonical)
  - No bounded-enum columns currently — placeholder for future hardening
    if categorical fields land

| Project | Daily Reports template | Weekly Rollup template | Status |
|---|---|---|---|
| Bradley 1 | (dynamic) | (dynamic) | ⬜ |
| Bradley 2 | (dynamic) | (dynamic) | ⬜ |
| Brimfield 1 | (dynamic) | (dynamic) | ⬜ |
| Brimfield 2 | (dynamic) | (dynamic) | ⬜ |
| Huntley | (dynamic) | (dynamic) | ⬜ |
| Rockford | (dynamic) | (dynamic) | ⬜ |

After operator converts each project's TEMPLATE sheet's `Report Category`
column to strict PICKLIST, the convention propagates forward (each new
week's clone inherits the column type). Past-week sheets that were cloned
before conversion stay TEXT_NUMBER-or-non-strict-PICKLIST — operator
judgment whether to retroactively convert them or accept that historical
weeks may have free-form category values.

## Summary

Pending conversions: 14 toggle-verification passes (rows marked ⬜) +
1 column-add (ITS_Quarantine `Disposition` + `Reason`) + 6 per-project
template conversions = ~21 operator actions, all UI work.

Once all rows show ✅:
- Two-layer enforcement is live.
- The `scripts/audit_picklist_drift.py` weekly watchdog flips its WARN
  threshold to ERROR — any future drift surfaces as a real alarm
  rather than a soft warning.
- Picklist-hardening (Phase 1.4 deliverable #2) is complete.
