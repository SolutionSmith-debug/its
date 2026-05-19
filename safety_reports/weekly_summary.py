"""Safety Reports weekly summary — launchd-scheduled. BLOCKED on owner decisions.

Pre-cascade scaffold. Per Foundation Mission v6 Invariant 1 (External Send Gate, permanent),
this file is superseded by the two-process refactor: `weekly_generate.py` (AI step, no send
capability) + `weekly_send.py` (transmission, no AI step). The file stays in-tree as a
reference for the launchd plist wiring until that refactor lands.
"""
from __future__ import annotations

from shared.error_log import its_error_log
from shared.kill_switch import require_active


@its_error_log("safety_reports.weekly_summary")
@require_active
def main() -> None:
    """Generate per-job WPR drafts for the past week; queue for review.

    TODO: implement once safety_reports/intake.py is producing tracking rows and the
    canonical Master WPR template is identified.
    """
    raise NotImplementedError("Awaiting owner decisions on WPR template + cadence.")


if __name__ == "__main__":
    main()
