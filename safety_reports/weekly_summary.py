"""DEPRECATED — superseded by safety_reports/weekly_generate.py + weekly_send.py.

Per Foundation Mission v8 Invariant 1 (External Send Gate, permanent), the
weekly cycle splits into two processes:

  - `safety_reports.weekly_generate` — drafts WPRs via Anthropic, writes to
    WPR_Pending_Review with `Approved for Send=False`. Zero send capability.
  - `safety_reports.weekly_send` — reads approved rows, transmits via Graph.
    Zero AI capability. (R3 Session 3, not yet created.)

This file stays in-tree for one cycle so any orphan launchd reference to
the old `org.solutionsmith.its.safety-weekly-summary` plist surfaces as an
explicit NotImplementedError rather than a silent crash. Delete in a
follow-on cleanup PR once `org.solutionsmith.its.weekly-generate` plist is
loaded on the production MacBook and no orphan plist remains.
"""
from __future__ import annotations

from shared.error_log import its_error_log
from shared.kill_switch import require_active


@its_error_log("safety_reports.weekly_summary")
@require_active
def main() -> None:
    raise NotImplementedError(
        "weekly_summary is DEPRECATED — use safety_reports.weekly_generate "
        "for the draft step and safety_reports.weekly_send (R3 Session 3, "
        "pending) for the send step. See Foundation Mission v8 Invariant 1."
    )


if __name__ == "__main__":
    main()
