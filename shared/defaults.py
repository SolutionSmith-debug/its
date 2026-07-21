"""Default values used by shared/scheduling.py.

Identity references (reviewer emails) live here, not in scheduling.py. At runtime ITS_Config
takes precedence — these defaults are the fallback used before the sandbox config sheet is
provisioned, and the bootstrap data for first-run seeding.

Why the split: keeping defaults in a separate module satisfies the "no hardcoded emails in
shared/scheduling.py" constraint and gives planning-layer humans a single file to update when
the chain composition changes for this customer.
"""
from __future__ import annotations

from typing import TypedDict


class ReviewerChainConfig(TypedDict):
    """Shape of a reviewer-chain entry in ITS_Config.

    The chain has three positional slots — primary, secondary, tertiary — each an email.
    The two `delay_to_*_hours` integers are the hours after the item lands in the queue
    before the next slot is paged. Offsets are positional, so when PTO removes the primary,
    the secondary takes the 0-hour slot.
    """
    primary: str
    secondary: str
    tertiary: str
    delay_to_secondary_hours: int
    delay_to_tertiary_hours: int


# Alerting — defaults for shared/alert_dedupe.py. Window value is read at
# runtime via smartsheet_client.get_setting("alerting.dedupe_window_minutes",
# workstream="global"); this constant is the fallback used when the row is
# missing or the read fails. ITS_Config takes precedence whenever readable.
ALERTING_DEDUPE_WINDOW_MINUTES = 60

# Alerts-per-hour cap (F09) — global ceiling on operator Resend emails across
# all dedupe keys, so a flapping failure with many distinct keys cannot fire
# unbounded email. Read at runtime via
# smartsheet_client.get_setting("alerting.max_alerts_per_hour", workstream="global");
# this constant is the fallback when the row is missing or the read fails.
# The record (ITS_Errors) is never capped — only the Resend fan-out is
# (Op Stds §3.1 push-vs-record separation, as amended 2026-07-03: Sentry is
# a deduped push leg gated per-key by should_fire, not by this hourly cap).
ALERTING_MAX_ALERTS_PER_HOUR = 15

# Operator alert recipient — build-time FALLBACK for shared/resend_client.send_alert
# when system.operator_email cannot be read from ITS_Config (e.g. the Smartsheet
# circuit breaker is OPEN during the very outage the prolonged-open CRITICAL page
# must reach the operator about — the ITS_Config read short-circuits). ITS_Config's
# system.operator_email takes precedence whenever readable; this is the last-resort
# recipient so an out-of-band page still delivers during a total Smartsheet outage
# (Resend is HTTP, unaffected). Per-customer-repo invariant: replace at fork time.
OPERATOR_EMAIL_FALLBACK = "seths@evergreenmirror.com"

# Circuit breaker (F08) — fallbacks for shared/circuit_breaker.py's Smartsheet
# breaker. Each is operator-tunable via an ITS_Config row (workstream="global")
# read under circuit_breaker.bypass(); these constants are the fallback used
# when the row is missing or unreadable. On an unreadable config the breaker
# falls back to ENABLED (safe — a degraded Smartsheet still trips), per the
# D4 escape-hatch design.
CIRCUIT_BREAKER_ENABLED                      = True   # circuit_breaker.enabled
CIRCUIT_BREAKER_FAILURE_THRESHOLD            = 5      # circuit_breaker.failure_threshold
CIRCUIT_BREAKER_COOLDOWN_SECONDS             = 300    # circuit_breaker.cooldown_seconds
CIRCUIT_BREAKER_PROLONGED_OPEN_ALERT_SECONDS = 600    # circuit_breaker.prolonged_open_alert_seconds (PR-2 watchdog)

# Bounded transient retry — fallbacks for shared/smartsheet_client.py's reads-only
# `_transient_retry`. Covers the two gaps the Smartsheet SDK does NOT retry: an HTTP
# 500 carrying errorCode 4000 (absent from the SDK's should_retry lookup) and any
# requests-level ReadTimeout/ConnectionError (raised before the SDK's retry loop runs).
# Operator-tunable via ITS_Config rows (workstream="global"); `enabled=false` is a pure
# pass-through escape hatch. Two extra attempts over ~7 s stays well inside every
# daemon's launchd cadence (the shortest enrolled cadence is 60 s).
SMARTSHEET_RETRY_ENABLED             = True         # smartsheet.retry.enabled
SMARTSHEET_RETRY_MAX_EXTRA_ATTEMPTS  = 2            # smartsheet.retry.max_extra_attempts
SMARTSHEET_RETRY_BACKOFF_SECONDS     = (2.0, 5.0)   # smartsheet.retry.backoff_seconds

# Picklist sync — size guardrails for shared/picklist_sync.py. Two-stage:
# WARN at >200 options, HARD-HALT-that-mapping at >400. Both values are
# operator-tunable via ITS_Config rows picklist_sync.size_warn_threshold
# and picklist_sync.size_hard_halt_threshold (workstream=global). The
# validation helper _resolve_size_thresholds() falls back to these
# defaults on any read failure or invalid (warn>=halt, non-int,
# negative, >1000) configured value.
PICKLIST_SIZE_WARN_THRESHOLD       = 200
PICKLIST_SIZE_HARD_HALT_THRESHOLD  = 400
PICKLIST_SIZE_THRESHOLD_MAX        = 1000  # sanity ceiling on configured values

# Smartsheet sheet-count guard (A1 / forensic scaling eval B1) — per-workspace
# ceiling + margin for shared/sheet_capacity.check_create_headroom, which gates
# find-or-create so a new week/period sheet never silently lands PAST the plan's
# sheet cap (it routes to the Review Queue instead). Operator-tunable via ITS_Config
# rows smartsheet.sheet_count_ceiling / smartsheet.sheet_count_margin (workstream=
# "global"); these are the fallback when the row is missing or unreadable. The REAL
# per-plan/per-workspace cap is NOT exposed by the Smartsheet API — set the ceiling
# once confirmed with Smartsheet plan docs/support (scripts/verify_sheet_cap.py +
# operator follow-up). Conservative defaults: signal well before any plausible cap.
# Sheets stay WEEKLY (monthly reverted 2026-06-29); Evergreen is Business/Enterprise
# (operator-confirmed 2026-06-29) so capacity is non-limiting — this is a runaway
# tripwire, not a cost gate.
SHEET_COUNT_CEILING = 1500
SHEET_COUNT_MARGIN  = 50

# Smartsheet ROW-cap rotation (growth Slice 1 / eval A5 / watchdog Check O) —
# thresholds for scripts/watchdog.py `_check_row_cap_rotation`, which keeps
# ITS_Errors + ITS_Review_Queue from ever hitting the Smartsheet per-sheet row
# cap (verified 20,000 rows at current plan/width — NOT the eval's 5,000
# assumption; the A5 spec text lives on unmerged branch c0cbf3b and is
# corrected here). Past the cap, add_rows fails → the forensic record is lost
# and watchdog Check B goes blind. WARN when a sheet crosses
# SHEET_ROW_WARN_THRESHOLD; at SHEET_ROW_ROTATE_THRESHOLD delete TERMINAL rows
# older than SHEET_ROW_ROTATION_RETENTION_DAYS, oldest first, in delete_rows
# batches of SHEET_ROW_ROTATION_DELETE_BATCH,
# bounded to SHEET_ROW_ROTATION_MAX_BATCHES_PER_RUN per daily run (the next
# run re-counts and continues — no retry loop inside one check execution).
SHEET_ROW_HARD_CAP                     = 20_000  # verified Smartsheet limit (row-bound at these widths)
SHEET_ROW_WARN_THRESHOLD               = 15_000
SHEET_ROW_ROTATE_THRESHOLD             = 16_000
SHEET_ROW_ROTATION_RETENTION_DAYS      = 90
# 200, NOT 450: the original 450 claimed to be "the Smartsheet per-call ID
# cap" but FAILED live with HTTP 400 (Bad Request) the first time a rotation
# actually deleted (2026-07-13 cap-incident drain) — the smartsheet SDK
# passes row IDs in the URL query string, and 450 sixteen-digit IDs exceed
# the URL length limit. 200 is live-verified working (13,815 rows drained
# clean, zero 400s, same day). Mocks-pass-live-fails class: Check O had
# never deleted before (nothing was ever age-eligible), so the latent bug
# was never exercised.
SHEET_ROW_ROTATION_DELETE_BATCH        = 200
# 23 × 200 = 4,600 rows/run ≈ the original 10 × 450 = 4,500 per-run budget.
SHEET_ROW_ROTATION_MAX_BATCHES_PER_RUN = 23

# Storm-mode floor (2026-07-13 ITS_Errors cap incident): with the system only
# ~8 weeks old, the 90d retention exceeded the sheet's ENTIRE age — nothing
# could ever be age-eligible, so rotation was structurally dead while a
# config-WARN storm (~1,400–4,500 rows/day from daemons WARNing per-cycle on
# 5 missing ITS_Config rows) filled ITS_Errors to the 20,000 hard cap and
# Check O fired CRITICAL "nothing deletable" two days running. When the 90d
# pass yields ZERO eligible rows on an over-the-rotate-mark sheet,
# _rotate_one_sheet re-selects with this floor instead (terminal rows older
# than 2 days — 48h at date granularity; _row_age_date is date-only), so
# rotation can never again be pinned by a retention window longer than the
# system's life. Same invariants: open CRITICALs / un-drained queue rows /
# unprovable dates are NEVER deleted, at any floor.
SHEET_ROW_STORM_FLOOR_DAYS             = 2

# Weekly-packet size early warning (growth Slice 4b / eval row 7). Graph's
# upload-session hard ceiling is 150 MB (graph_client.UPLOAD_SESSION_MAX_BYTES)
# — past it weekly_send HELDs the row (`held_oversized_packet`), an operator-
# actionable refusal discovered only at Friday send time. This threshold makes
# the wall a FORECAST: a compiled packet above it (but still sendable) WARNs
# via an ITS_Errors record pointing at the manual packet-split runbook
# (docs/runbooks/safety_weekly_send.md), while the send proceeds unchanged.
PACKET_SIZE_WARN_BYTES = 100 * 1024 * 1024  # 104,857,600


DEFAULT_REVIEWER_CHAINS: dict[str, ReviewerChainConfig] = {
    "safety_reports": {
        "primary": "tealap@evergreenmirror.com",
        "secondary": "samr@evergreenmirror.com",
        "tertiary": "jacobs@evergreenmirror.com",
        "delay_to_secondary_hours": 4,
        "delay_to_tertiary_hours": 18,
    },
}


FOREFRONT_CUSTOMER_NAME = "Forefront"

# Box project folders under ITS DATA root (id 382010286207).
# Active-side schema follows the "1111B (Copy for new projects)" canonical
# template (folder 383696567483, materialized PR #70 + verified 267
# descendants). Values updated 2026-05-23 via
# scripts/migrations/reclone_projects_from_1111b.py post-cutover — each
# entry is the Box folder ID of a project-specific clone of 1111B under
# ITS DATA. The legacy 1111A-derived clones are archived under
# "ITS DATA / 99. Legacy 1111A Clones / <Project> (legacy 1111A)" for
# audit reference; per Op Stds v11 §14 they stay archived (not deleted)
# for ≥30 days.
# Per-customer-repo invariant: replace at fork time.
BOX_PROJECT_FOLDERS: dict[str, str] = {
    "Bradley 1": "383795291728",
    "Bradley 2": "383795215056",
    "Brimfield 1": "383796013268",
    "Brimfield 2": "383792793376",
    "Huntley": "383796738311",
    "Rockford": "383794509507",
}
