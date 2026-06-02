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
# Records (ITS_Errors + Sentry) are never capped — only the Resend fan-out is
# (Op Stds v16 §3.1 push-vs-record separation).
ALERTING_MAX_ALERTS_PER_HOUR = 15

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
