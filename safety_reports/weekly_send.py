"""Safety Reports weekly send — transmit one approved WSR_human_review row via Graph.

Send half of the External Send Gate two-process model (Foundation Mission v11
Invariant 1) for the Safety Portal pull flow. Invoked per row by
`safety_reports/weekly_send_poll.py` (the launchd poller), which discovers WSR rows
with `Approve for Scheduled Send` (scheduled) OR `Send Now` (immediate) checked,
runs the F22 approval-attestation gate, then calls `send_one_row(row_id, cfg)`.

Phase-5 rewrite (2026-06-05): repointed WPR_Pending_Review → WSR_human_review.

**Zero AI capability** — `anthropic_client` / `anthropic` AST-forbidden via
`tests/test_capability_gating.py::SEND_SCRIPTS`.

**Parameterized (P1b — parameterize-not-clone, Op Stds §14 deviation).** Every
workstream-specific binding lives in a required, no-default `SendConfig`
(`send_one_row(row_id, cfg)`), so a future `progress_send` reuses this dispatch logic
WITHOUT cloning it. A cross-workstream **contamination guard** reads each row's
`Workstream` tag and HARD-HELDs (+ CRITICAL) a row whose tag != the sender's
(`safety`); an absent tag WARNs + proceeds (pre-backfill back-compat). See the
`SendConfig` block + the Stage-2b guard for the §42 rationale; §43 runbook
`docs/runbooks/safety_weekly_send.md`.

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
     A packet over Graph's ~150 MB upload-session ceiling → **HELD** (unsendable by
     any path; reduce photos / split) — checked before the write-ahead marker.
  5. Body = the WSR `Email Body` (the human's edits are the source of truth).
     Subject `Weekly Safety Report — <project> — week of <Week Of>`.
  6. Send via Graph (TO + CC + the PDF attachment). Log the resolved TO+CC.
     **Transport switch (PR-3):** a packet ≤ UPLOAD_SESSION_THRESHOLD_BYTES (2.5 MB)
     sends inline via `graph_client.send_mail`; a larger (photo-bearing) packet sends
     via `graph_client.send_mail_large_attachment` (the Graph upload-session: draft →
     chunked PUT → send) so it clears the ~3 MB inline /sendMail ceiling. Both paths
     are the same send capability and share the error fences below.
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol, cast

from safety_reports import wsr_review
from shared import (
    active_jobs,
    box_client,
    defaults,
    error_log,
    graph_client,
    recipient_health,
    smartsheet_client,
)
from shared.error_log import Severity, its_error_log
from shared.graph_client import GraphAttachmentTooLargeError, GraphAuthError, GraphError
from shared.kill_switch import require_active
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

SCRIPT_NAME = "safety_reports.weekly_send"
WORKSTREAM = "safety_reports"

CFG_FROM_MAILBOX = "safety_reports.weekly_send.from_mailbox"
DEFAULT_FROM_MAILBOX = "safety@evergreenmirror.com"

MAX_SEND_RETRIES = 3

# Transport switch: a compiled packet at or below this size sends inline via
# graph_client.send_mail (one request, base64-inline). Above it, a photo-bearing
# packet can blow past Graph's ~3 MB inline /sendMail ceiling, so we switch to the
# upload-session path (graph_client.send_mail_large_attachment). 2.5 MB leaves
# headroom below the 3 MB inline limit for the base64 (+33%) + envelope overhead.
# See docs/adr/0001-portal-photo-transport-d1-vs-r2.md + docs/tech_debt.md.
UPLOAD_SESSION_THRESHOLD_BYTES = int(2.5 * 1024 * 1024)  # 2,621,440

_ADDR_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LAST_ERROR_TAG_RE = re.compile(r"\[LAST_SEND_ERROR: [^\]]*\]")
_RETRY_COUNT_TAG_RE = re.compile(r"\[SEND_RETRY_COUNT: (\d+)\]")
_BOX_FILE_LINK_RE = re.compile(r"/file/(\d+)")

# Send Status picklist values (WSR; mirror wsr_review).
STATUS_PENDING = wsr_review.STATUS_PENDING
STATUS_SENT = wsr_review.STATUS_SENT
STATUS_FAILED = wsr_review.STATUS_FAILED
STATUS_HELD = wsr_review.STATUS_HELD
STATUS_SENDING = wsr_review.STATUS_SENDING  # write-ahead intent marker (see Stage 6)

SHEET = wsr_review.SHEET_ID


SendStatus = Literal[
    "sent",
    "skipped_already_sent",
    "skipped_held",
    "held_no_recipient",
    "held_missing_pdf",
    "held_oversized_packet",
    "held_workstream_mismatch",
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


# ---- SendConfig: the parameterize-not-clone binding (Op Stds §14 deviation) --
#
# weekly_send is the SEND half of the External Send Gate (FM v11 Invariant 1). To
# let a future progress_send reuse this exact dispatch logic WITHOUT cloning it (the
# cross-workstream contamination §14 warns of), every workstream-specific value is
# bound in a required, NO-DEFAULT SendConfig. No field defaults to a safety value: a
# default would let a new workstream silently inherit safety's recipients / sheet /
# subject / tag — the precise cross-wiring this guards. Constructing a config forces
# each workstream to NAME every binding explicitly; that *is* the gate.


class _ReviewModule(Protocol):
    """Structural type of a workstream review-sheet module (`wsr_review` for safety;
    a future `wpr_review` for progress). Bound as `SendConfig.review`."""

    SHEET_ID: int
    COL_JOB_PROJECT: str
    COL_JOB_ID: str
    COL_WEEK_OF: str
    COL_COMPILED_PDF: str
    COL_EMAIL_BODY: str
    COL_SEND_STATUS: str
    COL_SENT_AT: str
    COL_NOTES: str
    COL_WORKSTREAM: str
    STATUS_PENDING: str
    STATUS_SENT: str
    STATUS_FAILED: str
    STATUS_HELD: str
    STATUS_SENDING: str
    to_wsr_datetime: Callable[[datetime | str | None], str]


@dataclass(frozen=True)
class SendConfig:
    """Required, no-default per-workstream binding for `send_one_row` (see above)."""

    script_name: str
    workstream_tag: str          # the contamination-guard expected value ("safety")
    config_workstream: str       # ITS_Config get_setting scope ("safety_reports")
    review: _ReviewModule        # the review-sheet module (columns, statuses, sheet id)
    recipient_resolver: Callable[[Any], tuple[str, Sequence[str]]]
    # WHICH Active-Jobs sheet this workstream resolves recipients FROM. Required, no
    # default: a default would let a new workstream silently resolve from safety's
    # ITS_Active_Jobs (and thus its contacts) — the precise cross-wiring the no-default
    # SendConfig guards. Safety binds SAFETY_ACTIVE_JOBS_CONFIG (byte-identical to the
    # pre-P5 hardcoded default); progress binds PROGRESS_ACTIVE_JOBS_CONFIG. The
    # per-sheet TTL cache (active_jobs._cache) keeps the two resolutions from colliding.
    active_jobs_config: active_jobs.ActiveJobsConfig
    report_label: str            # subject + attachment label ("Weekly Safety Report")
    from_mailbox_cfg_key: str
    from_mailbox_default: str
    max_send_retries: int
    upload_session_threshold_bytes: int


def _resolve_safety_recipients(job: Any) -> tuple[str, Sequence[str]]:
    """Safety recipient binding: TO = the job's safety-reports contact; CC = its CC 1–5
    (already flattened / de-duped / validated by active_jobs). The stakeholder is NOT on
    the envelope. `send_one_row` re-validates the resolved addresses."""
    return (job.safety_reports_contact_email or "").strip(), job.cc_emails


CONFIG = SendConfig(
    script_name=SCRIPT_NAME,
    workstream_tag="safety",
    config_workstream=WORKSTREAM,
    # cast: a module doesn't structurally match a Protocol in mypy, but wsr_review DOES
    # satisfy _ReviewModule's surface (verified by the live tests + the structural contract).
    review=cast(_ReviewModule, wsr_review),
    recipient_resolver=_resolve_safety_recipients,
    active_jobs_config=active_jobs.SAFETY_ACTIVE_JOBS_CONFIG,
    report_label="Weekly Safety Report",
    from_mailbox_cfg_key=CFG_FROM_MAILBOX,
    from_mailbox_default=DEFAULT_FROM_MAILBOX,
    max_send_retries=MAX_SEND_RETRIES,
    upload_session_threshold_bytes=UPLOAD_SESSION_THRESHOLD_BYTES,
)


# ---- Config reader -------------------------------------------------------


def _read_str_setting(key: str, fallback: str, *, workstream: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=workstream)
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


def send_one_row(row_id: int, cfg: SendConfig) -> SendResult:
    """Send (or HELD / FAIL) one approved review row, per the workstream `cfg`.

    `cfg` is REQUIRED with no default — the sender cannot run without a workstream
    binding (the contamination gate). SmartsheetError other than NotFound propagates
    to the caller (the poller's per-row fence handles)."""
    try:
        row = smartsheet_client.get_row(cfg.review.SHEET_ID, row_id)
    except SmartsheetNotFoundError:
        error_log.log(
            Severity.INFO, cfg.script_name,
            f"row_id={row_id} not found (deleted by operator?)",
            error_code="weekly_send.row_not_found",
        )
        return SendResult(status="row_not_found", row_id=row_id)

    project_name = str(row.get(cfg.review.COL_JOB_PROJECT) or "")
    notes = row.get(cfg.review.COL_NOTES) or ""
    send_status = row.get(cfg.review.COL_SEND_STATUS) or cfg.review.STATUS_PENDING
    retry_count = _parse_retry_count(notes)

    if send_status == cfg.review.STATUS_SENT:
        return SendResult(status="skipped_already_sent", row_id=row_id, project_name=project_name, retry_count=retry_count)
    if send_status == cfg.review.STATUS_HELD:
        return SendResult(status="skipped_held", row_id=row_id, project_name=project_name, retry_count=retry_count)

    # Stage 2b: cross-workstream contamination guard (External Send Gate — defense-in-depth on top
    # of Invariant 1's two-process boundary, NOT the primary boundary). A row tagged for a DIFFERENT
    # workstream than this sender must NEVER transmit. Placed AFTER the SENT/HELD skip gates (a
    # terminal row is never rewritten) and BEFORE recipient resolution + the write-ahead SENDING
    # marker (a contaminated row never enters the in-flight state) — mirroring the Stage-4b oversized
    # refusal's fail-toward-not-sending placement.
    #
    # Three cases, keyed on the RAW cell value:
    #   - GENUINE-ABSENT (null / empty cell) → WARN + proceed. A deliberate, bounded fail-OPEN for
    #     the pre-backfill window. Bounded-SAFE because each review sheet bound here (WSR for safety,
    #     WPR for progress — send_one_row is dual-tenant via cfg.review.SHEET_ID as of P5) is
    #     single-workstream by construction: a blank-tag row IS a row for THIS sender's workstream
    #     (cfg.workstream_tag), sent to that workstream's recipients via its job + F22 — not
    #     cross-workstream contamination. (Tightening this to fail-CLOSED in the
    #     post-backfill steady state is a Send-Gate POSTURE decision reserved for Seth — §43 runbook.)
    #   - MALFORMED (a NON-empty raw value that STRIPS to empty — e.g. a U+00A0 / U+2007 whitespace
    #     cell) → HARD-HELD. A non-null cell that isn't a clean tag is a contamination signal, never
    #     the back-compat WARN path — closes the str().strip()-collapses-to-absent evasion.
    #   - MISMATCH (strips to a non-empty value != the sender's tag) → HARD-HELD + CRITICAL.
    # The review `Workstream` tag is the report-family value (`safety` / `progress`), DISTINCT from
    # cfg.config_workstream (`safety_reports`, the ITS_Config scope) and the global Workstream
    # picklist — do not unify them or the guard silently mis-compares.
    raw_workstream = row.get(cfg.review.COL_WORKSTREAM)
    stripped_workstream = str(raw_workstream).strip() if raw_workstream is not None else ""
    if raw_workstream is None or str(raw_workstream) == "":
        error_log.log(
            Severity.WARN, cfg.script_name,
            f"row_id={row_id} has no Workstream tag; proceeding as {cfg.workstream_tag!r} "
            "(back-compat — pre-backfill row).",
            error_code="weekly_send.workstream_absent",
        )
    elif stripped_workstream != cfg.workstream_tag:
        malformed = stripped_workstream == ""
        kind = "malformed whitespace Workstream tag" if malformed else "workstream contamination"
        error_log.log(
            Severity.CRITICAL, cfg.script_name,
            f"WORKSTREAM {'MALFORMED' if malformed else 'CONTAMINATION'}: row_id={row_id} "
            f"Workstream={raw_workstream!r} (stripped {stripped_workstream!r}) != sender "
            f"{cfg.workstream_tag!r}; send BLOCKED (fail-closed).",
            error_code="weekly_send.workstream_mismatch",
        )
        return _mark_held(
            row_id, project_name, notes,
            f"{kind}: row={raw_workstream!r} != sender {cfg.workstream_tag!r}",
            "held_workstream_mismatch", cfg,
        )
    # else: present and exact-match → proceed

    # Stage 3: recipients RESOLVED AT SEND TIME via the workstream resolver (NOT the display
    # cols). For safety: TO = the job's safety-reports contact; CC = its CC 1–5.
    job_id = str(row.get(cfg.review.COL_JOB_ID) or "").strip()
    # Resolve from THIS workstream's Active-Jobs sheet (cfg.active_jobs_config), never a
    # hardcoded default — a progress send can only ever read ITS_Active_Jobs_Progress, a
    # safety send only ITS_Active_Jobs (the cross-workstream recipient-contamination gate).
    job = active_jobs.get_job(job_id, cfg.active_jobs_config)
    if job is None:
        return _held_no_recipient(
            row_id, project_name, notes, cfg,
            job_id=job_id, reason=f"unknown job_id={job_id!r} — cannot resolve recipients",
        )
    to_addr, cc_raw = cfg.recipient_resolver(job)
    to_addr = (to_addr or "").strip()
    if not to_addr or not _valid_addr(to_addr):
        return _held_no_recipient(
            row_id, project_name, notes, cfg,
            job_id=job_id, reason=f"empty/invalid contact (TO) for job {job_id}",
        )
    # CC already flattened + de-duped + validated by the resolver (active_jobs._flatten_cc
    # WARNs on each malformed entry it drops). This belt-and-suspenders re-filter normally
    # strips nothing; if it ever DOES (a resolver bug, or a non-active_jobs resolver), WARN
    # rather than silently dropping a CC — "not a silent strip" (Op Stds never-silent).
    cc_list = [a for a in cc_raw if _valid_addr(a)]
    dropped_cc = [a for a in cc_raw if not _valid_addr(a)]
    if dropped_cc:
        error_log.log(
            Severity.WARN, cfg.script_name,
            f"row_id={row_id} job={job_id}: dropped {len(dropped_cc)} malformed CC "
            f"address(es) at send time: {dropped_cc!r} (resolver should have filtered these).",
            error_code="weekly_send.cc_dropped_malformed",
        )

    # Stage 4: the compiled packet (attach it; never send a half-formed packet).
    compiled_link = str(row.get(cfg.review.COL_COMPILED_PDF) or "")
    file_id = _box_file_id(compiled_link)
    if not file_id:
        return _mark_held(row_id, project_name, notes, "no Compiled PDF on the review row — recompile needed", "held_missing_pdf", cfg)
    try:
        pdf_bytes = box_client.download_file(file_id)
    except box_client.BoxError as exc:
        return _mark_failed(row_id, project_name, notes, retry_count + 1, f"Box download failed: {exc!r}", "send_failed", cfg)

    # Stage 4b: oversized-packet refusal. A packet over Graph's upload-session hard
    # ceiling (~150 MB) cannot be emailed by ANY Graph path — so HELD (operator-
    # actionable refusal), not FAILED-with-retry. Checked BEFORE the write-ahead
    # SENDING marker so the row never enters the in-flight state for a send that
    # can't happen. (The 2.5 MB inline/upload-session SWITCH is in Stage 6.)
    packet_size = len(pdf_bytes)
    if packet_size > graph_client.UPLOAD_SESSION_MAX_BYTES:
        return _mark_held(
            row_id, project_name, notes,
            f"compiled packet is {packet_size} bytes, over Graph's "
            f"{graph_client.UPLOAD_SESSION_MAX_BYTES}-byte upload-session ceiling — "
            "cannot email; reduce photo count / split the packet",
            "held_oversized_packet", cfg,
        )
    # Stage 4c: packet-size early warning (growth Slice 4b). A packet past
    # ~100 MB (defaults.PACKET_SIZE_WARN_BYTES) still SENDS — via the
    # upload-session path — but is forecast-close to the 150 MB HELD wall
    # above: a photo-heavy job that crossed 100 MB this week HELDs a few
    # weeks later, and HELD is otherwise only discovered at Friday send
    # time. WARN record (ITS_Errors, never a page) pointing at the manual
    # packet-split runbook so the operator can act BEFORE the wall. HELD
    # semantics above are untouched; this branch is unreachable for a
    # >150 MB packet (already returned).
    if packet_size > defaults.PACKET_SIZE_WARN_BYTES:
        error_log.log(
            Severity.WARN, cfg.script_name,
            f"row_id={row_id} project={project_name!r}: compiled packet is "
            f"{packet_size} bytes — over the {defaults.PACKET_SIZE_WARN_BYTES}-byte "
            f"early-warning threshold and approaching Graph's "
            f"{graph_client.UPLOAD_SESSION_MAX_BYTES}-byte HELD wall. Sending anyway; "
            f"see docs/runbooks/safety_weekly_send.md 'Packet approaching the 150 MB "
            f"ceiling' for the manual packet-split procedure.",
            error_code="weekly_send.packet_size_warn",
        )

    # Stage 5: build the email (body = the human-edited Email Body, source of truth).
    body = str(row.get(cfg.review.COL_EMAIL_BODY) or "")
    week = _coerce_week(row.get(cfg.review.COL_WEEK_OF))
    subject = f"{cfg.report_label} — {project_name} — week of {week}"
    from_mailbox = _read_str_setting(cfg.from_mailbox_cfg_key, cfg.from_mailbox_default, workstream=cfg.config_workstream)
    attachment_name = f"{cfg.report_label} — {week}.pdf"
    attachment = {
        "name": attachment_name,
        "contentType": "application/pdf",
        "contentBytes": pdf_bytes,
    }

    # Stage 6: send. Log the RESOLVED recipients (brief §E).
    error_log.log(
        Severity.INFO, cfg.script_name,
        f"sending review row_id={row_id} project={project_name!r} TO={to_addr!r} CC={cc_list}",
        error_code="weekly_send.dispatch",
    )
    # WRITE-AHEAD intent marker — the idempotency guard for the irreversible send. Flip
    # the row to SENDING *before* graph_client.send_mail. SENDING is NOT a dispatch
    # candidate (weekly_send_poll.DISPATCH_STATUSES = {PENDING, FAILED}), so if the
    # post-send SENT-stamp (Stage 7) fails, the row is left in SENDING and is NEVER
    # re-dispatched — the customer is not double-sent. If THIS write fails we have NOT
    # sent yet, so return without sending: the row stays PENDING/FAILED and retries next
    # cycle (a sustained Smartsheet outage is backstopped by the circuit breaker, which
    # also halts the poller's candidate read). Fail toward not-sending.
    try:
        smartsheet_client.update_rows(
            cfg.review.SHEET_ID, [{"_row_id": row_id, cfg.review.COL_SEND_STATUS: cfg.review.STATUS_SENDING}],
        )
    except SmartsheetError as exc:
        error_log.log(
            Severity.WARN, cfg.script_name,
            f"row_id={row_id} project={project_name!r}: pre-send SENDING marker write failed; "
            f"NOT sending this cycle (will retry): {exc!r}",
            error_code="weekly_send.pre_send_marker_failed",
        )
        return SendResult(
            status="send_failed", row_id=row_id, project_name=project_name,
            error=f"pre_send_marker_failed: {exc!r}", retry_count=retry_count,
        )
    # Transport switch: inline send_mail at/below the threshold; upload-session above
    # it (a photo-bearing packet can exceed Graph's ~3 MB inline /sendMail ceiling).
    # Both paths are the SAME send capability (Invariant 1) and share the error fences.
    try:
        if packet_size > cfg.upload_session_threshold_bytes:
            error_log.log(
                Severity.INFO, cfg.script_name,
                f"row_id={row_id} packet={packet_size}B > {cfg.upload_session_threshold_bytes}B "
                "— sending via Graph upload session (large attachment)",
                error_code="weekly_send.upload_session",
            )
            graph_client.send_mail_large_attachment(
                from_mailbox=from_mailbox, to=[to_addr], cc=cc_list or None,
                subject=subject, body=body, content_type="Text",
                attachment_name=attachment_name, attachment_bytes=pdf_bytes,
                attachment_content_type="application/pdf",
            )
        else:
            graph_client.send_mail(
                from_mailbox=from_mailbox, to=[to_addr], cc=cc_list or None,
                subject=subject, body=body, content_type="Text", attachments=[attachment],
            )
    except GraphAttachmentTooLargeError as exc:
        # Belt-and-suspenders: Stage 4b already HELDs an over-ceiling packet, but if the
        # threshold/ceiling constants ever drift, the upload-session layer's own guard
        # still refuses rather than retrying forever. HELD (operator-actionable), and the
        # row was already flipped to SENDING — _mark_held overwrites it back to HELD.
        return _mark_held(
            row_id, project_name, notes,
            f"upload-session refused oversized attachment: {exc!r}",
            "held_oversized_packet", cfg,
        )
    except GraphAuthError as exc:
        error_log.log(
            Severity.CRITICAL, cfg.script_name,
            f"Graph auth failure sending row_id={row_id} project={project_name!r}: {exc!r}. Operator credential rotation likely needed.",
            error_code="weekly_send.graph_auth_failed", exc_info=repr(exc),
        )
        return _mark_failed(row_id, project_name, notes, retry_count + 1, f"GraphAuthError: {exc!r}", "send_failed", cfg)
    except GraphError as exc:
        new_retry = retry_count + 1
        if new_retry >= cfg.max_send_retries:
            error_log.log(
                Severity.CRITICAL, cfg.script_name,
                f"row_id={row_id} project={project_name!r} hit max_send_retries={cfg.max_send_retries}; CRITICAL fire",
                error_code="weekly_send.retries_exhausted", exc_info=f"{type(exc).__name__}: {exc!r}",
            )
        else:
            error_log.log(
                Severity.ERROR, cfg.script_name,
                f"GraphError sending row_id={row_id} project={project_name!r} (retry {new_retry}/{cfg.max_send_retries}): {exc!r}",
                error_code="weekly_send.graph_error",
            )
        return _mark_failed(row_id, project_name, notes, new_retry, f"{type(exc).__name__}: {exc!r}", "send_failed", cfg)

    # Stage 7: mark SENT.
    sent_at = datetime.now(UTC)
    new_notes = _update_notes_tags(notes, append_sent_timestamp=True)
    try:
        smartsheet_client.update_rows(
            cfg.review.SHEET_ID,
            [{
                "_row_id": row_id,
                cfg.review.COL_SEND_STATUS: cfg.review.STATUS_SENT,
                # ABSTRACT_DATETIME column: naive Pacific wall-clock (an offset-bearing
                # value is rejected, errorCode 5536). The Notes `sent=` tag stays UTC.
                cfg.review.COL_SENT_AT: cfg.review.to_wsr_datetime(sent_at),
                cfg.review.COL_NOTES: new_notes,
            }],
        )
    except SmartsheetError as exc:
        error_log.log(
            Severity.CRITICAL, cfg.script_name,
            f"row_id={row_id} send fired but SENT-stamp failed: {exc!r}. Row is left in "
            f"SENDING (the write-ahead marker), so it is NOT re-dispatched — no double-send. "
            f"Operator: confirm delivery, then mark SENT (watchdog Check N also flags this).",
            error_code="weekly_send.post_send_row_update_failed", exc_info=repr(exc),
        )
        return SendResult(status="sent", row_id=row_id, project_name=project_name, error=f"row_update_failed: {exc!r}", retry_count=retry_count)

    error_log.log(
        Severity.INFO, cfg.script_name,
        f"sent row_id={row_id} project={project_name!r} to={to_addr!r} cc={len(cc_list)}",
        error_code="weekly_send.sent",
    )
    return SendResult(status="sent", row_id=row_id, project_name=project_name, retry_count=retry_count)


def _held_no_recipient(
    row_id: int, project_name: str, notes: str, cfg: SendConfig,
    *, job_id: str, reason: str,
) -> SendResult:
    """HELD for an unhealthy send recipient — surfaced as a tracked record, then HELD.

    A bare HELD is operator-actionable but easy to miss (it sits in the review sheet until
    someone notices). This wraps the HELD with `shared.recipient_health` so a stale / empty /
    invalid recipient also files a queryable `ITS_Review_Queue` record ("never silent", the
    cross-cutting ITS invariant — a §3.1 RECORD leg, idempotent on open-row state, NOT a
    push-deduped alert; watchdog Check A escalates it if it goes stale). Built ONCE here so BOTH
    workstreams (safety via WSR, progress via WPR) inherit it. `report_unhealthy_recipient` is
    fail-soft (never raises), so the HELD below always happens regardless of the record leg."""
    recipient_health.report_unhealthy_recipient(
        config_workstream=cfg.config_workstream,
        script_name=cfg.script_name,
        row_id=row_id,
        job_id=job_id,
        project_name=project_name,
        reason_detail=reason,
    )
    return _mark_held(row_id, project_name, notes, reason, "held_no_recipient", cfg)


def _mark_held(
    row_id: int, project_name: str, notes: str, reason: str, outcome: SendStatus,
    cfg: SendConfig,
) -> SendResult:
    """Set Send Status=HELD (operator-actionable refusal; no auto-retry).

    `outcome` is passed explicitly by the caller (NOT sniffed from the reason
    string) so the SendResult status is unambiguous."""
    error_log.log(
        Severity.WARN, cfg.script_name,
        f"HELD row_id={row_id} project={project_name!r}: {reason}",
        error_code="weekly_send.held",
    )
    new_notes = _update_notes_tags(notes, new_status_note=f"[HELD: {reason}]")
    try:
        smartsheet_client.update_rows(
            cfg.review.SHEET_ID, [{"_row_id": row_id, cfg.review.COL_SEND_STATUS: cfg.review.STATUS_HELD, cfg.review.COL_NOTES: new_notes}],
        )
    except SmartsheetError as exc:
        error_log.log(
            Severity.WARN, cfg.script_name,
            f"failed to mark row_id={row_id} HELD: {exc!r}",
            error_code="weekly_send.mark_held_failed",
        )
    return SendResult(status=outcome, row_id=row_id, project_name=project_name, error=reason)


def _mark_failed(
    row_id: int, project_name: str, notes: str, retry_count: int, error_text: str, outcome_status: SendStatus,
    cfg: SendConfig,
) -> SendResult:
    """Set Send Status=FAILED + Notes retry/error tags (transient → auto-retry)."""
    new_notes = _update_notes_tags(notes, new_retry_count=retry_count, new_last_error=error_text)
    try:
        smartsheet_client.update_rows(
            cfg.review.SHEET_ID, [{"_row_id": row_id, cfg.review.COL_SEND_STATUS: cfg.review.STATUS_FAILED, cfg.review.COL_NOTES: new_notes}],
        )
    except SmartsheetError as exc:
        error_log.log(
            Severity.WARN, cfg.script_name,
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
    result = send_one_row(row_id_override, CONFIG)
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
