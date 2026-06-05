"""Safety Reports weekly send — transmit one approved WSR_human_review row via Graph.

Send half of the External Send Gate two-process model (Foundation Mission v11
Invariant 1) for the Safety Portal pull flow. Invoked per row by
`safety_reports/weekly_send_poll.py` (the launchd poller), which discovers WSR rows
with `Approve for Scheduled Send` (scheduled) OR `Send Now` (immediate) checked,
runs the F22 approval-attestation gate, then calls `send_one_row(row_id)`.

Phase-5 rewrite (2026-06-05): repointed WPR_Pending_Review → WSR_human_review.

**Zero AI capability** — `anthropic_client` / `anthropic` AST-forbidden via
`tests/test_capability_gating.py::SEND_SCRIPTS`.

send_one_row pipeline
---------------------
  1. Fetch the WSR row (404 → row_not_found; not an error).
  2. State gate: Send Status SENT → skip (idempotent watermark); HELD → skip
     (operator hold). Approval is verified by the poller (F22) before dispatch.
  3. RECIPIENTS RESOLVED AT SEND TIME from `ITS_Active_Jobs` via the row's Job ID
     (NOT the WSR display columns): TO = the job's safety-reports contact; CC = the
     job's non-empty CC 1–5 (already flattened + de-duped + validated by active_jobs).
     The stakeholder is NOT on the envelope. Empty/unknown job or empty TO → **HELD**
     (refuse; never send a half-formed packet) — operator-actionable, no auto-retry.
  4. Attach the compiled packet: download the Compiled-PDF Box file. Missing link →
     **HELD** (recompile needed); a transient Box download failure → FAILED (retry).
  5. Body = the WSR `Email Body` (the human's edits are the source of truth).
     Subject `Weekly Safety Report — <project> — week of <Week Of>`.
  6. Send via Graph (TO + CC + the PDF attachment). Log the resolved TO+CC.
     GraphAuthError → CRITICAL + FAILED; GraphError → FAILED + retry (Notes-encoded);
     retry-exhaust → CRITICAL.
  7. SENT + Sent At + Notes(sent ts).

Schema-degradation (Notes-encoded retry state)
----------------------------------------------
WSR has no `Send Retry Count` / `Last Send Error` columns, so both are tag-encoded
in `Notes` (`[SEND_RETRY_COUNT: N]`, `[LAST_SEND_ERROR: …]`) — parse-on-read,
replace-or-append-on-write — exactly as the retired WPR path did (Op Stds §19).

Send Status: PENDING | SENT | FAILED | HELD (the WSR picklist). HELD is an
operator-actionable refusal (the poller's filter excludes HELD from re-dispatch);
FAILED auto-retries until MAX_SEND_RETRIES.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from safety_reports import wsr_review
from shared import active_jobs, box_client, error_log, graph_client, smartsheet_client
from shared.error_log import Severity, its_error_log
from shared.graph_client import GraphAuthError, GraphError
from shared.kill_switch import require_active
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

SCRIPT_NAME = "safety_reports.weekly_send"
WORKSTREAM = "safety_reports"

CFG_FROM_MAILBOX = "safety_reports.weekly_send.from_mailbox"
DEFAULT_FROM_MAILBOX = "safety@evergreenmirror.com"

MAX_SEND_RETRIES = 3

_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LAST_ERROR_TAG_RE = re.compile(r"\[LAST_SEND_ERROR: [^\]]*\]")
_RETRY_COUNT_TAG_RE = re.compile(r"\[SEND_RETRY_COUNT: (\d+)\]")
_BOX_FILE_LINK_RE = re.compile(r"/file/(\d+)")

# Send Status picklist values (WSR; mirror wsr_review).
STATUS_PENDING = wsr_review.STATUS_PENDING
STATUS_SENT = wsr_review.STATUS_SENT
STATUS_FAILED = wsr_review.STATUS_FAILED
STATUS_HELD = wsr_review.STATUS_HELD

SHEET = wsr_review.SHEET_ID


SendStatus = Literal[
    "sent",
    "skipped_already_sent",
    "skipped_held",
    "held_no_recipient",
    "held_missing_pdf",
    "row_not_found",
    "send_failed",
    "invalid_recipients",
]


@dataclass(frozen=True)
class SendResult:
    """Outcome of one `send_one_row` call. Returned to the poller for logging."""
    status: SendStatus
    row_id: int
    project_name: str | None = None
    error: str | None = None
    retry_count: int = 0


# ---- Config reader -------------------------------------------------------


def _read_str_setting(key: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except SmartsheetNotFoundError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


# ---- Notes-encoded retry state (graceful-degrade) ------------------------


def _parse_retry_count(notes: str | None) -> int:
    if not notes:
        return 0
    match = _RETRY_COUNT_TAG_RE.search(notes)
    if match is None:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _update_notes_tags(
    notes: str | None,
    *,
    new_retry_count: int | None = None,
    new_last_error: str | None = None,
    new_status_note: str | None = None,
    append_sent_timestamp: bool = False,
) -> str:
    """Update tag-encoded fields in Notes; preserve other content (manifest, etc.)."""
    text = notes or ""
    if new_retry_count is not None:
        tag = f"[SEND_RETRY_COUNT: {new_retry_count}]"
        text = _RETRY_COUNT_TAG_RE.sub(tag, text) if _RETRY_COUNT_TAG_RE.search(text) else (f"{text} {tag}".strip() if text else tag)
    if new_last_error is not None:
        sanitized = new_last_error.replace("\n", " ").replace("[", "(").replace("]", ")")
        tag = f"[LAST_SEND_ERROR: {sanitized}]"
        text = _LAST_ERROR_TAG_RE.sub(tag, text) if _LAST_ERROR_TAG_RE.search(text) else (f"{text} {tag}".strip() if text else tag)
    if new_status_note is not None:
        text = f"{text} {new_status_note}".strip() if text else new_status_note
    if append_sent_timestamp:
        ts = datetime.now(UTC).replace(microsecond=0).isoformat()
        text = f"{text} sent={ts}".strip() if text else f"sent={ts}"
    return text


# ---- Recipient validation ------------------------------------------------


def _valid_addr(addr: str) -> bool:
    return bool(_ADDR_RE.match(addr or ""))


# ---- Box helper ----------------------------------------------------------


def _box_file_id(link: str) -> str | None:
    m = _BOX_FILE_LINK_RE.search(link or "")
    return m.group(1) if m else None


def _coerce_week(raw: Any) -> str:
    """`Week Of` (DATE) → ISO string for the subject ('' if unparseable)."""
    if isinstance(raw, date):
        return raw.isoformat()
    return str(raw or "")[:10]


# ---- send_one_row --------------------------------------------------------


def send_one_row(row_id: int) -> SendResult:
    """Send (or HELD / FAIL) one approved WSR_human_review row.

    SmartsheetError other than NotFound propagates to the caller (the poller's
    per-row fence handles)."""
    try:
        row = smartsheet_client.get_row(SHEET, row_id)
    except SmartsheetNotFoundError:
        error_log.log(
            Severity.INFO, SCRIPT_NAME,
            f"row_id={row_id} not found (deleted by operator?)",
            error_code="weekly_send.row_not_found",
        )
        return SendResult(status="row_not_found", row_id=row_id)

    project_name = str(row.get(wsr_review.COL_JOB_PROJECT) or "")
    notes = row.get(wsr_review.COL_NOTES) or ""
    send_status = row.get(wsr_review.COL_SEND_STATUS) or STATUS_PENDING
    retry_count = _parse_retry_count(notes)

    if send_status == STATUS_SENT:
        return SendResult(status="skipped_already_sent", row_id=row_id, project_name=project_name, retry_count=retry_count)
    if send_status == STATUS_HELD:
        return SendResult(status="skipped_held", row_id=row_id, project_name=project_name, retry_count=retry_count)

    # Stage 3: recipients RESOLVED AT SEND TIME from active_jobs (NOT the display cols).
    job_id = str(row.get(wsr_review.COL_JOB_ID) or "").strip()
    job = active_jobs.get_job(job_id)
    if job is None:
        return _mark_held(row_id, project_name, notes, f"unknown job_id={job_id!r} — cannot resolve recipients", "held_no_recipient")
    to_addr = (job.safety_reports_contact_email or "").strip()
    if not to_addr or not _valid_addr(to_addr):
        return _mark_held(row_id, project_name, notes, f"empty/invalid safety-reports contact (TO) for job {job_id}", "held_no_recipient")
    # CC already flattened + de-duped + validated by active_jobs; belt-and-suspenders re-filter.
    cc_list = [a for a in job.cc_emails if _valid_addr(a)]

    # Stage 4: the compiled packet (attach it; never send a half-formed packet).
    compiled_link = str(row.get(wsr_review.COL_COMPILED_PDF) or "")
    file_id = _box_file_id(compiled_link)
    if not file_id:
        return _mark_held(row_id, project_name, notes, "no Compiled PDF on the WSR row — recompile needed", "held_missing_pdf")
    try:
        pdf_bytes = box_client.download_file(file_id)
    except box_client.BoxError as exc:
        return _mark_failed(row_id, project_name, notes, retry_count + 1, f"Box download failed: {exc!r}", "send_failed")

    # Stage 5: build the email (body = the human-edited Email Body, source of truth).
    body = str(row.get(wsr_review.COL_EMAIL_BODY) or "")
    week = _coerce_week(row.get(wsr_review.COL_WEEK_OF))
    subject = f"Weekly Safety Report — {project_name} — week of {week}"
    from_mailbox = _read_str_setting(CFG_FROM_MAILBOX, DEFAULT_FROM_MAILBOX)
    attachment = {
        "name": f"Weekly Safety Report — {week}.pdf",
        "contentType": "application/pdf",
        "contentBytes": pdf_bytes,
    }

    # Stage 6: send. Log the RESOLVED recipients (brief §E).
    error_log.log(
        Severity.INFO, SCRIPT_NAME,
        f"sending WSR row_id={row_id} project={project_name!r} TO={to_addr!r} CC={cc_list}",
        error_code="weekly_send.dispatch",
    )
    try:
        graph_client.send_mail(
            from_mailbox=from_mailbox, to=[to_addr], cc=cc_list or None,
            subject=subject, body=body, content_type="Text", attachments=[attachment],
        )
    except GraphAuthError as exc:
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            f"Graph auth failure sending row_id={row_id} project={project_name!r}: {exc!r}. Operator credential rotation likely needed.",
            error_code="weekly_send.graph_auth_failed", exc_info=repr(exc),
        )
        return _mark_failed(row_id, project_name, notes, retry_count + 1, f"GraphAuthError: {exc!r}", "send_failed")
    except GraphError as exc:
        new_retry = retry_count + 1
        if new_retry >= MAX_SEND_RETRIES:
            error_log.log(
                Severity.CRITICAL, SCRIPT_NAME,
                f"row_id={row_id} project={project_name!r} hit MAX_SEND_RETRIES={MAX_SEND_RETRIES}; CRITICAL fire",
                error_code="weekly_send.retries_exhausted", exc_info=f"{type(exc).__name__}: {exc!r}",
            )
        else:
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"GraphError sending row_id={row_id} project={project_name!r} (retry {new_retry}/{MAX_SEND_RETRIES}): {exc!r}",
                error_code="weekly_send.graph_error",
            )
        return _mark_failed(row_id, project_name, notes, new_retry, f"{type(exc).__name__}: {exc!r}", "send_failed")

    # Stage 7: mark SENT.
    sent_at = datetime.now(UTC)
    new_notes = _update_notes_tags(notes, append_sent_timestamp=True)
    try:
        smartsheet_client.update_rows(
            SHEET,
            [{
                "_row_id": row_id,
                wsr_review.COL_SEND_STATUS: STATUS_SENT,
                wsr_review.COL_SENT_AT: sent_at.replace(microsecond=0).isoformat(),
                wsr_review.COL_NOTES: new_notes,
            }],
        )
    except SmartsheetError as exc:
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            f"row_id={row_id} send fired but row update failed: {exc!r}. DOUBLE-SEND RISK — operator must mark this row SENT manually.",
            error_code="weekly_send.post_send_row_update_failed", exc_info=repr(exc),
        )
        return SendResult(status="sent", row_id=row_id, project_name=project_name, error=f"row_update_failed: {exc!r}", retry_count=retry_count)

    error_log.log(
        Severity.INFO, SCRIPT_NAME,
        f"sent row_id={row_id} project={project_name!r} to={to_addr!r} cc={len(cc_list)}",
        error_code="weekly_send.sent",
    )
    return SendResult(status="sent", row_id=row_id, project_name=project_name, retry_count=retry_count)


def _mark_held(
    row_id: int, project_name: str, notes: str, reason: str, outcome: SendStatus,
) -> SendResult:
    """Set Send Status=HELD (operator-actionable refusal; no auto-retry).

    `outcome` is passed explicitly by the caller (NOT sniffed from the reason
    string) so the SendResult status is unambiguous."""
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        f"HELD row_id={row_id} project={project_name!r}: {reason}",
        error_code="weekly_send.held",
    )
    new_notes = _update_notes_tags(notes, new_status_note=f"[HELD: {reason}]")
    try:
        smartsheet_client.update_rows(
            SHEET, [{"_row_id": row_id, wsr_review.COL_SEND_STATUS: STATUS_HELD, wsr_review.COL_NOTES: new_notes}],
        )
    except SmartsheetError as exc:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"failed to mark row_id={row_id} HELD: {exc!r}",
            error_code="weekly_send.mark_held_failed",
        )
    return SendResult(status=outcome, row_id=row_id, project_name=project_name, error=reason)


def _mark_failed(
    row_id: int, project_name: str, notes: str, retry_count: int, error_text: str, outcome_status: SendStatus,
) -> SendResult:
    """Set Send Status=FAILED + Notes retry/error tags (transient → auto-retry)."""
    new_notes = _update_notes_tags(notes, new_retry_count=retry_count, new_last_error=error_text)
    try:
        smartsheet_client.update_rows(
            SHEET, [{"_row_id": row_id, wsr_review.COL_SEND_STATUS: STATUS_FAILED, wsr_review.COL_NOTES: new_notes}],
        )
    except SmartsheetError as exc:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"failed to mark row_id={row_id} FAILED: {exc!r}. Retry counter may under-count by one.",
            error_code="weekly_send.mark_failed_failed",
        )
    return SendResult(status=outcome_status, row_id=row_id, project_name=project_name, error=error_text, retry_count=retry_count)


# ---- main + CLI ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(row_id_override: int | None = None) -> dict[str, Any]:
    """Manual rerun of one approved WSR row via CLI (operator debugging)."""
    if row_id_override is None:
        raise SystemExit("usage: python -m safety_reports.weekly_send <row_id>")
    result = send_one_row(row_id_override)
    return {
        "row_id": result.row_id, "status": result.status,
        "project_name": result.project_name, "error": result.error,
        "retry_count": result.retry_count,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="safety_reports.weekly_send",
        description="Manually send (or HELD) one approved WSR_human_review row. Production sends fire via weekly_send_poll.",
    )
    parser.add_argument("row_id", type=int, help="WSR_human_review row ID.")
    args = parser.parse_args(argv)
    main(row_id_override=args.row_id)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
