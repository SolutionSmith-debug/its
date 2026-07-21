"""Build the five "ITS — System" sheets — ITS_Config, ITS_Errors, ITS_Quarantine,
ITS_Review_Queue, ITS_Daemon_Health.

D3 of the Phase-1 production-cutover gap-builder set. All five sheets predate the
builder family — they were hand-created during the 2026-05-17 sandbox restructure
and have never had a migration script, so a fresh tenant (the dedicated ITS identity
on Evergreen's PRODUCTION Smartsheet plan) has no reproducible way to stand them up.
This script closes that gap. The column schemas below were DERIVED from a read of the
live sandbox sheets cross-checked against every code writer, but they are declared
here EXPLICITLY and self-contained: a Customer-2+ fork must be able to provision the
System workspace with no sandbox to copy from.

Purpose
    Find-or-create, idempotently and create-only, exactly five sheets in exactly
    four folders of the "ITS — System" workspace:

        ITS_Config        -> "01 — Config"
        ITS_Errors        -> "02 — Logs"
        ITS_Quarantine    -> "02 — Logs"
        ITS_Review_Queue  -> "03 — Queues"
        ITS_Daemon_Health -> "04 — Daemons"

    The workspace and the four folders are D1's business
    (`build_system_workspace.py`); this script NEVER creates either. It resolves
    them BY EXACT NAME — workspace name -> folder name -> sheet name — and
    deliberately does NOT read the folder ids from `shared/sheet_ids.py`: at cutover
    those constants still hold the SANDBOX ids (FLIP precedes SEED, and the flip has
    not happened yet when this runs). Name resolution is what makes the builder work
    pre-flip, and it is also invariant 3 (scoped creation).

    Sheets are created EMPTY. No seed rows: ITS_Config is seeded by
    `scripts/seed_its_config.py` (SEED follows FLIP), and every daemon
    self-provisions its own ITS_Daemon_Health row on its first cycle
    (`heartbeat.HeartbeatReporter._resolve_row_id` find-or-create). A builder that
    seeded rows would race that logic and mint the exact duplicates its post-create
    re-find exists to warn about.

Picklist sourcing (the #247->#253 lesson)
    Every PICKLIST option set that a `shared/picklist_validation.py` set governs is
    SOURCED from that module at import time, never hand-typed — a builder option the
    registry lacks blocks the live write path with `PicklistViolationError`, and
    mocks never catch it. `sorted()` everywhere for deterministic, diffable output;
    Smartsheet validates picklist MEMBERSHIP, not option ORDER, and no code reads
    dropdown order, so the rebuilt sheets' dropdowns read alphabetically rather than
    in the sandbox's authoring order. That is cosmetic and intended.

    The sets are read from the module-level frozensets / StrEnums directly, NOT via
    `REGISTRY[sheet_ids.SHEET_*]`: the REGISTRY is keyed by the sheet-id CONSTANT,
    which is still the old tenant's id while this runs (FLIP precedes SEED), so
    keying off it couples the option source to a value this script is trying to
    replace. Going to the enum / frozenset is invariant to the flip.

    Two deliberate deviations from a pure registry read, both recorded in the
    per-column comments below:
      - `field_ops` is unioned into the Workstream sets of ITS_Config and
        ITS_Daemon_Health. `field_ops/fieldops_sync.py` sets WORKSTREAM="field_ops"
        and `seed_generate_and_interval_config.py` writes a Workstream="field_ops"
        config row, but `_WORKSTREAM_VALUES_GLOBAL` does not contain it. Neither of
        those two sheets is REGISTRY-gated, so the union is safe and correct here.
        Whether `_WORKSTREAM_VALUES_GLOBAL` itself should grow `field_ops` is a
        SEPARATE decision (it would widen the ITS_Errors and ITS_Review_Queue write
        gates too) — flagged, not fixed, by a create-only builder.
      - ITS_Review_Queue's "Escalation Level" is the one PICKLIST with no registry
        entry and no code writer; its options are a literal preserving the live set.
        See the WARN this script prints — those labels hard-code named staff.

Invariants (blast-radius controls; this runs against a customer's PRODUCTION tenant
containing Evergreen's own live content)
    1. CREATE-ONLY. GET and create-POST only. No PUT, no DELETE, no update of any
       kind, on anything, ever — including sheets this script itself created. In
       particular the column DESCRIPTIONS below apply only to a sheet this script
       CREATES; they are never back-filled onto an adopted sheet (that would be a
       PUT).
    2. EXACT-NAME FIND, ADOPT-DON'T-TOUCH. Find is an exact, case-sensitive string
       match. On a find the script prints "[skip] ... already present" and moves on:
       no rename, no re-parent, no re-share, no column add, no write of any kind.
    3. SCOPED CREATION. A sheet is created only inside a folder this run resolved BY
       EXACT NAME inside the workspace it resolved BY EXACT NAME. No enumerate-and-act
       across other workspaces or folders; nothing whose name is not in this module's
       canonical list is ever touched.
    4. MINIMAL SET. Exactly the five sheets named above, with exactly the columns
       declared below. No extra sheets, no extra folders, no extra columns.
    5. IDEMPOTENT NO-OP. A second run prints the same ids and creates nothing.
    6. LIVE-WRITE CONFIRMATION. LIVE by default (family convention), --dry-run to
       preview the complete plan. A `seed_its_config.py`-style y/N prompt gates the
       FIRST live create; declining exits having created nothing at all. --dry-run
       never prompts.
    7. NO SECRETS IN OUTPUT. Names and ids only — the bearer token is never printed.
    8. DUPLICATE-NAME AMBIGUITY IS LOUD — AND PARENTS FAIL CLOSED. Smartsheet does
       not enforce unique names, and `smartsheet_client.find_*_by_name_*` returns the
       FIRST match, silently. That is NOT theoretical: FIVE sheets named "ITS_Errors"
       exist in the live "02 — Logs" folder (ids 4195780532326276, 470411799121796,
       2704945844277124, 4505679602601860, 27291433258884) and only the LAST is the
       live one the code uses. So every find here enumerates the parent listing
       itself and counts ALL exact-name matches. The response then SPLITS by role:

         - A TERMINAL object (a sheet) is ADOPTED-FIRST with a loud [WARN]. Adopting
           is a pure GET; the blast radius of guessing wrong is a wrong id in the
           FLIP BLOCK, which the operator reconciles before pasting. Creating a
           sixth ITS_Errors would be strictly worse.
         - A PARENT container (the workspace, a folder) FAILS CLOSED. Adopt-and-warn
           is NOT safe for a parent: it would WARN "this might be the wrong folder"
           and then CREATE real sheets inside that very folder — a write into a
           container the script just declared unidentified, and an invariant-3
           violation in spirit (scoped creation presumes the scope is KNOWN, not
           guessed). So >1 match on the workspace or on a folder resolves to None:
           the sheets under it are reported `blocked-parent`, NOTHING is created
           there, and main() returns nonzero telling the operator to reconcile the
           duplicate containers first.

       Ambiguity is also threaded into the FLIP BLOCK (see `_AMBIGUOUS`): an adopted
       ambiguous sheet renders as `<AMBIGUOUS — N matches, RECONCILE BEFORE FLIPPING>`
       rather than as a clean paste-ready integer. A paste-ready WRONG id is worse
       than an obviously-missing one — the run most likely to be wrong must not print
       the most confident output.

    AUTO_NUMBER guard: Smartsheet REFUSES to create an AUTO_NUMBER column via the API
    (errorCode 1008). No sheet below declares one today, but the guard is implemented
    unconditionally so a future schema addition cannot half-create a sheet — the
    affected sheet is REFUSED whole and a "MANUAL:" instruction is printed instead.
    The guard runs AFTER the find/adopt branch, gating only the CREATE path — that is
    where 1008 actually lives. Ordering it first would make its own remediation
    unreachable: the printed MANUAL: instruction says "create it by hand, then re-run
    — it will adopt the sheet and print the constant", which is impossible if the
    re-run refuses before it ever looks. Adopting is a pure GET and violates nothing.

Status / exit-code matrix (`main()` returns nonzero if ANY sheet is non-terminal)
    exists               adopted untouched, id printed                        exit-OK
    created              created this run, id printed                         exit-OK
    dry-run              --dry-run preview, nothing written                   exit-OK
    declined             operator answered N at the one confirmation prompt   exit-OK
    refused-auto-number  schema declares AUTO_NUMBER; sheet NOT created       NONZERO
    blocked-parent       workspace or target folder is DUPLICATE-AMBIGUOUS    NONZERO
    FAILED               folder absent, or an API error inside the fence      NONZERO

    `refused-auto-number` and `blocked-parent` count toward the nonzero exit
    deliberately: a checklist- or script-driven cutover gates on the exit code, and
    reporting success while a sheet does not exist is exactly the silent failure the
    "never silent" invariant forbids. A DECLINE remains exit 0 — that is an operator
    no-op, not a failure (family convention; D1/D2/D4 agree).

Failure modes
    - Missing ITS_SMARTSHEET_TOKEN in Keychain, or a token scoped to the wrong plan
      → the Keychain read or the first GET raises; nothing is created.
    - Workspace or folder not found by exact name → that sheet is marked FAILED with
      a pointer to build_system_workspace.py (D1). This script never creates either.
    - Workspace or folder DUPLICATE-ambiguous → fail closed (invariant 8): the sheets
      under it are `blocked-parent`, nothing is created, exit nonzero.
    - HTTP non-2xx → `raise_for_status()` / the typed `SmartsheetError` hierarchy
      propagates into the per-sheet fence; the other four sheets still run.
    - Duplicate SHEET name → [WARN], adopt-first, and the FLIP BLOCK renders that
      constant as `<AMBIGUOUS — N matches …>` instead of a pasteable id.
    - Post-create read-back returns EMPTY → [info], not [WARN]: Smartsheet has a
      LIVE-VERIFIED create→read propagation window (see `shared/job_sheet.py`'s
      bounded readiness probe). The id the POST returned is authoritative. Only a
      NON-EMPTY read-back that disagrees is a real concurrent-create race.
    - Partial run (three sheets created, then a failure) → safe: re-running adopts
      what exists and creates only the remainder (invariant 5).
    - ITS_Daemon_Health column-id read-back failure → [WARN] only; the sheet itself
      is fine, but the operator MUST re-read the ids before heartbeats can write.

Consumers
    Operator-run, one-time, during the Phase-1 production cutover. Downstream:
    `shared/sheet_ids.py` (SHEET_CONFIG / SHEET_ERRORS / SHEET_QUARANTINE /
    SHEET_REVIEW_QUEUE / SHEET_DAEMON_HEALTH plus the DAEMON_HEALTH_COLUMNS dict),
    and through those constants essentially every module in the system — the kill
    switch and every `get_setting` read, `shared/error_log.py`, `shared/quarantine.py`,
    `shared/review_queue.py`, `shared/heartbeat.py`, `scripts/watchdog.py` Checks
    A/B/O, and the operator dashboard's errors / review-queue panels and ACT verbs.

    ITS_Daemon_Health is the special case worth naming: every heartbeat write is
    COLUMN-ID-KEYED (`smartsheet_client.update_row_cells_by_id` / `add_row_by_id`
    against `sheet_ids.DAEMON_HEALTH_COLUMNS`), NOT title-keyed. A freshly created
    sheet gets twelve brand-new column ids, so until the operator pastes the printed
    dict every heartbeat targets column ids on a DIFFERENT sheet on a DIFFERENT plan
    and fails — SILENTLY, because HeartbeatReporter is broad-except-isolated
    ("heartbeat must never block primary work") and degrades to an ITS_Errors row
    with error_code=daemon_health_write_failed. Hence the mandatory read-back below.

No §43 successor-remediation runbook entry is needed: this is a one-time operator
migration with no Tier-2-recurring failure mode. It runs under Seth's hand during
cutover, is idempotent, and has no daemon, no schedule, and no runtime consumer.

Cutover sequence (FLIP precedes SEED):
  1. build_system_workspace.py (D1) — creates the workspace + the four folders.
     Flip WORKSPACE_SYSTEM + FOLDER_SYSTEM_* in shared/sheet_ids.py.
  2. THIS script — note the printed SHEET ids and the DAEMON_HEALTH_COLUMNS dict.
  3. Flip SHEET_CONFIG / SHEET_ERRORS / SHEET_QUARANTINE / SHEET_REVIEW_QUEUE /
     SHEET_DAEMON_HEALTH and paste DAEMON_HEALTH_COLUMNS (the FLIP BLOCK this
     script prints). Heartbeats stay silently dark until that paste is committed
     and the daemons restart.
  4. seed_its_config.py (and the other seeders) — SEED follows FLIP.
  5. OPERATOR: reconcile any [WARN] duplicate-name ambiguity BEFORE step 3.

Convention: LIVE-write by default; pass --dry-run to preview.

Auth: ITS_SMARTSHEET_TOKEN from macOS Keychain.

Run from ~/its (or a worktree):
    python3 scripts/migrations/build_system_sheets.py --dry-run
    python3 scripts/migrations/build_system_sheets.py

Exit 0 on success, no-op, or an operator-declined confirmation; nonzero if any
sheet is FAILED, refused-auto-number, or blocked-parent.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, get_args

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import heartbeat, keychain, picklist_validation, smartsheet_client  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

# Canonical names. The workspace and all four folders use U+2014 EM DASH with a
# single space either side, written as explicit — ESCAPES (the D2 idiom): an
# editor, a copy-paste, or a Unicode normalizer can silently swap an em dash for an
# en dash or a hyphen, and the resulting miss is invisible in a diff. This script
# never creates a workspace or a folder, so a workspace/folder miss is a hard
# refusal rather than a duplicate — but the SHEET names ride the same discipline,
# and there a miss WOULD mint a duplicate in the customer's production plan.
WORKSPACE_NAME = "ITS \u2014 System"  # renders as: ITS — System

FOLDER_CONFIG = "01 \u2014 Config"    # renders as: 01 — Config
FOLDER_LOGS = "02 \u2014 Logs"        # renders as: 02 — Logs
FOLDER_QUEUES = "03 \u2014 Queues"    # renders as: 03 — Queues
FOLDER_DAEMONS = "04 \u2014 Daemons"  # renders as: 04 — Daemons

EM_DASH = "\u2014"
# Every dash-like codepoint that is NOT the canonical one. A normalizer substitutes one
# of these; each would silently miss the find. Mirrors D1's _NON_CANONICAL_DASHES.
_NON_CANONICAL_DASHES = (
    "\u002d",  # HYPHEN-MINUS
    "\u2010",  # HYPHEN
    "\u2011",  # NON-BREAKING HYPHEN
    "\u2012",  # FIGURE DASH
    "\u2013",  # EN DASH
    "\u2015",  # HORIZONTAL BAR
    "\u2212",  # MINUS SIGN
)
_CANONICAL_NAMES = (WORKSPACE_NAME, FOLDER_CONFIG, FOLDER_LOGS, FOLDER_QUEUES, FOLDER_DAEMONS)


def _assert_canonical_dashes() -> None:
    """Fail CLOSED at import if any canonical name's dash was normalized (rule-4 parity).

    An EXPLICIT raise, NOT a bare `assert` -- `python -O` / PYTHONOPTIMIZE strips
    `assert`, which would silently disable this fail-closed check exactly when a
    production run most needs it. Mirrors D1's `_assert_canonical_dashes`: each canonical
    name must contain exactly ONE U+2014 EM DASH surrounded by single spaces and no other
    dash-like codepoint. This script never CREATES a workspace/folder (a miss there is a
    hard refusal), but the SHEET names ride the same discipline and there a miss WOULD
    mint a duplicate in the customer's production plan.
    """
    for name in _CANONICAL_NAMES:
        if name.count(EM_DASH) != 1 or f" {EM_DASH} " not in name:
            raise ValueError(
                f"canonical_name_dash_corrupted: {name!r} must contain exactly one "
                "U+2014 EM DASH surrounded by single spaces. A dash was normalized -- "
                "restore the \\u2014 escape before running against any tenant."
            )
        for bad in _NON_CANONICAL_DASHES:
            if bad in name:
                raise ValueError(
                    f"canonical_name_dash_corrupted: {name!r} contains U+{ord(bad):04X}, "
                    "not the canonical U+2014 EM DASH. Restore the \\u2014 escape."
                )


_assert_canonical_dashes()


# ---- picklist option sets (sourced, never hand-typed) --------------------

# `field_ops` is NOT in _WORKSTREAM_VALUES_GLOBAL but IS written to both ITS_Config
# (seed_generate_and_interval_config.py) and ITS_Daemon_Health (fieldops_sync.py
# WORKSTREAM = "field_ops"). Neither sheet is in picklist_validation.REGISTRY, so
# nothing gates those writes client-side today — but the option must exist on the
# column the moment the operator enables Smartsheet's server-side "restrict to
# picklist values only". Union, so the seven governed values stay SOURCED.
# Widening _WORKSTREAM_VALUES_GLOBAL itself is a separate reviewed decision (it
# would also widen the ITS_Errors / ITS_Review_Queue write gates).
_WORKSTREAM_PLUS_FIELD_OPS: list[str] = sorted(
    picklist_validation._WORKSTREAM_VALUES_GLOBAL | {"field_ops"}
)

# The `global` catch-all variant — gates ITS_Errors and ITS_Review_Queue Workstream.
# CL-15: set-equal to the live ITS_Review_Queue picklist, so a builder sourcing from
# picklist_validation cannot under-provision any workstream review_queue.add() writes.
_WORKSTREAM_GLOBAL: list[str] = sorted(picklist_validation._WORKSTREAM_VALUES_GLOBAL)

# The `other` catch-all variant — ITS_Quarantine ONLY. This sheet has no `global`
# and no `progress_reports`. Do not cross-wire it with the set above.
_WORKSTREAM_OTHER: list[str] = sorted(picklist_validation._WORKSTREAM_VALUES_OTHER)

_SEVERITY: list[str] = sorted(s.value for s in picklist_validation.Severity)
_REVIEW_REASON: list[str] = sorted(r.value for r in picklist_validation.ReviewReason)
_REVIEW_STATUS: list[str] = sorted(s.value for s in picklist_validation.ReviewStatus)
_SLA_TIER: list[str] = sorted(t.value for t in picklist_validation.SlaTier)
_QUARANTINE_DISPOSITION: list[str] = sorted(picklist_validation._QUARANTINE_DISPOSITION_VALUES)

# The writer's own Literal IS the source of truth for ITS_Daemon_Health's status
# column, so a value added to shared.heartbeat.HeartbeatStatus cannot ship without
# this builder knowing. NOTE this deliberately drops the live sandbox sheet's dead
# NEVER_RAN / STALE options (zero non-test writers anywhere) and adds DEGRADED +
# CIRCUIT_OPEN, which the writer emits but the sandbox column lacks — CIRCUIT_OPEN
# is precisely the observability write that matters most under an open breaker.
_HEARTBEAT_STATUS: list[str] = sorted(str(v) for v in get_args(heartbeat.HeartbeatStatus))

# ITS_Review_Queue "Escalation Level": the ONE picklist with no REGISTRY entry and
# no code writer (the escalation walker named TBD in review_queue.py is unbuilt).
# Literal, preserving the live set — see _warn_escalation_labels().
_ESCALATION_LEVELS: list[str] = ["L1-Teala", "L2-Sam", "L3-Jacob", "Escalated-External"]


# ---- column schemas (explicit + self-contained, invariant 4) -------------
#
# Column ORDER below reproduces the live sheets' index order exactly. Descriptions
# are set at CREATE time only (the build_po_log_sheet.py COLUMN_SCHEMA idiom) and
# are never back-filled onto an adopted sheet (invariant 1).

CONFIG_COLUMNS: list[dict[str, Any]] = [
    {"title": "Setting", "type": "TEXT_NUMBER", "primary": True,
     "description": "Canonical dotted config key (e.g. 'system.state', "
                    "'safety_reports.portal_poll.polling_enabled'). Rows are keyed on "
                    "(Setting, Workstream) — the same Setting name under two Workstreams is "
                    "two different rows."},
    {"title": "Value", "type": "TEXT_NUMBER",
     "description": "The value, always stored as a STRING ('true'/'false' for booleans, integer "
                    "seconds for intervals, JSON for structured values). get_setting() returns "
                    "None when this cell is empty or non-string. The only cell mutated at "
                    "runtime (the §50 Class-A config editor + dashboard interval writes)."},
    {"title": "Workstream", "type": "PICKLIST", "options": _WORKSTREAM_PLUS_FIELD_OPS,
     "description": "Scope of the row — ITS matches on Setting AND Workstream. Sourced from "
                    "picklist_validation._WORKSTREAM_VALUES_GLOBAL plus 'field_ops', which the "
                    "set is missing but fieldops_sync's config rows require."},
    {"title": "Description", "type": "TEXT_NUMBER",
     "description": "Operator-facing note on what the key CONTROLS and any activation "
                    "precondition. Per HOUSE_REFLEXES §5 it states what the switch MEANS and "
                    "never asserts a live gate state — the Value cell is the live state."},
    {"title": "Last Modified", "type": "DATETIME", "systemColumnType": "MODIFIED_DATE",
     "description": "Smartsheet-maintained provenance. Never written by ITS code; the audit "
                    "trail for a §50 privileged config edit."},
    {"title": "Modified By", "type": "CONTACT_LIST", "systemColumnType": "MODIFIED_BY",
     "description": "Smartsheet-maintained provenance — who last changed the row. Never "
                    "written by ITS code."},
]

ERRORS_COLUMNS: list[dict[str, Any]] = [
    {"title": "Error", "type": "TEXT_NUMBER", "primary": True,
     "description": "Short stable error_code label ('started', 'uncaught_exception', "
                    "'<daemon>_pending_fetch_sustained'). Also the dedupe-key suffix for both "
                    "push legs and a filter axis for the dashboard mark-resolved / clear verbs."},
    {"title": "Timestamp", "type": "DATE",
     "description": "Occurrence date, a bare YYYY-MM-DD (date.today().isoformat()). The age "
                    "axis for watchdog Check O rotation. DATE is deliberate — do NOT retype to "
                    "DATETIME (rejects a bare date, 4000) or ABSTRACT_DATETIME (rejects an "
                    "offset, 5536)."},
    {"title": "Severity", "type": "PICKLIST", "options": _SEVERITY,
     "description": "INFO / WARN / ERROR / CRITICAL, sourced from the Severity StrEnum. "
                    "CRITICAL with a blank 'Resolved At' is the open 'am I on fire' set watchdog "
                    "Check B counts and errors_rotation refuses to delete."},
    {"title": "Script", "type": "TEXT_NUMBER",
     "description": "Dotted script identity passed to @its_error_log (e.g. "
                    "'safety_reports.portal_poll'). The other half of the push-leg dedupe key."},
    {"title": "Message", "type": "TEXT_NUMBER",
     "description": "Free-text event message, passed through shared/redact.redact before the "
                    "write (§54 — ITS_Errors is an off-Mac egress surface; the on-Mac log keeps "
                    "the raw text)."},
    {"title": "Traceback", "type": "TEXT_NUMBER",
     "description": "Formatted traceback for exception rows, also §54-redacted. Blank otherwise."},
    {"title": "Correlation_ID", "type": "TEXT_NUMBER",
     "description": "Shared UUID threading this row to its triple-fire Resend email + Sentry "
                    "event. Created here from the start — supersedes the retrofit migration "
                    "scripts/migrations/add_correlation_id_column.py, which no-ops against a "
                    "sheet this builder created."},
    {"title": "Surfaced At", "type": "DATE",
     "description": "Operator-triage cell: when the event was surfaced to a human. No code "
                    "writer — the watchdog CRITICAL digest only tells the operator to query it."},
    {"title": "Resolved At", "type": "DATE",
     "description": "TERMINALITY STAMP — a CRITICAL is deletable only once this is non-blank "
                    "(shared/errors_rotation.errors_row_is_terminal is the single source of "
                    "truth). Blank means OPEN and UNROTATABLE; that is what re-opened the "
                    "19,975/20,000 row-cap lockout. Stamped by the dashboard mark-resolved verb."},
    {"title": "Resolved By", "type": "CONTACT_LIST",
     "description": "Operator-triage cell: who resolved the row. No code writer — the dashboard "
                    "verb carries operator identity in its audit row instead."},
    {"title": "Notes", "type": "TEXT_NUMBER",
     "description": "Free-text operator triage notes. No code writer. NOT the same column as "
                    "ITS_Review_Queue's 'Resolution Notes' — do not cross-wire the schemas."},
    {"title": "Workstream", "type": "PICKLIST", "options": _WORKSTREAM_GLOBAL,
     "description": "Owning workstream tag, gated by REGISTRY[SHEET_ERRORS]['Workstream'] = the "
                    "'global' catch-all set (NOT ITS_Quarantine's 'other' variant). error_log "
                    "writes no Workstream cell today — the column is operator classification "
                    "with the write-gate pre-registered."},
]

QUARANTINE_COLUMNS: list[dict[str, Any]] = [
    {"title": "Quarantined Message", "type": "TEXT_NUMBER", "primary": True,
     "description": "Short operator-facing label, written as 'quarantined: <sender>'. Mirrors "
                    "the ITS_Errors 'Error' convention — a stable label, not the message body."},
    {"title": "Received At", "type": "DATE",
     "description": "Receipt timestamp. The writer passes an offset-bearing ISO-8601 UTC string; "
                    "the column is DATE, matching the sandbox sheet the writer has been exercised "
                    "against. If the production tenant rejects the write, fix the WRITER (naive "
                    "Pacific wall-clock) — do NOT widen this column."},
    {"title": "Sender", "type": "TEXT_NUMBER",
     "description": "Sender address that failed the allowlist / scope / header-forgery check "
                    "(Invariant 2 Layer 1). The operator's key into ITS_Trusted_Contacts."},
    {"title": "Subject", "type": "TEXT_NUMBER",
     "description": "Message subject, truncated to 200 chars by the caller. UNTRUSTED external "
                    "text — never interpolate into a prompt."},
    {"title": "Summary", "type": "TEXT_NUMBER",
     "description": "First ~200 chars of the body, verbatim. Deliberately NOT AI-generated — "
                    "nothing that failed the sender allowlist reaches Anthropic."},
    {"title": "Workstream", "type": "PICKLIST", "options": _WORKSTREAM_OTHER,
     "description": "Which workstream's allowlist rejected the message. Catch-all is 'other', "
                    "NOT 'global' — this sheet deliberately differs from ITS_Review_Queue. Gated "
                    "by REGISTRY[SHEET_QUARANTINE]['Workstream']."},
    {"title": "Reviewed", "type": "CHECKBOX",
     "description": "Operator-workflow cell, left blank by ITS. Ticked after triage."},
    {"title": "Added to Allowlist", "type": "CHECKBOX",
     "description": "Operator-workflow cell, left blank by ITS. Ticked when the sender was added "
                    "to ITS_Trusted_Contacts."},
    {"title": "Reviewed By", "type": "CONTACT_LIST",
     "description": "Operator-workflow cell, left blank by ITS."},
    {"title": "Reviewed At", "type": "DATE",
     "description": "Operator-workflow cell, left blank by ITS."},
    {"title": "Notes", "type": "TEXT_NUMBER",
     "description": "Operator notes AND the graceful-degrade carrier for the disposition reason: "
                    "quarantine.py writes '[reason: <QuarantineReason>]' here because this sheet "
                    "has no dedicated Reason column (by design — live and code agree)."},
    {"title": "Disposition", "type": "PICKLIST", "options": _QUARANTINE_DISPOSITION,
     "description": "Operator review action (RELEASE / DELETE / ESCALATE), sourced from the "
                    "registry. DORMANT — registered and present, but no ITS writer yet "
                    "(Phase 3a decision D1 = ADD)."},
]

REVIEW_QUEUE_COLUMNS: list[dict[str, Any]] = [
    {"title": "Item ID", "type": "TEXT_NUMBER", "primary": True,
     "description": "Stable identifier, format <workstream>-<YYYYMMDD>-<HHMMSS> UTC. Written by "
                    "review_queue.add(), read back by get_status(), named by watchdog Check A."},
    {"title": "Created At", "type": "DATE",
     "description": "Enqueue date (date.today().isoformat()). DATE not DATETIME is intentional — "
                    "is_past_sla() maps SLA hours to whole-DAY thresholds and parses this with "
                    "date.fromisoformat(). Retyping breaks that parse."},
    {"title": "Workstream", "type": "PICKLIST", "options": _WORKSTREAM_GLOBAL,
     "description": "Owning workstream. Gated by REGISTRY[SHEET_REVIEW_QUEUE]['Workstream']; "
                    "must stay in lockstep with review_queue.VALID_WORKSTREAMS (they drifted "
                    "once, at P5). Also the DASH-13 resolve filter."},
    {"title": "Summary", "type": "TEXT_NUMBER",
     "description": "One-line human-readable description of why the item landed. The DASH-13 "
                    "resolve verb matches a STARTS-WITH prefix of this cell, so recurring classes "
                    "must keep a stable prefix."},
    {"title": "Reason", "type": "PICKLIST", "options": _REVIEW_REASON,
     "description": "Standardized why-it-landed code, sourced from the ReviewReason StrEnum. "
                    "Defaults to 'other'."},
    {"title": "Severity", "type": "PICKLIST", "options": _SEVERITY,
     "description": "Reuses shared.error_log.Severity verbatim. Defaults to WARN; "
                    "security-flagged items are typically CRITICAL."},
    {"title": "SLA Tier", "type": "PICKLIST", "options": _SLA_TIER,
     "description": "SLA tier per Op Stds (4h safety intake / 24h RFQ drafts / 48h subcontract "
                    "drafts). is_past_sla() maps these to 2x-SLA day thresholds; watchdog "
                    "Check A WARNs on breach."},
    {"title": "Source File", "type": "TEXT_NUMBER",
     "description": "Optional source document path or inbox URL. Written as empty string when "
                    "the caller passes None."},
    {"title": "Payload", "type": "TEXT_NUMBER",
     "description": "Compact JSON (separators=(',',':')) of the structured data the reviewer "
                    "needs. Free-form per caller."},
    {"title": "Status", "type": "PICKLIST", "options": _REVIEW_STATUS,
     "description": "Review lifecycle: PENDING on write, terminal APPROVED/REJECTED via the "
                    "DASH-13 dashboard verb. Nothing else moves a row out of PENDING."},
    {"title": "Security Flag", "type": "CHECKBOX",
     "description": "True when an anomaly_logger sentinel fired (Invariant 2 Layer 5 post-hoc "
                    "tripwire) or a §34 screening refusal routed the item here. Reviewers triage "
                    "suspected injection on this first."},
    {"title": "Assigned To", "type": "CONTACT_LIST",
     "description": "Operator-workflow cell — never written by ITS code."},
    {"title": "Resolved By", "type": "CONTACT_LIST",
     "description": "Operator-workflow cell, DELIBERATELY not written by the DASH-13 verb: this "
                    "is CONTACT_LIST-typed and a bare login string can fail its validation, so "
                    "the resolving operator's identity rides in Resolution Notes instead. Do not "
                    "'fix' that."},
    {"title": "Resolved At", "type": "DATE",
     "description": "Date the row reached a terminal Status. Stamped (UTC date, ISO) by the "
                    "DASH-13 resolve verb; otherwise operator-set."},
    {"title": "Resolution Notes", "type": "TEXT_NUMBER",
     "description": "Free-text rationale. The DASH-13 verb writes 'resolved via dashboard by "
                    "<operator>' plus an optional note capped at 300 chars — this cell carries "
                    "the operator attribution (see Resolved By)."},
    {"title": "Source Sheet ID", "type": "TEXT_NUMBER",
     "description": "Programmatic ID of the sheet that triggered this review item; used by "
                    "automation to mark the source row reviewed. DORMANT — no code writer today."},
    {"title": "Source Row ID", "type": "TEXT_NUMBER",
     "description": "Programmatic ID of the row that triggered this review item. DORMANT — no "
                    "code writer today."},
    {"title": "Source Row Permalink", "type": "TEXT_NUMBER",
     "description": "Clickable URL to the source row for one-click human traceback. DORMANT — "
                    "no code writer today."},
    {"title": "Sender", "type": "TEXT_NUMBER",
     "description": "Email sender address for sender-allowlist / email-triage items. DORMANT on "
                    "THIS sheet — the only 'Sender' writer in the codebase (shared/quarantine.py) "
                    "targets ITS_Quarantine. Email Triage is the future writer."},
    {"title": "Escalation Level", "type": "PICKLIST", "options": _ESCALATION_LEVELS,
     "description": "Escalation tier per the Op Stds reviewer chain. DORMANT — no code writer, "
                    "and the escalation walker is TBD. NOT registry-gated, so these options are "
                    "a literal; they hard-code named staff (see the [WARN] this builder prints)."},
    {"title": "Created By", "type": "CONTACT_LIST", "systemColumnType": "CREATED_BY",
     "description": "Auto-stamped by Smartsheet. System column — never written by ITS."},
    {"title": "Modified By", "type": "CONTACT_LIST", "systemColumnType": "MODIFIED_BY",
     "description": "Auto-stamped by Smartsheet. System column — never written by ITS."},
]

DAEMON_HEALTH_COLUMNS_SCHEMA: list[dict[str, Any]] = [
    {"title": "Daemon Name", "type": "TEXT_NUMBER", "primary": True,
     "description": "Primary key — the daemon's registered name (each module's DAEMON_NAME). One "
                    "row per daemon, update-in-place per cycle; find_row_by_primary matches here."},
    {"title": "Workstream", "type": "PICKLIST", "options": _WORKSTREAM_PLUS_FIELD_OPS,
     "description": "Owning workstream (each daemon's WORKSTREAM const), written once at "
                    "self-provision. Sourced from _WORKSTREAM_VALUES_GLOBAL plus 'field_ops', "
                    "which fieldops_sync writes but that set lacks."},
    {"title": "Enabled", "type": "CHECKBOX",
     "description": "ARCH-1: report-filter metadata ONLY — this is NOT the runtime gate. The "
                    "canonical runtime gate is the ITS_Config row "
                    "'<workstream>.<daemon>.polling_enabled'. Self-provisioned rows register "
                    "true; verify_cutover skips rows where this is falsy."},
    {"title": "Interval Seconds", "type": "TEXT_NUMBER",
     "description": "The daemon's launchd StartInterval in seconds. verify_cutover's staleness "
                    "check uses 2.0x this value as the freshness limit, so a non-numeric value "
                    "fails that check."},
    {"title": "Source ID", "type": "TEXT_NUMBER",
     "description": "Registration provenance (each daemon's _REGISTRATION_SOURCE_ID) identifying "
                    "the plist / launchd label that provisioned the row. Written once."},
    {"title": "Last Heartbeat", "type": "TEXT_NUMBER",
     "description": "UTC ISO-8601 timestamp of the last completed cycle. TEXT_NUMBER is CORRECT "
                    "and load-bearing: the writer stores an OFFSET-AWARE string, which "
                    "ABSTRACT_DATETIME rejects (5536) and DATETIME rejects (4000). Do not "
                    "'upgrade' this column."},
    {"title": "Last Cycle Status", "type": "PICKLIST", "options": _HEARTBEAT_STATUS,
     "description": "Outcome of the most recent cycle, sourced from the writer's own "
                    "shared.heartbeat.HeartbeatStatus Literal. CIRCUIT_OPEN is written under "
                    "circuit_breaker.bypass() so it can still land. The self-provision create "
                    "omits this cell so a restricted dropdown cannot reject the create."},
    {"title": "Last Cycle Items Processed", "type": "TEXT_NUMBER",
     "description": "Integer count of items handled in the last cycle. Written every cycle, "
                    "including zero."},
    {"title": "Total Cycles Today", "type": "TEXT_NUMBER",
     "description": "ARCH-3: despite the title the semantics are LIFETIME MONOTONIC, never "
                    "daily-reset — the counter lives in ~/its/state/heartbeat_row_ids.json so no "
                    "read-before-write round trip is needed. The sheet_ids key is 'total_cycles'; "
                    "the title rename is a deferred UI-only cleanup and the column id is stable "
                    "across it."},
    {"title": "Last Error Summary", "type": "TEXT_NUMBER",
     "description": "Summary of the last error context. Written only when write_row receives a "
                    "non-None error_summary — otherwise the prior value is left untouched "
                    "(partial-cell updates, never a full-row rewrite)."},
    {"title": "Last Error Correlation ID", "type": "TEXT_NUMBER",
     "description": "Correlation ID joining this row to the matching ITS_Errors record / Sentry "
                    "event / Resend alert. Written only when supplied."},
    {"title": "Notes", "type": "TEXT_NUMBER",
     "description": "Free-text per-cycle operator note. Written only when supplied."},
]

# snake_case key in shared/sheet_ids.DAEMON_HEALTH_COLUMNS -> live column TITLE.
# The ONE mismatch is total_cycles -> "Total Cycles Today" (ARCH-3, above).
DAEMON_HEALTH_KEY_TO_TITLE: tuple[tuple[str, str], ...] = (
    ("daemon_name", "Daemon Name"),
    ("workstream", "Workstream"),
    ("enabled", "Enabled"),
    ("interval_seconds", "Interval Seconds"),
    ("source_id", "Source ID"),
    ("last_heartbeat", "Last Heartbeat"),
    ("last_cycle_status", "Last Cycle Status"),
    ("last_cycle_items_processed", "Last Cycle Items Processed"),
    ("total_cycles", "Total Cycles Today"),
    ("last_error_summary", "Last Error Summary"),
    ("last_error_correlation_id", "Last Error Correlation ID"),
    ("notes", "Notes"),
)

# Statuses that mean "the sheet does NOT exist as this run intended" — each drives the
# nonzero exit (F5). A create-only builder that reported success while a sheet was
# refused (AUTO_NUMBER), blocked by an ambiguous parent, or errored inside the fence
# would silently pass a checklist that gates on the exit code. A `declined` is NOT
# here: an operator no-op is exit 0 by family convention (D1/D2/D4 agree).
NON_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"FAILED", "refused-auto-number", "blocked-parent"}
)

SHEET_DAEMON_HEALTH_NAME = "ITS_Daemon_Health"

# (sheet name, folder name, shared/sheet_ids.py constant, column schema).
# The MINIMAL SET (invariant 4). Order is the cutover reading order.
SHEETS: tuple[tuple[str, str, str, list[dict[str, Any]]], ...] = (
    ("ITS_Config", FOLDER_CONFIG, "SHEET_CONFIG", CONFIG_COLUMNS),
    ("ITS_Errors", FOLDER_LOGS, "SHEET_ERRORS", ERRORS_COLUMNS),
    ("ITS_Quarantine", FOLDER_LOGS, "SHEET_QUARANTINE", QUARANTINE_COLUMNS),
    ("ITS_Review_Queue", FOLDER_QUEUES, "SHEET_REVIEW_QUEUE", REVIEW_QUEUE_COLUMNS),
    (SHEET_DAEMON_HEALTH_NAME, FOLDER_DAEMONS, "SHEET_DAEMON_HEALTH",
     DAEMON_HEALTH_COLUMNS_SCHEMA),
)


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---- confirmation seam (invariant 6) ------------------------------------
#
# A module-level function, deliberately NOT a --yes flag: the prompt IS the control,
# and a flag would let it be switched off from a shell history line. Tests
# monkeypatch `_confirm`.


def _confirm(prompt: str) -> bool:
    """Ask the operator to authorise live writes. True only on an explicit 'y'."""
    return input(f"{prompt} [y/N] ").strip().lower() == "y"


class LiveWriteGate:
    """Prompts ONCE before the first live create, then remembers the answer.

    Invariant 6. `allow()` returns False for the rest of the run if the operator
    declines, so a decline creates nothing at all (not just "nothing more").
    """

    def __init__(self, *, dry_run: bool) -> None:
        self._dry_run = dry_run
        self._answer: bool | None = None

    @property
    def declined(self) -> bool:
        return self._answer is False

    def allow(self, what: str) -> bool:
        """True if a live create may proceed. Never prompts under --dry-run."""
        if self._dry_run:
            return False
        if self._answer is None:
            print(f"\nAbout to make the FIRST live create in {WORKSPACE_NAME!r}: {what}")
            self._answer = _confirm("Proceed with live creates?")
            if not self._answer:
                print("[skip] Operator declined; nothing was created.")
        return self._answer


# ---- duplicate-aware finders (invariant 8) ------------------------------


def _find_workspaces() -> list[dict[str, Any]]:
    """Return the WHOLE object of every workspace named WORKSPACE_NAME (exact match).

    Whole objects, not just ids (rule 1): `GET /workspaces?includeAll=true` lists every
    workspace the token is a MEMBER of — across accounts and plans — and the only
    discriminators between "the production workspace" and "the sandbox workspace shared
    into this identity" are `accessLevel` and `permalink`, which an id-only finder throws
    away. This script never CREATES the workspace, but it must still refuse to build
    sheets inside one it does not OWN (the sandbox-shared-into-production trap D1/D2 guard
    against). Live-verified shape: each entry carries id / name / accessLevel / permalink.
    """
    r = requests.get(f"{BASE}/workspaces?includeAll=true", headers=_headers(), timeout=30)
    r.raise_for_status()
    data: list[dict[str, Any]] = r.json().get("data", [])
    return [ws for ws in data if ws.get("name") == WORKSPACE_NAME]


def _find_workspace_ids() -> list[int]:
    """Ids of ALL workspaces named WORKSPACE_NAME — the count invariant 8 needs."""
    return [int(ws["id"]) for ws in _find_workspaces()]


def _find_folder_ids(workspace_id: int, name: str) -> list[int]:
    """Return the ids of ALL top-level folders in `workspace_id` named `name`.

    `smartsheet_client.find_folder_by_name_in_workspace` hides duplicates (first
    match wins, silently), so enumerate the workspace listing ourselves.
    """
    r = requests.get(f"{BASE}/workspaces/{workspace_id}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return [int(f["id"]) for f in r.json().get("folders", []) if f.get("name") == name]


def _find_sheet_ids(folder_id: int, name: str) -> list[int]:
    """Return the ids of ALL sheets in `folder_id` named `name` (exact match).

    `smartsheet_client.find_sheet_by_name_in_folder` returns the FIRST match and
    hides the rest. That is not academic here: FIVE sheets named "ITS_Errors" live
    in "02 — Logs" and the first is NOT the one shared/sheet_ids.py points at. The
    count is what invariant 8 needs, so enumerate the folder listing ourselves.
    """
    r = requests.get(f"{BASE}/folders/{folder_id}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return [int(s["id"]) for s in r.json().get("sheets", []) if s.get("name") == name]


# Constants whose resolved id came from an AMBIGUOUS (>1 exact-name match) find,
# mapped to the match COUNT. Populated by `_adopt_first`, consumed by the FLIP BLOCK
# so a known-ambiguous id is never rendered as clean paste-ready output (F2). Reset
# at the top of every `main()` so a second in-process run starts clean.
_AMBIGUOUS: dict[str, int] = {}


def _adopt_first(ids: list[int], kind: str, name: str, constant: str) -> int:
    """Adopt ids[0] of a TERMINAL object, WARNing loudly when ambiguous (invariant 8).

    TERMINAL only — a sheet. Do NOT call this for a workspace or a folder: adopting a
    possibly-wrong PARENT and then creating real sheets inside it is the failure this
    module fails closed on (`_resolve_unique_parent`). Adopting a terminal sheet is a
    pure GET whose worst case is a wrong id in the FLIP BLOCK, and `_AMBIGUOUS` makes
    sure that id is never rendered as pasteable.
    """
    if len(ids) > 1:
        _AMBIGUOUS[constant] = len(ids)
        print(f"[WARN] duplicate_name_ambiguity: {len(ids)} {kind}s are named {name!r} "
              f"(ids={', '.join(str(i) for i in ids)}). Adopting the FIRST ({ids[0]}) and "
              f"creating nothing — but the first match may NOT be the live one (the live "
              f"'02 — Logs' folder holds FIVE 'ITS_Errors' sheets and the first is the wrong "
              f"one). Reconcile — identify the live object, delete or rename the rest — BEFORE "
              f"flipping {constant}. The FLIP BLOCK below will NOT print this id.")
    return ids[0]


def _resolve_unique_parent(ids: list[int], kind: str, name: str, remedy: str) -> int | None:
    """Resolve a PARENT container, failing CLOSED on duplicate-name ambiguity (F1).

    Invariant 8's adopt-and-warn is sanctioned for a TERMINAL object only. A parent is
    different in kind: this script goes on to CREATE sheets inside whatever it returns,
    so adopting an unidentified container means writing into a container the script
    itself just said might be the wrong one — and invariant 3 (scoped creation) presumes
    a KNOWN scope, not a guessed one. So >1 match returns None; the caller reports the
    dependent sheets `blocked-parent`, creates nothing, and main() exits nonzero.

    Returns the single id, or None when absent (0 matches) or ambiguous (>1).
    """
    if not ids:
        print(f"[WARN] {kind}_not_found: no {kind} named {name!r}. {remedy}")
        return None
    if len(ids) > 1:
        print(f"[WARN] duplicate_parent_ambiguity: {len(ids)} {kind}s are named {name!r} "
              f"(ids={', '.join(str(i) for i in ids)}). FAILING CLOSED — this script creates "
              f"SHEETS inside this {kind}, and it will not write into a container it cannot "
              f"uniquely identify. Nothing under it is created this run. Reconcile the "
              f"duplicate {kind}s in Smartsheet (identify the live one, delete or rename the "
              f"rest), then re-run — the run is idempotent and will create only the remainder.")
        return None
    return ids[0]


# ---- AUTO_NUMBER guard --------------------------------------------------


def _auto_number_titles(columns: list[dict[str, Any]]) -> list[str]:
    """Titles of any AUTO_NUMBER columns in the schema.

    Smartsheet REFUSES an AUTO_NUMBER column on the create path (errorCode 1008).
    No current schema declares one, but a half-created sheet (all columns but the
    auto-number) is worse than none — so a sheet with any AUTO_NUMBER is refused
    WHOLE and the operator gets a MANUAL: instruction.
    """
    return [str(c["title"]) for c in columns if c.get("type") == "AUTO_NUMBER"]


# ---- §45 re-find-after-create (propagation-aware) -----------------------

# Mirrors shared/job_sheet.py's LIVE-VERIFIED create->read readiness probe: 5 tries,
# ~2s apart. Smartsheet's create->read propagation window is real (a brand-new job's
# first filing lost its per-job row to it, 2026-07-13 live smoke) and an empty
# read-back inside that window means "not visible YET", never "a duplicate exists".
_REFIND_ATTEMPTS = 5
_REFIND_SLEEP_SECONDS = 2.0


def _verify_after_create(folder_id: int, sheet_name: str, new_id: int, constant: str) -> None:
    """§45 re-find-after-create, classified by SHAPE rather than by inequality (F3).

    Three distinct outcomes, and conflating them is what made the happy path alarming:

      - read-back == [new_id]      -> clean; say nothing.
      - read-back is EMPTY         -> [info], not [WARN]. This is the create->read
                                      propagation window, not a duplicate. The id the
                                      POST returned is authoritative; a bounded probe
                                      (5 x ~2s, the job_sheet.py shape) gives it a
                                      chance to settle before we say so.
      - read-back NON-EMPTY and    -> the REAL [WARN]: a concurrent create raced us,
        != [new_id]                   or a same-named sheet already existed. Reconcile
                                      before flipping `constant`.

    Read-only (GETs). Never raises on the propagation case — the sheet exists either
    way; this only decides what the operator is told.
    """
    after: list[int] = []
    for attempt in range(_REFIND_ATTEMPTS):
        after = _find_sheet_ids(folder_id, sheet_name)
        if after:
            break
        if attempt < _REFIND_ATTEMPTS - 1:
            time.sleep(_REFIND_SLEEP_SECONDS)

    if after == [new_id]:
        return
    if not after:
        print(f"[info] sheet_readback_pending: post-create read-back for {sheet_name!r} in "
              f"folder {folder_id} returned no match yet after {_REFIND_ATTEMPTS} tries "
              f"(Smartsheet create->read propagation window, same one shared/job_sheet.py "
              f"probes for). This is NOT a duplicate: sheet_id={new_id} from the create "
              f"response is authoritative — use it for {constant}.")
        return
    print(f"[WARN] sheet_race_duplicate: created {new_id} but a name lookup returns {after} "
          f"— another sheet named {sheet_name!r} exists in folder {folder_id}; reconcile "
          f"before flipping {constant}.")


# ---- per-sheet build (create-only, adopt-don't-touch) -------------------


def build_one_sheet(
    sheet_name: str,
    folder_id: int,
    constant: str,
    columns: list[dict[str, Any]],
    gate: LiveWriteGate,
    *,
    dry_run: bool,
) -> tuple[str, int | None]:
    """Find-or-create ONE sheet inside an already-resolved folder.

    Returns (status, sheet_id). Status is one of: exists / created / dry-run /
    declined / refused-auto-number. Raises on API failure — the caller fences.

    ORDERING IS LOAD-BEARING: find/adopt runs FIRST, the AUTO_NUMBER guard second.
    The guard's own MANUAL: remedy is "build it by hand, then re-run — it will adopt
    the sheet and print the constant", which is only reachable if the re-run is
    allowed to LOOK before it refuses. Guarding first made the remedy a dead end (the
    re-run refused again and the hand-built sheet's id was never reported). Adopting
    is a pure GET, so an early find violates nothing; errorCode 1008 lives strictly on
    the CREATE path, which is the only thing the guard now gates.
    """
    existing = _find_sheet_ids(folder_id, sheet_name)
    if existing:
        found = _adopt_first(existing, "sheet", sheet_name, constant)
        print(f"[skip] sheet {sheet_name!r} already present (sheet_id={found}). Adopted "
              f"untouched — no column add, no retype, no description back-fill.")
        return "exists", found

    blocked = _auto_number_titles(columns)
    if blocked:
        print(f"[WARN] auto_number_unsupported: {sheet_name!r} declares AUTO_NUMBER column(s) "
              f"{blocked} — Smartsheet rejects AUTO_NUMBER on the create path (errorCode 1008). "
              f"REFUSING to half-create the sheet.")
        print(f"MANUAL: create the sheet {sheet_name!r} in folder {folder_id} by hand in the "
              f"Smartsheet UI, add the AUTO_NUMBER column(s) {blocked} there, then re-run this "
              f"script — it will adopt the sheet and print {constant} for the flip.")
        return "refused-auto-number", None

    if dry_run:
        print(f"[dry-run] Would create sheet {sheet_name!r} in folder {folder_id} "
              f"({len(columns)} columns): {[c['title'] for c in columns]}")
        return "dry-run", None

    if not gate.allow(f"create sheet {sheet_name!r} in folder {folder_id}"):
        return "declined", None

    new_id = smartsheet_client.create_sheet_in_folder(folder_id, sheet_name, columns)
    _verify_after_create(folder_id, sheet_name, new_id, constant)
    print(f"[ok] created sheet {sheet_name!r} in folder {folder_id} (sheet_id={new_id}, "
          f"{len(columns)} columns).")
    print(f"[bootstrap] Update shared/sheet_ids.py:\n    {constant} = {new_id}")
    return "created", new_id


# ---- ITS_Daemon_Health column-id read-back ------------------------------


def read_daemon_health_column_ids(sheet_id: int) -> dict[str, int | None]:
    """Map each DAEMON_HEALTH_COLUMNS key to the live column id on `sheet_id`.

    Read-only (`smartsheet_client.list_columns_with_options`, a GET). Load-bearing:
    heartbeat writes are COLUMN-ID-KEYED, so a freshly created sheet's twelve new
    ids must reach shared/sheet_ids.py or every heartbeat silently targets the old
    tenant's columns and degrades to daemon_health_write_failed.
    """
    live = smartsheet_client.list_columns_with_options(sheet_id)
    by_title = {str(c["title"]): int(c["id"]) for c in live}
    out: dict[str, int | None] = {}
    for key, title in DAEMON_HEALTH_KEY_TO_TITLE:
        out[key] = by_title.get(title)
    missing = [k for k, v in out.items() if v is None]
    if missing:
        print(f"[WARN] daemon_health_column_missing: could not resolve column id(s) for "
              f"{missing} on sheet {sheet_id} — the paste block below is INCOMPLETE. Do not "
              f"paste a partial DAEMON_HEALTH_COLUMNS dict; re-read the sheet's columns and "
              f"fill the gaps by hand.")
    return out


def _render_daemon_health_block(ids: dict[str, int | None]) -> str:
    """Render the paste-ready DAEMON_HEALTH_COLUMNS dict.

    Formatting matches shared/sheet_ids.py exactly: four-space indent, quoted key,
    colon, and the value RIGHT-ALIGNED so its last digit lands in column 51.
    """
    lines = ["DAEMON_HEALTH_COLUMNS: dict[str, int] = {"]
    for key, _title in DAEMON_HEALTH_KEY_TO_TITLE:
        if key == "total_cycles":
            lines.append(
                '    # `total_cycles` is the lifetime monotonic counter (PR #59.5 ARCH-3).\n'
                '    # The Smartsheet column title is "Total Cycles Today" but the semantics\n'
                '    # were changed to lifetime monotonic to avoid a read-before-write round\n'
                '    # trip per cycle for an informational field. The column-title rename\n'
                '    # is a separate UI-only cleanup; the ID below is stable across that.'
            )
        prefix = f'    "{key}":'
        value = ids.get(key)
        rendered = "<unresolved>" if value is None else str(value)
        width = max(len(rendered) + 1, 51 - len(prefix))
        lines.append(f"{prefix}{rendered:>{width}},")
    lines.append("}")
    return "\n".join(lines)


# ---- FLIP BLOCK ---------------------------------------------------------

# Comment text for each constant, matching shared/sheet_ids.py's existing lines.
_FLIP_COMMENTS: dict[str, str] = {
    "SHEET_CONFIG": "ITS — System / 01 — Config / ITS_Config",
    "SHEET_ERRORS": "ITS — System / 02 — Logs / ITS_Errors",
    "SHEET_QUARANTINE": "ITS — System / 02 — Logs / ITS_Quarantine",
    "SHEET_REVIEW_QUEUE": "ITS — System / 03 — Queues / ITS_Review_Queue",
    "SHEET_DAEMON_HEALTH": "ITS — System / 04 — Daemons / ITS_Daemon_Health",
}


def _print_flip_block(
    sheet_ids_by_constant: dict[str, int | None],
    daemon_health_ids: dict[str, int | None] | None,
) -> None:
    """Emit ready-to-paste shared/sheet_ids.py lines (alignment matches the module).

    An id resolved from an AMBIGUOUS find is NEVER rendered as an integer (F2). The
    run most likely to be WRONG must not print the most confident output: "02 — Logs"
    holds five sheets named ITS_Errors, `_find_sheet_ids` returns them in API order,
    and the first is NOT the id the runtime uses — so a clean `SHEET_ERRORS = <first>`
    is a paste-ready wrong answer. A visibly broken placeholder is strictly better.
    """
    print("\n=== FLIP BLOCK ===")
    print("Paste into shared/sheet_ids.py (replacing the existing lines):\n")
    for _name, _folder, constant, _cols in SHEETS:
        value = sheet_ids_by_constant.get(constant)
        matches = _AMBIGUOUS.get(constant)
        if matches:
            rendered = f"<AMBIGUOUS — {matches} matches, RECONCILE BEFORE FLIPPING>"
        elif value is None:
            rendered = "<unresolved>"
        else:
            rendered = str(value)
        print(f"{constant:<25} = {rendered:<16}  # {_FLIP_COMMENTS[constant]}")
    print()
    if daemon_health_ids is None:
        print("# DAEMON_HEALTH_COLUMNS: <unresolved> — ITS_Daemon_Health was not resolved this "
              "run.\n# Re-run live; heartbeats stay SILENTLY dark until this dict is pasted.")
    else:
        if "SHEET_DAEMON_HEALTH" in _AMBIGUOUS:
            print("# [WARN] The column ids below were read from the FIRST of "
                  f"{_AMBIGUOUS['SHEET_DAEMON_HEALTH']} sheets named "
                  f"{SHEET_DAEMON_HEALTH_NAME!r} — possibly the WRONG sheet. Do not paste "
                  "until the duplicates are reconciled and this is re-run.")
        print(_render_daemon_health_block(daemon_health_ids))

    incomplete = any(v is None for v in sheet_ids_by_constant.values()) or (
        daemon_health_ids is None or any(v is None for v in daemon_health_ids.values())
    )
    if incomplete or _AMBIGUOUS:
        detail = ""
        if _AMBIGUOUS:
            detail = (f" AMBIGUOUS (duplicate-name, id withheld on purpose): "
                      f"{', '.join(sorted(_AMBIGUOUS))}.")
        print("\n[WARN] flip_block_incomplete: one or more ids are not pasteable — "
              "<unresolved> (dry-run, declined confirmation, or a partial run) or "
              f"<AMBIGUOUS> (more than one object shares the canonical name).{detail} "
              "Reconcile the duplicates, re-run live, and paste the complete block — "
              "never flip a placeholder and never guess an ambiguous id.")


def _warn_escalation_labels() -> None:
    """Operator flag: the one non-registry picklist bakes named staff into a dropdown."""
    print(f"[WARN] escalation_level_names_staff: ITS_Review_Queue 'Escalation Level' ships the "
          f"literal options {_ESCALATION_LEVELS} — sandbox-era staff names, with no code writer "
          f"and no registry entry (the reviewer-chain walker is TBD). Confirm the L1/L2/L3 names "
          f"for the production tenant BEFORE this runs, or edit the options out and populate "
          f"them in the UI at chain go-live.")


# ---- main ---------------------------------------------------------------


ALL_FOLDERS: tuple[str, ...] = (FOLDER_CONFIG, FOLDER_LOGS, FOLDER_QUEUES, FOLDER_DAEMONS)

_FOLDER_REMEDY = ("This script NEVER creates folders — run "
                  "scripts/migrations/build_system_workspace.py (D1) first.")


def _resolve_folders(workspace_id: int) -> tuple[dict[str, int | None], set[str]]:
    """Resolve each canonical folder name to an id inside `workspace_id` (invariant 3).

    Returns (resolved, ambiguous_names). A folder is resolved only when EXACTLY ONE
    exact-name match exists — >1 fails closed (F1) rather than adopting a container
    this script is about to create real sheets inside. `ambiguous_names` lets main()
    report the dependent sheets as `blocked-parent` (an operator-reconcilable
    duplicate) rather than the misleading `FAILED` (folder simply absent).
    """
    resolved: dict[str, int | None] = {}
    ambiguous: set[str] = set()
    for folder_name in ALL_FOLDERS:
        ids = _find_folder_ids(workspace_id, folder_name)
        if len(ids) > 1:
            ambiguous.add(folder_name)
        resolved[folder_name] = _resolve_unique_parent(
            ids, "folder", folder_name,
            f"{_FOLDER_REMEDY} (searched workspace {workspace_id}.)",
        )
    return resolved, ambiguous


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the five ITS — System sheets (D3): ITS_Config, ITS_Errors, "
                    "ITS_Quarantine, ITS_Review_Queue, ITS_Daemon_Health."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = parser.parse_args()

    print(f"[info] Workspace = {WORKSPACE_NAME!r} (resolved BY NAME — sheet_ids.py holds the "
          f"SANDBOX ids until the flip)")
    for name, folder, _constant, cols in SHEETS:
        print(f"[info] {name:<18} -> {folder!r} ({len(cols)} columns)")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}")
    print("[info] Create-only: this script issues GET + create-POST only — no update, "
          "no delete, no re-share, on anything. Sheets are created EMPTY (no seed rows).\n")
    _warn_escalation_labels()
    print()

    gate = LiveWriteGate(dry_run=args.dry_run)
    _AMBIGUOUS.clear()

    workspaces = _find_workspaces()
    workspace_ids = [int(ws["id"]) for ws in workspaces]
    workspace_ambiguous = len(workspace_ids) > 1
    workspace_id = _resolve_unique_parent(
        workspace_ids, "workspace", WORKSPACE_NAME,
        "This script NEVER creates the workspace — run "
        "scripts/migrations/build_system_workspace.py (D1) first, then re-run this one.",
    )
    workspace_not_owned = False
    if workspace_id is not None:
        # Exactly one exact-name match — the object is workspaces[0]. OWNERSHIP guard
        # (rule 1, converged with D1/D2): this script CREATES sheets inside the workspace,
        # so it must refuse a workspace it does not OWN (the sandbox-shared-into-production
        # trap). Print the discriminators unconditionally — even the OWNER path should show
        # which plan this run is about to write into — then fail closed on non-OWNER or
        # an absent accessLevel (an anomalous omission — the endpoint populates it in
        # practice, and UNKNOWN ownership on a customer production tenant must fail closed).
        adopted = workspaces[0]
        access = adopted.get("accessLevel")
        permalink = adopted.get("permalink")
        print(f"[info] adopted workspace accessLevel={access} permalink={permalink}")
        if access is None or access != "OWNER":
            reason = ("reported NO accessLevel, so OWNER access could not be confirmed"
                      if access is None else f"has accessLevel={access}, not OWNER")
            print(f"[WARN] adopted_workspace_not_owned: the workspace named "
                  f"{WORKSPACE_NAME!r} (id={workspace_id}) {reason} — very likely the "
                  f"SANDBOX workspace shared into this identity, and building the five "
                  f"sheets inside it would land them on the WRONG PLAN. "
                  f"permalink={permalink}\n"
                  "       REFUSING to create anything. Open the permalink and verify this "
                  "is the production plan; if the API genuinely omitted accessLevel, "
                  "escalate rather than override. Re-run from an identity that OWNS the "
                  "production workspace.")
            workspace_not_owned = True
            workspace_id = None
        else:
            print(f"[ok] workspace {WORKSPACE_NAME!r} resolved and OWNED "
                  f"(workspace_id={workspace_id}).")

    if workspace_id is not None:
        folders, ambiguous_folders = _resolve_folders(workspace_id)
    else:
        folders = dict.fromkeys(ALL_FOLDERS)
        # An ambiguous or not-owned WORKSPACE blocks every folder beneath it: we never
        # resolved inside it, so "folder absent" would be a lie. The per-sheet loop below
        # reads workspace_ambiguous / workspace_not_owned to render the right reason.
        ambiguous_folders = set()

    results: dict[str, tuple[str, int | None]] = {}
    for sheet_name, folder_name, constant, columns in SHEETS:
        print()
        folder_id = folders.get(folder_name)
        if folder_id is None:
            if workspace_ambiguous:
                print(f"[WARN] {sheet_name}: blocked-parent — the target workspace is "
                      f"DUPLICATE-AMBIGUOUS, so nothing is created inside it. Reconcile the "
                      f"duplicate workspaces and re-run.")
                results[sheet_name] = ("blocked-parent", None)
            elif workspace_not_owned:
                print(f"[WARN] {sheet_name}: blocked-parent — the target workspace is NOT "
                      f"OWNED by this identity, so nothing is created inside it. Re-run from "
                      f"an identity that OWNS the production workspace.")
                results[sheet_name] = ("blocked-parent", None)
            elif folder_name in ambiguous_folders:
                print(f"[WARN] {sheet_name}: blocked-parent — the target folder is "
                      f"DUPLICATE-AMBIGUOUS, so nothing is created inside it. Reconcile the "
                      f"duplicate folders and re-run.")
                results[sheet_name] = ("blocked-parent", None)
            elif workspace_id is None:
                print(f"[WARN] {sheet_name}: FAILED — workspace {WORKSPACE_NAME!r} is "
                      f"unresolved (run build_system_workspace.py first).")
                results[sheet_name] = ("FAILED", None)
            else:
                print(f"[WARN] {sheet_name}: FAILED — target folder {folder_name!r} is "
                      f"unresolved.")
                results[sheet_name] = ("FAILED", None)
            continue
        try:
            # Per-sheet fence (all five are independent; one failure must not abort
            # the rest). Broad by design: the point is that a Smartsheet error on
            # ITS_Errors still lets ITS_Review_Queue get built.
            results[sheet_name] = build_one_sheet(
                sheet_name, folder_id, constant, columns, gate, dry_run=args.dry_run
            )
        except Exception as exc:  # noqa: BLE001 - per-sheet fence, reported below
            print(f"[WARN] {sheet_name}: FAILED — {exc!r}")
            results[sheet_name] = ("FAILED", None)

    # ITS_Daemon_Health column-id read-back (a GET; safe on both create and adopt —
    # an adopted sheet's ids are just as necessary for the flip as a new one's).
    daemon_health_ids: dict[str, int | None] | None = None
    dh_status, dh_id = results.get(SHEET_DAEMON_HEALTH_NAME, ("FAILED", None))
    if dh_id is not None and dh_status in {"created", "exists"}:
        print()
        try:
            daemon_health_ids = read_daemon_health_column_ids(dh_id)
            print(f"[ok] read back {SHEET_DAEMON_HEALTH_NAME} column ids from sheet {dh_id}.")
        except Exception as exc:  # noqa: BLE001 - read-back must not fail the run
            print(f"[WARN] daemon_health_readback_failed: {exc!r}. The sheet is fine, but "
                  f"heartbeats stay SILENTLY dark (broad-except-isolated → "
                  f"daemon_health_write_failed) until DAEMON_HEALTH_COLUMNS is repopulated.")

    print("\nSummary:")
    unresolved: list[tuple[str, str]] = []
    for sheet_name, folder_name, constant, _cols in SHEETS:
        status, sheet_id = results.get(sheet_name, ("FAILED", None))
        if status in NON_TERMINAL_STATUSES:
            unresolved.append((sheet_name, status))
        print(f"  {sheet_name + ':':<19} {status:<21} id={sheet_id}  "
              f"({folder_name} → {constant})")

    _print_flip_block(
        {constant: results.get(name, ("", None))[1] for name, _f, constant, _c in SHEETS},
        daemon_health_ids,
    )

    # Report order matters: a run can BOTH have unresolved sheets AND end in a decline
    # (e.g. a blocked-parent folder plus an operator N). The unresolved set is the one
    # that changes the exit code, so it is reported FIRST — a checklist gating on exit
    # status must not read "operator declined" and conclude the run merely no-opped.
    if unresolved:
        detail = ", ".join(f"{n} ({s})" for n, s in unresolved)
        print(f"\n{len(unresolved)} sheet(s) NOT created: {detail}. Exiting NONZERO — do not "
              f"advance the cutover checklist. Re-run after resolving; this script is "
              f"idempotent and will adopt what already exists and create only the remainder.")
        if gate.declined:
            print("(The confirmation prompt was also declined this run.)")
    elif gate.declined:
        print("\nDeclined at the confirmation prompt — nothing was created. Re-run to proceed.")
    else:
        print("\nNext: flip the five sheet ids AND paste DAEMON_HEALTH_COLUMNS into "
              "shared/sheet_ids.py, restart the daemons, then run the seeders "
              "(FLIP precedes SEED).")
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
