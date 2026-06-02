"""Safety Reports weekly send — transmit one approved WPR row via Graph mail.

Send half of the External Send Gate two-process model (Foundation Mission
v8 Invariant 1). Invoked per row by `safety_reports/weekly_send_poll.py`
(the launchd-driven polling daemon) which calls `send_one_row(row_id)`
for each approved-PENDING row discovered on `WPR_Pending_Review`. The
`main()` CLI wrapper preserves a manual-rerun entrypoint:
`python -m safety_reports.weekly_send <row_id>` re-sends one row by ID
(useful for operator debugging or replaying a SEND_FAILED row after a
config fix).

Zero AI capability — `anthropic_client`, `anthropic` AST-forbidden via
`tests/test_capability_gating.py::SEND_SCRIPTS`. The send process performs
no fresh extraction or generation; it transmits a Draft Body that was
already produced + reviewed + approved upstream.

7-stage pipeline (per `send_one_row(row_id)`)
---------------------------------------------

  1. Fetch row via `smartsheet_client.get_row`. 404 → row_not_found
     (operator deleted the row out from under us; not an error — return).
     Other SmartsheetError → re-raise; caller's per-row fence handles.

  2. State gate. Read Send Status, Approved for Send, Notes. Apply the
     idempotency contract decision table:
       - Send Status=SENT → skip (already sent).
       - Send Status=PENDING + Approved=False → skip (still in review).
       - Send Status=FAILED + retries >= MAX_SEND_RETRIES → CRITICAL-fire
         (terminal; needs human resolution).
       - Notes contains `[GENERATION_FAILED:` → refuse regardless of
         Recipients or Approved state. Belt-and-suspenders: if the
         placeholder row is approved by mistake, we still refuse.
       - Empty Recipients → skip silently (this is a `[NO_RECIPIENTS]`
         design-property hold from weekly_generate; operator resolves
         via Smartsheet UI by populating ITS_Config recipients row and
         re-running weekly_generate for that project-week).
       - `[SECURITY_TRIGGER]`, `[LOW_CONFIDENCE]`, `[ZERO_DATA_WEEK]`
         tags are ADVISORY once approved (reviewer's explicit
         Approved for Send=True overrides). Send fires normally.

  3. Recipients parse + validate. `json.loads(row["Recipients"])` to a
     list of strings; validate each against an RFC822-ish regex. Any
     malformed address → mark FAILED + record error in Notes tag,
     return invalid_recipients.

  4. Build email. Subject `f"WPR — {project_name} — Week of {date_human}"`.
     `from_mailbox` from ITS_Config (default mirror tenant safety mailbox).
     Body = Draft Body as-is, content_type="Text". HTML rendering is a
     v0.2.0 follow-on (tech_debt entry).

  5. Send via Graph. `graph_client.send_mail(...)`. Catch GraphAuthError
     → CRITICAL triple-fire (operator credential rotation needed) +
     mark FAILED. Catch generic GraphError → increment retry count
     (tag-encoded in Notes; see schema-degradation note below), mark
     FAILED, log ERROR. On retry-count exhaust, ALSO triple-fire.

  6. Compute Late Send. Parse deadline from ITS_Config
     `safety_reports.weekly_send.send_deadline_local` (ISO-weekday + HH:MM,
     default `MON 12:00`). Deadline datetime is per-week:
     monday-after-week-start at the configured local time. If
     `datetime.now(local_tz) > deadline` → Late Send=True. Informational
     only; never gates sending.

  7. Update row to SENT. Set `Send Status=SENT`, `Sent At=iso_now()`,
     `Late Send=<bool>`, append `sent=<iso_now()>` to Notes.

Schema-degradation note (2026-05-23)
------------------------------------

The live `WPR_Pending_Review` schema (sheet 3096105695793028) does NOT
have `Last Send Error` or `Send Retry Count` columns. Per the brief and
Op Stds v11 §19 (sheet-level columns added via UI, not API), this
module gracefully degrades by encoding both fields as bracketed tags in
the `Notes` column:

  - `[LAST_SEND_ERROR: <ExceptionClass>: <message>]`
  - `[SEND_RETRY_COUNT: N]`

Parsing on read; replace-or-append on write. Each cycle's tag replaces
the previous instance (regex-based). When operator adds the explicit
columns via Smartsheet UI, a follow-on PR can migrate to native columns;
the tag encoding stays as a fallback for backwards-compatibility during
the migration window.

Picklist drift note (2026-05-23)
--------------------------------

The brief referenced `Send Status=SEND_FAILED` but the actual picklist
column allows `PENDING | SENT | FAILED | HELD`. This module uses
`FAILED` (no SEND_ prefix); the picklist enforcement at the Smartsheet
side would have rejected the brief's SEND_FAILED string. `HELD` is
unused today (reserved for future operator-driven manual hold; brief's
empty-Recipients case stays at PENDING per the "skip silently" contract).

Capability gating
-----------------

No AI capability. Imports `shared.graph_client` for `send_mail`. Does
NOT import `anthropic_client`, `anthropic`, or any LLM client. AST gate
in `tests/test_capability_gating.py::SEND_SCRIPTS` enforces.

Push-vs-Record Separation (Op Stds v11 §3.1)
--------------------------------------------

Each successful `graph_client.send_mail` is a PUSH event — but the send
itself is NOT deduped (every approved row sends exactly once; the
`Sent At` column is the canonical idempotency watermark). The dedupe
scope is the CRITICAL-tier failure alert: 3 consecutive SEND_FAILEDs on
the same row produce ONE alert (not three) per the `alert_dedupe` key
shape `(script, error_code)`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from shared import error_log, graph_client, sheet_ids, smartsheet_client
from shared.error_log import Severity, its_error_log
from shared.graph_client import GraphAuthError, GraphError
from shared.kill_switch import require_active
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

SCRIPT_NAME = "safety_reports.weekly_send"
WORKSTREAM = "safety_reports"

# ITS_Config keys (operator-side seeding required; defaults apply when
# rows are missing).
CFG_FROM_MAILBOX = "safety_reports.weekly_send.from_mailbox"
CFG_SEND_DEADLINE = "safety_reports.weekly_send.send_deadline_local"

# Default from-mailbox matches the sandbox tenant; production cutover will
# update the ITS_Config row (no code change). The default is operationally
# safe: if the row is missing the send still attempts via the mirror
# mailbox, which the operator can intercept.
DEFAULT_FROM_MAILBOX = "safety@evergreenmirror.com"
DEFAULT_SEND_DEADLINE = "MON 12:00"
DEFAULT_TZ = "America/Los_Angeles"  # operator local time per Brief v6.1

MAX_SEND_RETRIES = 3

# RFC822-ish minimal address validation — strict enough to reject the
# obvious malformed cases (no @, no domain TLD, embedded whitespace) but
# not exhaustive. Graph will reject more sophisticated edge cases with a
# clearer 400 if any slip through.
_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Tag-encoding regexes for the Notes-column graceful-degrade columns.
_LAST_ERROR_TAG_RE = re.compile(r"\[LAST_SEND_ERROR: [^\]]*\]")
_RETRY_COUNT_TAG_RE = re.compile(r"\[SEND_RETRY_COUNT: (\d+)\]")

# Status values written to Send Status (picklist). FAILED is the live
# picklist value; brief said SEND_FAILED but the picklist enforcement
# would have rejected that.
STATUS_PENDING = "PENDING"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"

# Refusal-tag substrings searched in Notes — these gate sending even
# when Approved for Send=True. Belt-and-suspenders against placeholder
# rows accidentally approved by the operator.
REFUSAL_TAG_GENERATION_FAILED = "[GENERATION_FAILED:"


# ---- Data classes --------------------------------------------------------


SendStatus = Literal[
    "sent",
    "skipped_already_sent",
    "skipped_not_approved",
    "skipped_no_recipients",
    "skipped_generation_failed",
    "skipped_retries_exhausted",
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
    late: bool = False
    error: str | None = None
    retry_count: int = 0


# ---- Config readers (replicated from intake_poll per preservation) ------


def _read_str_setting(key: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except SmartsheetNotFoundError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


# ---- Tag-encoded field helpers (graceful-degrade for missing columns) ---


def _parse_retry_count(notes: str | None) -> int:
    """Extract `[SEND_RETRY_COUNT: N]` from Notes; 0 if absent."""
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
    append_sent_timestamp: bool = False,
) -> str:
    """Update tag-encoded fields in the Notes string.

    Each updatable field has a regex that locates the existing tag (if any)
    and replaces it with the new value; if no tag exists, append. Other
    Notes content (e.g. `[ZERO_DATA_WEEK]`, `[LOW_CONFIDENCE: 0.50]`,
    `generated=...`) is preserved.
    """
    text = notes or ""
    if new_retry_count is not None:
        new_tag = f"[SEND_RETRY_COUNT: {new_retry_count}]"
        if _RETRY_COUNT_TAG_RE.search(text):
            text = _RETRY_COUNT_TAG_RE.sub(new_tag, text)
        else:
            text = f"{text} {new_tag}".strip() if text else new_tag
    if new_last_error is not None:
        # Sanitize: strip newlines + brackets so the tag stays well-formed.
        # Both [ and ] convert so an embedded bracket pair doesn't close
        # the surrounding [LAST_SEND_ERROR: ...] tag early.
        sanitized = (
            new_last_error.replace("\n", " ").replace("[", "(").replace("]", ")")
        )
        new_tag = f"[LAST_SEND_ERROR: {sanitized}]"
        if _LAST_ERROR_TAG_RE.search(text):
            text = _LAST_ERROR_TAG_RE.sub(new_tag, text)
        else:
            text = f"{text} {new_tag}".strip() if text else new_tag
    if append_sent_timestamp:
        ts = datetime.now(UTC).replace(microsecond=0).isoformat()
        text = f"{text} sent={ts}".strip() if text else f"sent={ts}"
    return text


# ---- Recipients validation ----------------------------------------------


def _parse_recipients(raw: Any) -> list[str] | None:
    """Parse the Recipients cell into a list of trimmed strings.

    Returns None on parse failure; empty list when the cell is empty.
    Callers distinguish None (malformed JSON — likely operator typo) from
    [] (deliberately empty — `[NO_RECIPIENTS]` design hold).
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(e).strip() for e in raw if isinstance(e, str) and e.strip()]
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [str(e).strip() for e in parsed if isinstance(e, str) and e.strip()]


def _validate_recipients(recipients: list[str]) -> tuple[bool, str | None]:
    """Returns (all_valid, first_bad_address_or_None)."""
    for addr in recipients:
        if not _ADDR_RE.match(addr):
            return False, addr
    return True, None


# ---- Deadline / Late Send -----------------------------------------------


_WEEKDAY_MAP = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}


def _parse_deadline_spec(spec: str) -> tuple[int, time]:
    """Parse `MON 12:00` -> (0, time(12, 0)).

    Returns the default (`MON 12:00`) on parse failure — Late Send is
    informational only, so a deadline-parse failure should not block sending.
    """
    try:
        weekday_str, hhmm_str = spec.strip().split()
        weekday = _WEEKDAY_MAP[weekday_str.upper()]
        hour_str, minute_str = hhmm_str.split(":")
        return weekday, time(int(hour_str), int(minute_str))
    except (KeyError, ValueError):
        return 0, time(12, 0)


def _compute_late_send(week_start: date, now_local: datetime, deadline_spec: str) -> bool:
    """True iff `now_local` is past the deadline for the row's week.

    Deadline = the configured weekday + time, computed relative to the
    week_start. E.g. for week_start = Monday 2026-03-16 and spec
    `MON 12:00`, deadline = Monday 2026-03-23 12:00 local (Monday AFTER
    week_start; the brief's "Monday-after-week-start" semantic).
    """
    weekday_offset, time_of_day = _parse_deadline_spec(deadline_spec)
    # The deadline is the NEXT occurrence of weekday_offset on/after week_start + 7 days.
    base = week_start + timedelta(days=7)
    days_forward = (weekday_offset - base.weekday()) % 7
    deadline_date = base + timedelta(days=days_forward)
    deadline_dt = datetime.combine(deadline_date, time_of_day, tzinfo=now_local.tzinfo)
    return now_local > deadline_dt


# ---- Row state helpers ---------------------------------------------------


def _coerce_week_to_date(raw: Any) -> date | None:
    """`Week` is a DATE column; the SDK returns it as either date or ISO string."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _humanize_week(week: date) -> str:
    """`2026-03-16` -> `March 16, 2026` for the email subject line."""
    return week.strftime("%B %-d, %Y")


# ---- send_one_row pipeline ----------------------------------------------


def send_one_row(row_id: int) -> SendResult:
    """Send (or refuse / fail) one approved WPR row.

    Returns a SendResult describing the outcome. SmartsheetError other
    than NotFoundError propagates to the caller (the poller's per-row
    fence handles).
    """
    # Stage 1: Fetch.
    try:
        row = smartsheet_client.get_row(sheet_ids.SHEET_WPR_PENDING_REVIEW, row_id)
    except SmartsheetNotFoundError:
        error_log.log(
            Severity.INFO,
            SCRIPT_NAME,
            f"row_id={row_id} not found (deleted by operator?)",
            error_code="weekly_send.row_not_found",
        )
        return SendResult(status="row_not_found", row_id=row_id)

    project_name = str(row.get("Job") or "")
    notes = row.get("Notes") or ""

    # Stage 2: State gate.
    send_status = row.get("Send Status") or STATUS_PENDING
    approved = bool(row.get("Approved for Send"))
    retry_count = _parse_retry_count(notes)

    if send_status == STATUS_SENT:
        return SendResult(
            status="skipped_already_sent",
            row_id=row_id,
            project_name=project_name,
            retry_count=retry_count,
        )

    if REFUSAL_TAG_GENERATION_FAILED in notes:
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            (
                f"row_id={row_id} has GENERATION_FAILED tag; refusing send "
                f"regardless of Approved state"
            ),
            error_code="weekly_send.refused_generation_failed",
        )
        return SendResult(
            status="skipped_generation_failed",
            row_id=row_id,
            project_name=project_name,
            retry_count=retry_count,
        )

    if not approved:
        return SendResult(
            status="skipped_not_approved",
            row_id=row_id,
            project_name=project_name,
            retry_count=retry_count,
        )

    if send_status == STATUS_FAILED and retry_count >= MAX_SEND_RETRIES:
        return SendResult(
            status="skipped_retries_exhausted",
            row_id=row_id,
            project_name=project_name,
            retry_count=retry_count,
        )

    # Stage 3: Recipients parse + validate.
    recipients = _parse_recipients(row.get("Recipients"))
    if recipients is None:
        # Malformed JSON in Recipients cell — treat as FAILED.
        return _mark_failed(
            row_id=row_id,
            project_name=project_name,
            notes=notes,
            retry_count=retry_count + 1,
            error_text="Recipients cell is not a valid JSON list",
            outcome_status="invalid_recipients",
        )
    if not recipients:
        # Empty Recipients — `[NO_RECIPIENTS]` design hold from
        # weekly_generate. Skip silently; do NOT mark FAILED.
        return SendResult(
            status="skipped_no_recipients",
            row_id=row_id,
            project_name=project_name,
            retry_count=retry_count,
        )
    all_valid, bad_addr = _validate_recipients(recipients)
    if not all_valid:
        return _mark_failed(
            row_id=row_id,
            project_name=project_name,
            notes=notes,
            retry_count=retry_count + 1,
            error_text=f"invalid_recipient: {bad_addr!r}",
            outcome_status="invalid_recipients",
        )

    # Stage 4: Build email.
    week = _coerce_week_to_date(row.get("Week"))
    if week is None:
        return _mark_failed(
            row_id=row_id,
            project_name=project_name,
            notes=notes,
            retry_count=retry_count + 1,
            error_text="Week column unparseable",
            outcome_status="send_failed",
        )
    subject = f"WPR — {project_name} — Week of {_humanize_week(week)}"
    body = str(row.get("Draft Body") or "")
    from_mailbox = _read_str_setting(CFG_FROM_MAILBOX, DEFAULT_FROM_MAILBOX)

    # Stage 5: Send via Graph.
    try:
        graph_client.send_mail(
            from_mailbox=from_mailbox,
            to=recipients,
            subject=subject,
            body=body,
            content_type="Text",
        )
    except GraphAuthError as exc:
        new_retry = retry_count + 1
        error_log.log(
            Severity.CRITICAL,
            SCRIPT_NAME,
            (
                f"Graph auth failure sending row_id={row_id} project={project_name!r}: "
                f"{exc!r}. Operator credential rotation likely needed."
            ),
            error_code="weekly_send.graph_auth_failed",
        )
        error_log._alert_critical(
            SCRIPT_NAME,
            f"weekly_send Graph auth failure for row_id={row_id}",
            repr(exc),
            error_code="weekly_send.graph_auth_failed",
        )
        return _mark_failed(
            row_id=row_id,
            project_name=project_name,
            notes=notes,
            retry_count=new_retry,
            error_text=f"GraphAuthError: {exc!r}",
            outcome_status="send_failed",
        )
    except GraphError as exc:
        new_retry = retry_count + 1
        # Per-cycle: ONE attempt. The retry counter ratchets across cycles;
        # the poller's next pass picks up the FAILED row and retries until
        # MAX_SEND_RETRIES.
        if new_retry >= MAX_SEND_RETRIES:
            error_log.log(
                Severity.CRITICAL,
                SCRIPT_NAME,
                (
                    f"row_id={row_id} project={project_name!r} hit "
                    f"MAX_SEND_RETRIES={MAX_SEND_RETRIES}; CRITICAL fire"
                ),
                error_code="weekly_send.retries_exhausted",
            )
            error_log._alert_critical(
                SCRIPT_NAME,
                (
                    f"weekly_send retries exhausted for row_id={row_id} "
                    f"project={project_name}"
                ),
                f"{type(exc).__name__}: {exc!r}",
                error_code="weekly_send.retries_exhausted",
            )
        else:
            error_log.log(
                Severity.ERROR,
                SCRIPT_NAME,
                (
                    f"GraphError sending row_id={row_id} project={project_name!r} "
                    f"(retry {new_retry}/{MAX_SEND_RETRIES}): {exc!r}"
                ),
                error_code="weekly_send.graph_error",
            )
        return _mark_failed(
            row_id=row_id,
            project_name=project_name,
            notes=notes,
            retry_count=new_retry,
            error_text=f"{type(exc).__name__}: {exc!r}",
            outcome_status="send_failed",
        )

    # Stage 6: Compute Late Send.
    deadline_spec = _read_str_setting(CFG_SEND_DEADLINE, DEFAULT_SEND_DEADLINE)
    now_local = datetime.now(ZoneInfo(DEFAULT_TZ))
    late = _compute_late_send(week, now_local, deadline_spec)

    # Stage 7: Update row to SENT.
    sent_at = datetime.now(UTC)
    new_notes = _update_notes_tags(
        notes,
        new_retry_count=None,  # leave retry count alone on success
        append_sent_timestamp=True,
    )
    try:
        smartsheet_client.update_rows(
            sheet_ids.SHEET_WPR_PENDING_REVIEW,
            [
                {
                    "_row_id": row_id,
                    "Send Status": STATUS_SENT,
                    "Sent At": sent_at.replace(microsecond=0).isoformat(),
                    "Late Send": late,
                    "Notes": new_notes,
                }
            ],
        )
    except SmartsheetError as exc:
        # The send already fired (Graph 202 Accepted). Failing to update
        # the row means we'll re-send on the next poll cycle — a
        # double-send risk. Log CRITICAL but do NOT auto-retry the row
        # update inline; operator inspection is the right disposition.
        error_log.log(
            Severity.CRITICAL,
            SCRIPT_NAME,
            (
                f"row_id={row_id} send fired but row update failed: {exc!r}. "
                f"DOUBLE-SEND RISK — operator must mark this row SENT manually."
            ),
            error_code="weekly_send.post_send_row_update_failed",
        )
        error_log._alert_critical(
            SCRIPT_NAME,
            f"weekly_send post-send row update failed for row_id={row_id}",
            repr(exc),
            error_code="weekly_send.post_send_row_update_failed",
        )
        return SendResult(
            status="sent",  # send DID happen
            row_id=row_id,
            project_name=project_name,
            late=late,
            error=f"row_update_failed: {exc!r}",
            retry_count=retry_count,
        )

    error_log.log(
        Severity.INFO,
        SCRIPT_NAME,
        (
            f"sent row_id={row_id} project={project_name!r} "
            f"recipients={len(recipients)} late={late}"
        ),
        error_code="weekly_send.sent",
    )
    return SendResult(
        status="sent",
        row_id=row_id,
        project_name=project_name,
        late=late,
        retry_count=retry_count,
    )


def _mark_failed(
    *,
    row_id: int,
    project_name: str,
    notes: str,
    retry_count: int,
    error_text: str,
    outcome_status: SendStatus,
) -> SendResult:
    """Write Send Status=FAILED + updated Notes tags. Return SendResult.

    Best-effort: if the row update itself fails, log + continue. The
    failure-to-mark case is rare (Smartsheet would need to be unreachable)
    and the next poll cycle will re-attempt the send anyway — at worst
    the retry counter under-counts by one.
    """
    new_notes = _update_notes_tags(
        notes,
        new_retry_count=retry_count,
        new_last_error=error_text,
    )
    try:
        smartsheet_client.update_rows(
            sheet_ids.SHEET_WPR_PENDING_REVIEW,
            [
                {
                    "_row_id": row_id,
                    "Send Status": STATUS_FAILED,
                    "Notes": new_notes,
                }
            ],
        )
    except SmartsheetError as exc:
        error_log.log(
            Severity.WARN,
            SCRIPT_NAME,
            (
                f"failed to mark row_id={row_id} as FAILED: {exc!r}. "
                f"Retry counter may under-count by one."
            ),
            error_code="weekly_send.mark_failed_failed",
        )
    return SendResult(
        status=outcome_status,
        row_id=row_id,
        project_name=project_name,
        error=error_text,
        retry_count=retry_count,
    )


# ---- main + CLI ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(row_id_override: int | None = None) -> dict[str, Any]:
    """Manual rerun of one approved row via CLI.

    Production sends fire via `weekly_send_poll`. This CLI exists for
    operator-driven debugging (replay a SEND_FAILED row after a config
    fix, force-send a row whose Send Status is stuck PENDING despite
    Approved=True, etc.).
    """
    if row_id_override is None:
        raise SystemExit("usage: python -m safety_reports.weekly_send <row_id>")
    result = send_one_row(row_id_override)
    return {
        "row_id": result.row_id,
        "status": result.status,
        "project_name": result.project_name,
        "late": result.late,
        "error": result.error,
        "retry_count": result.retry_count,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="safety_reports.weekly_send",
        description=(
            "Manually send (or refuse) one approved WPR_Pending_Review row. "
            "Production sends fire via weekly_send_poll; this CLI is for "
            "operator debugging."
        ),
    )
    parser.add_argument("row_id", type=int, help="WPR_Pending_Review row ID.")
    args = parser.parse_args(argv)
    main(row_id_override=args.row_id)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
