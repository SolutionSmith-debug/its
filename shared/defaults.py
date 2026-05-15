"""Default values used by shared/scheduling.py.

Identity references (reviewer emails) live here, not in scheduling.py. At runtime ITS_Config
takes precedence — these defaults are the fallback used before the sandbox config sheet is
provisioned, and the bootstrap data for first-run seeding.

Why the split: keeping defaults in a separate module satisfies the "no hardcoded emails in
shared/scheduling.py" constraint and gives planning-layer humans a single file to update when
the chain composition changes for a new customer tenant.
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


DEFAULT_REVIEWER_CHAINS: dict[str, ReviewerChainConfig] = {
    "safety_reports": {
        "primary": "tealap@evergreenmirror.com",
        "secondary": "samr@evergreenmirror.com",
        "tertiary": "jacobs@evergreenmirror.com",
        "delay_to_secondary_hours": 4,
        "delay_to_tertiary_hours": 18,
    },
}
