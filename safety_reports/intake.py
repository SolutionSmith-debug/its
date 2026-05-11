"""Safety Reports intake — fires per inbound email to the dedicated safety mailbox.

BLOCKED on 9 owner decisions (see planning project's Safety Reports Mission v3). Skeleton
only.
"""
from __future__ import annotations

from shared.error_log import its_error_log
from shared.kill_switch import require_active


@its_error_log("safety_reports.intake")
@require_active
def main(email_path: str) -> None:
    """Process one inbound safety report email.

    Args:
        email_path: Filesystem path to the email file dropped by the Mail.app hot-folder rule.

    TODO:
    - Parse email body + attachments.
    - Extract structured fields via Anthropic API + safety_extract.json schema.
    - Look up job in master jobs Smartsheet.
    - High confidence: upload to Box canonical path; write tracking row.
    - Low confidence: write to ITS_Review_Queue.
    """
    raise NotImplementedError("Awaiting owner decisions on safety inbox, schema, paths.")


if __name__ == "__main__":
    import sys
    main(sys.argv[1])
