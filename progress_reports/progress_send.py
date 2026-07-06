"""Progress Reports weekly send — the PROGRESS instantiation of the shared send engine.

Purpose
-------
The progress twin of ``safety_reports.weekly_send``: the SAME dispatch logic
(``safety_reports.weekly_send.send_one_row``), a different ``SendConfig``. This module is
the thin PROGRESS binding (P5 — parameterize-not-clone, Op Stds §14); it transmits one
human-approved ``WPR_human_review`` row via Microsoft Graph. Invoked per row by
``progress_reports/progress_send_poll.py`` (the launchd poller), which runs the F22
approval-attestation gate against the **Progress Reporting** workspace, then calls the
bound sender. It is the send half of the External Send Gate two-process model (Foundation
Mission v11 Invariant 1).

Invariants (§42 — why a binding, not a clone)
---------------------------------------------
- **Invariant 1 (External Send Gate):** zero AI capability — ``anthropic_client`` /
  ``anthropic`` AST-forbidden via ``tests/test_capability_gating.py::SEND_SCRIPTS``. This
  module has no Graph-send call of its own; it delegates to the one transmitter
  (``weekly_send.send_one_row``).
- **No cross-workstream mix-up:** every workstream-specific value lives in the required,
  no-default ``SendConfig``. ``workstream_tag="progress"`` is the contamination-guard
  expected value (a ``WPR_human_review`` row whose ``Workstream`` cell is not ``progress``
  is HARD-HELD before any send). ``active_jobs_config=PROGRESS_ACTIVE_JOBS_CONFIG`` is the
  **critical cross-wiring guard** — recipients resolve ONLY from the progress workspace's
  own ``ITS_Active_Jobs_Progress`` sheet, never ``ITS_Active_Jobs``. Omitting it (or passing
  the safety config) would silently route progress reports to the SAFETY contact column —
  no runtime error, just a different column in a different sheet (see
  ``docs/runbooks/progress_send.md`` Symptom B + the P4-Slice-1 forward-note in tech_debt).
- **Inherited unchanged from the shared engine (§42 there):** the SENT/HELD idempotency
  gates, the write-ahead ``SENDING`` marker (no double-send), the oversized-packet HELD,
  the inline-vs-upload-session transport switch, the Notes-encoded retry state, and the
  error fences.

Failure modes
-------------
``send_one_row`` returns a typed ``SendResult`` and HELDs (never transmits a half-formed
packet) on: unknown job / empty-or-invalid TO (``held_no_recipient``), missing compiled
PDF (``held_missing_pdf``), an over-ceiling packet (``held_oversized_packet``), or a
wrong-``Workstream`` row (``held_workstream_mismatch`` + CRITICAL). The recipient resolver
is TO = the job's progress reports contact (the workstream-neutral ``reports_contact_email``
alias) with a **stakeholder fallback** when the contact is blank (CC = CC 1–5) — the ONE
recipient-policy difference from safety; the fallback is logged INFO (never silent). Full
successor-remediation fault tree: ``docs/runbooks/progress_send.md`` (Op Stds §43).

Consumers
---------
- launchd daemon ``org.solutionsmith.its.progress-send`` via ``progress_send_poll`` (the
  Friday-onward dispatcher of approved WPR rows).
- ``main()`` / the CLI — operator manual rerun of one approved row (debugging).
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any, cast

from progress_reports import wpr_review
from safety_reports import weekly_send
from safety_reports.weekly_send import SendConfig, SendResult, _ReviewModule
from shared import active_jobs, error_log
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active
from shared.required_config import ConfigKey, resolve_and_log

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
    empty/invalid (e.g. both columns blank).

    **Never-silent:** when the fallback fires (contact blank, stakeholder present) the send
    still succeeds — to a DIFFERENT person than the named contact — so it is logged at INFO
    with a dedicated ``error_code`` rather than redirected silently. A blanked contact column
    (fat-fingered edit / partial migration) is then observable before someone notices the
    report reached the wrong inbox. See ``docs/runbooks/progress_send.md`` Symptom B."""
    to_addr = (job.reports_contact_email or "").strip()
    if not to_addr:
        to_addr = (job.stakeholder_email or "").strip()
        if to_addr:
            error_log.log(
                Severity.INFO,
                SCRIPT_NAME,
                f"progress reports-contact blank for job "
                f"{getattr(job, 'job_id', '?')!r} ({getattr(job, 'project_name', '?')!r}); "
                f"falling back to the stakeholder address for the weekly progress send. If "
                f"unintended, set 'Progress Reports Contact Email' on the "
                f"ITS_Active_Jobs_Progress row.",
                error_code="progress_send.stakeholder_fallback_used",
            )
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

# #336 — the ONE ITS_Config key send_one_row resolves at RUNTIME: the from_mailbox, read under
# CONFIG.config_workstream ('progress_reports'). Declared for the startup observability pass.
REQUIRED_CONFIG: list[ConfigKey] = [
    ConfigKey(CONFIG.from_mailbox_cfg_key, CONFIG.config_workstream, CONFIG.from_mailbox_default, "str"),
]


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
    # #336 startup observability (after @require_active, fail-open). Additive (§14).
    resolve_and_log(SCRIPT_NAME, REQUIRED_CONFIG)

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
