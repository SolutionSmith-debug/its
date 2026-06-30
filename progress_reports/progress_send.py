"""Progress Reports weekly send — the PROGRESS instantiation of the shared send engine.

The progress twin of ``safety_reports.weekly_send``: the SAME dispatch logic
(``safety_reports.weekly_send.send_one_row``), a different ``SendConfig``. This module
is the thin PROGRESS binding (P5 — parameterize-not-clone, Op Stds §14); it transmits
one human-approved ``WPR_human_review`` row via Microsoft Graph. Invoked per row by
``progress_reports/progress_send_poll.py`` (the launchd poller), which runs the F22
approval-attestation gate against the **Progress Reporting** workspace, then calls the
bound sender.

Send half of the External Send Gate two-process model (Foundation Mission v11
Invariant 1). **Zero AI capability** — ``anthropic_client`` / ``anthropic`` AST-forbidden
via ``tests/test_capability_gating.py::SEND_SCRIPTS``.

Why this is a binding, not a clone (§42)
----------------------------------------
Every workstream-specific value lives in the required, no-default ``SendConfig``; this
module supplies only the values that genuinely differ from safety:

- ``workstream_tag="progress"`` — the cross-workstream contamination guard's expected
  value. A ``WPR_human_review`` row whose ``Workstream`` cell is not ``progress`` is
  HARD-HELD before any send (defense-in-depth on the two-process boundary).
- ``active_jobs_config=PROGRESS_ACTIVE_JOBS_CONFIG`` — **the critical cross-wiring guard.**
  Recipients resolve ONLY from the progress workspace's own ``ITS_Active_Jobs_Progress``
  sheet, never ``ITS_Active_Jobs``. Omitting this (or passing the safety config) would
  silently route progress reports to the SAFETY contact column — there is no runtime
  error, the alias just resolves a different column in a different sheet. See
  ``docs/runbooks/progress_send.md`` and the P4-Slice-1 forward-note in tech_debt.
- ``recipient_resolver=_resolve_progress_recipients`` — TO = the job's **progress**
  reports contact (the workstream-neutral ``reports_contact_email`` alias), with a
  **stakeholder fallback** when the contact is blank; CC = the job's CC 1–5 (already
  flattened / de-duped / malformed-WARNed by ``active_jobs``). This stakeholder fallback
  is the ONE recipient-policy difference from safety (safety never falls back — its
  stakeholder is deliberately off the envelope).

Everything else — the SENT/HELD idempotency gates, the write-ahead ``SENDING`` marker
(no double-send), the oversized-packet HELD, the inline-vs-upload-session transport
switch, the Notes-encoded retry state, and the error fences — is inherited unchanged
from ``weekly_send.send_one_row`` (§42 there).
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any, cast

from progress_reports import wpr_review
from safety_reports import weekly_send
from safety_reports.weekly_send import SendConfig, SendResult, _ReviewModule
from shared import active_jobs
from shared.error_log import its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "progress_reports.progress_send"
WORKSTREAM = "progress_reports"

CFG_FROM_MAILBOX = "progress_reports.progress_send.from_mailbox"
DEFAULT_FROM_MAILBOX = "progress@evergreenmirror.com"


def _resolve_progress_recipients(job: Any) -> tuple[str, Sequence[str]]:
    """Progress recipient binding: TO = the job's progress reports contact (the
    workstream-neutral ``reports_contact_email`` alias, populated from
    ``ITS_Active_Jobs_Progress``'s "Progress Reports Contact Email" column), with a
    **stakeholder fallback** when that contact is blank; CC = its CC 1–5 (already
    flattened / de-duped / validated by active_jobs).

    The stakeholder fallback is the deliberate progress-vs-safety difference: a progress
    report still has a meaningful default recipient (the job's stakeholder) when the
    dedicated contact column is empty, whereas safety keeps the stakeholder off the
    envelope. ``send_one_row`` re-validates the resolved TO and HELDs if it is still
    empty/invalid (e.g. both columns blank)."""
    to_addr = (job.reports_contact_email or "").strip()
    if not to_addr:
        to_addr = (job.stakeholder_email or "").strip()
    return to_addr, job.cc_emails


CONFIG = SendConfig(
    script_name=SCRIPT_NAME,
    workstream_tag="progress",
    config_workstream=WORKSTREAM,
    # cast: a module doesn't structurally match a Protocol in mypy, but wpr_review DOES
    # satisfy _ReviewModule's surface (it re-exports the WSR/WPR shared schema; locked by
    # the live tests + the structural contract — same pattern as safety's wsr_review cast).
    review=cast(_ReviewModule, wpr_review),
    recipient_resolver=_resolve_progress_recipients,
    active_jobs_config=active_jobs.PROGRESS_ACTIVE_JOBS_CONFIG,
    report_label="Weekly Progress Report",
    from_mailbox_cfg_key=CFG_FROM_MAILBOX,
    from_mailbox_default=DEFAULT_FROM_MAILBOX,
    max_send_retries=weekly_send.MAX_SEND_RETRIES,
    upload_session_threshold_bytes=weekly_send.UPLOAD_SESSION_THRESHOLD_BYTES,
)


def send_one_row(row_id: int) -> SendResult:
    """Send (or HELD / FAIL) one approved WPR row via the progress ``CONFIG``.

    Thin wrapper over ``weekly_send.send_one_row`` — the binding is the value, the
    dispatch logic is shared. The poller dispatches through this entry."""
    return weekly_send.send_one_row(row_id, CONFIG)


# ---- main + CLI ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(row_id_override: int | None = None) -> dict[str, Any]:
    """Manual rerun of one approved WPR row via CLI (operator debugging)."""
    if row_id_override is None:
        raise SystemExit("usage: python -m progress_reports.progress_send <row_id>")
    result = send_one_row(row_id_override)
    return {
        "row_id": result.row_id, "status": result.status,
        "project_name": result.project_name, "error": result.error,
        "retry_count": result.retry_count,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="progress_reports.progress_send",
        description="Manually send (or HELD) one approved WPR_human_review row. Production sends fire via progress_send_poll.",
    )
    parser.add_argument("row_id", type=int, help="WPR_human_review row ID.")
    args = parser.parse_args(argv)
    main(row_id_override=args.row_id)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
