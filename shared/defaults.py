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
