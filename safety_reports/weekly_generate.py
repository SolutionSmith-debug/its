"""Safety Reports weekly compile — DETERMINISTIC weekly packet + WSR dual-write.

Generation half of the External Send Gate two-process model (Foundation Mission v11
Invariant 1) for the Safety Portal pull flow. **Zero send capability. Zero AI.**

Phase-5 rewrite (2026-06-05): the legacy WPR flow drafted a per-project narrative via
Anthropic and wrote `WPR_Pending_Review`. The portal flow is DETERMINISTIC — there is
no narrative to draft and no LLM call. This module now COMPILES: for each Active job's
Saturday→Friday week it gathers the per-submission PDFs recorded on the week sheet,
merges them (`form_pdf.merge_pdfs`) into one weekly packet, files the packet to an
`ITS`-prefixed Box week folder as a DISTINCT timestamped file, and APPENDS (operator
decision 2026-06-09 — append-only, never overwrite):
  (a) a NEW read-only **Rollup** snapshot row on the week sheet — a manifest of THIS packet;
  (b) a NEW **WSR_human_review** row (per compilation) — the editable Email Body (seeded
      from a fixed template), the resolved Recipient TO/CC display, Send Status=PENDING.
A recompile NEVER overwrites a prior compilation's Box packet, Rollup row, or WSR row, so
the master record keeps every weekly packet and the full send history. `weekly_send`
(Phase 5c) reads the human-approved WSR row and transmits (a SENT row is never re-sent).

Trigger + idempotency
---------------------
- Friday 14:00 local via launchd `StartCalendarInterval` (the watchdog Check-I catch-up
  re-runs a missed Friday). `--week-start` backfills a specific week.
- SKIP-if-already-compiled-and-no-new-docs: if a Rollup row exists and no submission is
  newer than the LATEST Rollup's `compiled_at` watermark, skip (unless the operator checked
  `Compile Now` on any Rollup row — an out-of-band recompile). The skip is what keeps
  append-only from cluttering: a NEW packet/Rollup/WSR row is created ONLY for genuinely new
  content (or a forced Compile Now), never on an idle re-run.
- NEVER closes the week: a later submission + a recompile APPENDS a fresh packet + Rollup +
  PENDING WSR row; prior compilations (incl. a SENT WSR row) are untouched (only a human
  flips approval; F22 verifies the actor).
- Empty week → STILL appends a Rollup + WSR row (a silent skip would look like daemon
  failure); the WSR row carries no packet, so `weekly_send` HELDs it.

Capability gating (Invariant 1)
-------------------------------
No send, no AI. `tests/test_capability_gating.py::GATED_SCRIPTS` forbids
`anthropic` / `anthropic_client` (the LLM surface is gone) AND `graph_client` /
`send_mail` / `resend` / `smtplib` / `email.mime` (no external send). Box egress is
the audited `shared.box_client` (boxsdk, not on the F02 network-needle list).

Adversarial Input Handling (Invariant 2)
----------------------------------------
The compile is deterministic over already-rendered, already-HMAC-verified PDFs +
typed Smartsheet cells — there is NO LLM step, so Layer-2 untrusted-content tagging
is N/A here (it lived on the retired narrative call). The submissions were validated +
HMAC-verified upstream by `portal_poll` + `intake.process_portal_submission`.
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from safety_reports import form_pdf, safety_naming, week_sheet, wsr_review
from shared import (
    active_jobs,
    box_client,
    error_log,
    project_routing,
    review_queue,
    safety_week,
    smartsheet_client,
)
from shared.active_jobs import ActiveJob
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active
from shared.smartsheet_client import SmartsheetError

SCRIPT_NAME = "safety_reports.weekly_generate"
WORKSTREAM = "safety_reports"

# ITS_Config: the Evergreen contact named in the seed email body (deploy-tunable).
CFG_EVERGREEN_CONTACT = "safety_reports.evergreen_contact_name"
DEFAULT_EVERGREEN_CONTACT = "the Evergreen Renewables office"

DEFAULT_TZ = "America/Los_Angeles"  # everything Pacific (Brief v6.1)

# Watchdog Check C marker — same pattern as the other daemons (preservation, §14).
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "safety_weekly_generate"

# Box link shape produced by intake (`https://app.box.com/file/<id>`).
_BOX_FILE_LINK_RE = re.compile(r"/file/(\d+)")


@dataclass
class RunSummary:
    """Per-run counters returned from main(); logged via @its_error_log."""
    jobs_processed: int = 0
    packets_compiled: int = 0
    skipped_no_change: int = 0
    empty_weeks: int = 0
    wsr_written: int = 0
    review_queue_entries: int = 0
    download_errors: int = 0
    errors_per_job: dict[str, str] = field(default_factory=dict)


# ---- Config reader (replicated per preservation) ------------------------


def _read_str_setting(key: str, fallback: str) -> str:
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    except smartsheet_client.SmartsheetCircuitOpenError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _now_pacific_iso() -> str:
    return datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat()


# ---- Box helpers ---------------------------------------------------------


def _box_file_id(link: str) -> str | None:
    """Parse the Box file id from a `https://app.box.com/file/<id>` link."""
    m = _BOX_FILE_LINK_RE.search(link or "")
    return m.group(1) if m else None


def _its_week_folder_name(week: safety_week.SafetyWeek) -> str:
    """ITS-prefixed Box folder for the week's compiled packet (operator naming rule)."""
    return f"ITS Week of {week.start.isoformat()} to {week.end.isoformat()}"


def _packet_filename(project_name: str, week: safety_week.SafetyWeek, stamp: str) -> str:
    """Per-COMPILATION packet filename — unique per compile (`stamp` = compiled-at
    YYYYMMDD-HHMMSS). APPEND-ONLY (operator decision 2026-06-09): each compilation files a
    DISTINCT Box file, so a recompile NEVER overwrites the prior packet (Box is the master
    record and must keep every weekly compilation). The unique name also means a plain
    `upload_bytes` cannot 409 — the reason the prior code used upload_bytes_or_new_version
    (which buried prior packets as file versions) is gone."""
    return (
        f"Weekly Safety Report — {project_name} — "
        f"{week.start.isoformat()} to {week.end.isoformat()} — {stamp}.pdf"
    )


def _gather_submission_pdfs(
    submission_rows: list[dict[str, Any]], summary: RunSummary, correlation_id: str
) -> tuple[list[bytes], list[str]]:
    """Download each submission's per-submission PDF from Box (by its sheet-recorded
    link). Returns (pdf_bytes_ordered, manifest_parts). A row with no link or a
    failed download is SKIPPED + announced (never silently dropped) — the manifest
    records the gap so the operator can see the packet is short."""
    pdfs: list[bytes] = []
    manifest: list[str] = []
    for row in submission_rows:
        form_code = str(row.get(week_sheet.COL_FORM_CODE) or "?")
        link = str(row.get(week_sheet.COL_SUBMISSION_PDF) or "")
        file_id = _box_file_id(link)
        if not file_id:
            summary.download_errors += 1
            manifest.append(f"{form_code}[no-box-link]")
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"compile: submission row has no Box link (form={form_code}); excluded from packet",
                error_code="weekly_generate.submission_no_link",
                correlation_id=correlation_id,
            )
            continue
        try:
            pdfs.append(box_client.download_file(file_id))
            manifest.append(form_code)
        except box_client.BoxError as exc:
            summary.download_errors += 1
            manifest.append(f"{form_code}[download-failed]")
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"compile: Box download failed for file {file_id} (form={form_code}): {exc!r}; excluded",
                error_code="weekly_generate.submission_download_failed",
                correlation_id=correlation_id,
            )
    return pdfs, manifest


# ---- Recipient display ---------------------------------------------------


def _recipient_display(job: ActiveJob) -> tuple[str, str]:
    """(TO display, CC display) from active_jobs — DISPLAY only; weekly_send
    re-resolves authoritatively at send time."""
    return job.safety_reports_contact_email, ", ".join(job.cc_emails)


# ---- Watchdog marker (replicated inline per preservation) ----------------


def _write_watchdog_marker() -> None:
    """Touch the Check C freshness marker for this run."""
    try:
        WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        marker = WATCHDOG_MARKER_DIR / f"{WATCHDOG_JOB_SLUG}.last_run"
        marker.write_text(datetime.now(UTC).isoformat())
    except OSError as exc:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"watchdog marker write failed: {exc!r}",
            error_code="watchdog_marker_failed",
        )


# ---- Per-(job, week) compile --------------------------------------------


def _compile_job_week(
    job: ActiveJob,
    week: safety_week.SafetyWeek,
    summary: RunSummary,
    correlation_id: str,
    *,
    selection: set[int] | None = None,
) -> None:
    """Compile one (job, week): merge → file → dual-write Rollup + WSR.

    `selection` (Part B / on-demand compile) narrows the PACKET to the given submission row
    IDs; None (the Friday run + a default Compile Now) = the full week. The narrowing applies
    to the merged packet only — the no-new-docs skip below still reads the FULL set (a
    selection narrows what is compiled, not whether anything changed). Behaviour is IDENTICAL
    to before when selection is None.

    Raises SmartsheetError / BoxError on transient infra failure (the per-job fence
    in _run_pipeline catches + routes to the Review Queue). A brand-new job
    self-provisions its per-job folder + week sheet under the Safety Portal
    workspace (find-or-create), so there is no per-project-folder config gap."""
    project_name = job.project_name
    sheet_id = week_sheet.ensure_week_sheet(project_name, week.start)
    # APPEND-ONLY (operator decision 2026-06-09): there can be MANY Rollup snapshots (one per
    # compile). `rollup` = the LATEST (the no-new-docs watermark); `force` = Compile Now on
    # ANY Rollup row (the trigger may sit on the latest or an older snapshot).
    rollup_rows = week_sheet.list_rollup_rows(sheet_id)
    rollup = rollup_rows[-1] if rollup_rows else None
    submissions = week_sheet.list_submission_rows(sheet_id, active_only=True)
    force = week_sheet.any_compile_now_requested(rollup_rows)

    # SKIP-if-already-compiled-and-no-new-docs (unless Compile Now forces it).
    if rollup is not None and not force:
        prior_compiled_at = str(rollup.get(week_sheet.COL_SUBMITTED_AT) or "")
        newest = week_sheet.latest_submitted_at(submissions)
        if not newest and submissions:
            # Submissions exist but NONE has a usable Submitted At — we cannot prove
            # "no new docs", so RECOMPILE (never silently skip) + WARN loudly. Guards
            # the silent-loss case where intake wrote a row without a timestamp.
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"compile: {project_name} week {week.start} has submissions with missing/blank "
                f"Submitted At — forcing recompile (cannot prove no-new-docs)",
                error_code="weekly_generate.missing_submitted_at",
                correlation_id=correlation_id,
            )
        elif newest <= prior_compiled_at:
            summary.skipped_no_change += 1
            error_log.log(
                Severity.INFO, SCRIPT_NAME,
                f"compile: {project_name} week {week.start} already compiled, no new docs — skip",
                error_code="weekly_generate.skip_no_change",
                correlation_id=correlation_id,
            )
            return

    # Part B: narrow the packet to the explicit selection (default-all when None). Placed
    # AFTER the skip check, which intentionally read the full set.
    if selection is not None:
        submissions = [s for s in submissions if int(s.get("_row_id") or 0) in selection]

    # One compiled-at instant for this compilation → the Submitted At watermark AND the
    # unique packet-filename stamp (append-only: a distinct Box file per compile). The
    # correlation_id suffix (per-run uuid4) guarantees uniqueness even if two compiles of the
    # same (job, week) land in the same wall-clock second across runs — so a distinct file is
    # ALWAYS created, never a same-name 409.
    compiled_dt = datetime.now(ZoneInfo(DEFAULT_TZ))
    compiled_at = compiled_dt.isoformat()
    stamp = f"{compiled_dt.strftime('%Y%m%d-%H%M%S')}-{correlation_id[:6]}"

    if not submissions:
        # EMPTY WEEK — still write the Rollup + WSR row (never silently skip). No
        # packet PDF; weekly_send HELDs a WSR row with no Compiled PDF.
        summary.empty_weeks += 1
        week_sheet.append_rollup_row(
            sheet_id, packet_link="", compiled_at=compiled_at,
            manifest_note="0 submissions this week (empty-week placeholder)",
        )
        week_sheet.clear_compile_now_on_rollups(sheet_id, rollup_rows)
        _write_wsr(job, week, packet_link="", manifest="no submissions this week",
                   summary=summary, correlation_id=correlation_id)
        return

    pdfs, manifest_parts = _gather_submission_pdfs(submissions, summary, correlation_id)
    packet_link = ""
    compiled: bytes | None = None
    if pdfs:
        compiled = form_pdf.merge_pdfs(pdfs)
        folder_id = _ensure_its_week_folder(project_name, week, correlation_id)
        # APPEND-ONLY (operator decision 2026-06-09): each compilation files a DISTINCT,
        # timestamped packet, so a recompile NEVER overwrites the prior packet — Box is the
        # master record and must keep every weekly compilation as its own file. The unique
        # name also means a plain `upload_bytes` can't 409 (the reason the prior code used
        # upload_bytes_or_new_version — which buried prior packets as file versions — is gone).
        meta = box_client.upload_bytes(
            folder_id, _packet_filename(project_name, week, stamp), compiled
        )
        packet_link = f"https://app.box.com/file/{meta['id']}"
        # Count ONLY a real packet (≥1 PDF merged + uploaded). The all-failed
        # branch below still dual-writes the Rollup/WSR rows but is NOT a packet.
        summary.packets_compiled += 1
    else:
        # All submissions failed to download — already WARN-logged per row.
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"compile: {project_name} week {week.start} had {len(submissions)} submissions "
            f"but ZERO downloadable PDFs — WSR row written without a packet",
            error_code="weekly_generate.no_downloadable_pdfs",
            correlation_id=correlation_id,
        )

    manifest_note = (
        f"{len(submissions)} submissions ({len(pdfs)} in packet): "
        f"{', '.join(manifest_parts)}; compiled {compiled_at}"
    )
    rollup_row_id = week_sheet.append_rollup_row(
        sheet_id, packet_link=packet_link, compiled_at=compiled_at,
        manifest_note=manifest_note,
    )
    week_sheet.clear_compile_now_on_rollups(sheet_id, rollup_rows)
    wsr_row_id = _write_wsr(job, week, packet_link=packet_link, manifest=manifest_note,
                            summary=summary, correlation_id=correlation_id)

    # Supplementary: attach the compiled packet inline on the Rollup row (the
    # week-sheet preview) + the WSR_human_review row (the approve/send surface), so
    # a reviewer sees the packet without a Box round-trip. Box stays the SoR (the
    # Compiled-PDF link cells are unchanged). Best-effort + only when a real packet
    # exists (an empty / all-downloads-failed week has no packet to attach).
    if compiled is not None:
        packet_name = _packet_filename(project_name, week, stamp)
        _attach_pdf_best_effort(sheet_id, rollup_row_id, packet_name, compiled, correlation_id)
        _attach_pdf_best_effort(wsr_review.SHEET_ID, wsr_row_id, packet_name, compiled, correlation_id)


def _portal_box_root() -> str:
    """The Box "ITS Safety Portal" root folder ID (ITS_Config, config-GATED, PR-K).

    Blank/unset → the mirror tree is OFF; the packet files into the legacy
    `project_routing` → ITS-prefixed week folder (so pulling PR-K is inert). The
    operator sets `safety_naming.CFG_BOX_PORTAL_ROOT` to activate. Mirrors
    `intake._portal_box_root` (same config key → same root for both writers).
    """
    return _read_str_setting(safety_naming.CFG_BOX_PORTAL_ROOT, "").strip()


def _ensure_its_week_folder(
    project_name: str, week: safety_week.SafetyWeek, correlation_id: str
) -> str:
    """Resolve the Box folder the compiled packet files into.

    MIRROR-TREE path (PR-K, when `_portal_box_root()` is set): the SAME
    `ROOT → per-job folder → per-week folder` tree the per-submission PDFs land in
    (`safety_naming.job_folder_name` / `week_label`) — the packet is a sibling of the
    week's submission PDFs, mirroring the week sheet holding submission rows + the
    rollup. LEGACY path (gated OFF): `project_routing` root → ITS-prefixed week folder.
    """
    portal_root = _portal_box_root()
    if portal_root:
        job_folder = box_client.get_or_create_folder(
            portal_root, safety_naming.job_folder_name(project_name)
        )
        return box_client.get_or_create_folder(
            job_folder, safety_naming.week_label(week.start)
        )
    root = project_routing.get_folder_id(project_name)
    if not root:
        # Surfaced to the fence → Review Queue (a config gap, not a silent skip).
        raise box_client.BoxError(
            f"no Box root for project {project_name!r} (project_routing unresolved)"
        )
    return box_client.get_or_create_folder(root, _its_week_folder_name(week))


def _write_wsr(
    job: ActiveJob,
    week: safety_week.SafetyWeek,
    *,
    packet_link: str,
    manifest: str,
    summary: RunSummary,
    correlation_id: str,
) -> int:
    """Dual-write (b): upsert the WSR_human_review row for (job, week); return its row ID."""
    to_display, cc_display = _recipient_display(job)
    evergreen = _read_str_setting(CFG_EVERGREEN_CONTACT, DEFAULT_EVERGREEN_CONTACT)
    body = wsr_review.email_body_template(
        contact_name=job.safety_reports_contact_name,
        week_label=week.label,
        job_name=job.project_name,
        evergreen_contact=evergreen,
    )
    _row_id = wsr_review.add_wsr_row(
        wsr_review.SHEET_ID,
        job_project=job.project_name,
        job_id=job.job_id,
        week_of=week.start,
        compiled_pdf_link=packet_link,
        recipient_to=to_display,
        cc_display=cc_display,
        email_body=body,
        notes=manifest,
    )
    summary.wsr_written += 1
    if not to_display:
        # No safety-reports contact on the job → weekly_send will HELD it. Surface now.
        # (Append-only: this fires per compilation that lacks a TO — each unsendable
        # compilation row is surfaced; compiles only happen on genuinely new content.)
        review_queue.add(
            workstream=WORKSTREAM,
            summary=f"weekly compile: job {job.job_id} ({job.project_name}) has no safety-reports contact (TO) for week {week.start}",
            payload={"job_id": job.job_id, "project": job.project_name, "week": week.start.isoformat()},
            sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
            reason=review_queue.ReviewReason.OTHER,
            severity=Severity.WARN,
            source_file=f"{job.job_id}-{week.start.isoformat()}",
        )
        summary.review_queue_entries += 1
    return _row_id


def _attach_pdf_best_effort(
    sheet_id: int, row_id: int, filename: str, pdf_bytes: bytes, correlation_id: str
) -> None:
    """Attach the compiled packet inline on a Smartsheet row, BEST-EFFORT.

    Box is the System of Record (the row's Compiled-PDF link is unchanged); this
    inline copy is supplementary, so a failure is a WARN (logged, not silent) that
    NEVER fails the compile."""
    try:
        smartsheet_client.attach_pdf_to_row(sheet_id, row_id, filename, pdf_bytes)
    except Exception as exc:  # noqa: BLE001 — supplementary inline copy; Box is the SoR
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"row PDF attach failed (row {row_id}, {filename!r}): {type(exc).__name__}: {exc!r}",
            error_code="row_pdf_attach_failed", correlation_id=correlation_id,
        )


# ---- main + CLI ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def main(week_start_override: date | None = None) -> dict[str, Any]:
    """Compile weekly packets + dual-write Rollup/WSR for each Active job.

    Args:
        week_start_override: any date inside the target Sat→Fri week (backfill).
            Defaults to the week containing today (Friday run → the just-closed week).
    """
    return _run_pipeline(week_start_override=week_start_override)


def _run_pipeline(*, week_start_override: date | None) -> dict[str, Any]:
    correlation_id = uuid.uuid4().hex[:12]
    summary = RunSummary()

    anchor = week_start_override if week_start_override is not None else datetime.now(
        ZoneInfo(DEFAULT_TZ)
    ).date()
    week = safety_week.week_bounds(anchor)

    for job in active_jobs.list_active_jobs():
        summary.jobs_processed += 1
        try:
            _compile_job_week(job, week, summary, correlation_id)
        except (SmartsheetError, box_client.BoxError) as exc:
            summary.errors_per_job[job.project_name] = f"{type(exc).__name__}: {exc!r}"
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"compile failed for {job.project_name} (job {job.job_id}) week {week.start}: {exc!r}",
                error_code="weekly_generate.compile_failed",
                correlation_id=correlation_id,
            )
            _safe_review_queue(job, week, type(exc).__name__, correlation_id, summary)
        except Exception as exc:  # noqa: BLE001 — per-job fence; one bad job never kills the run
            summary.errors_per_job[job.project_name] = f"{type(exc).__name__}: {exc!r}"
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"unexpected compile error for {job.project_name}: {exc!r}",
                error_code="weekly_generate.compile_unexpected",
                correlation_id=correlation_id,
            )
            _safe_review_queue(job, week, type(exc).__name__, correlation_id, summary)

    _write_watchdog_marker()
    return {
        **summary.__dict__,
        "week_start": week.start.isoformat(),
        "week_end": week.end.isoformat(),
        "correlation_id": correlation_id,
    }


def _safe_review_queue(
    job: ActiveJob, week: safety_week.SafetyWeek, error_class: str,
    correlation_id: str, summary: RunSummary,
) -> None:
    """Surface a per-job compile failure to the Review Queue (never silent). Defensive
    outer catch so a Review-Queue write failure can't tear down the remaining jobs."""
    try:
        review_queue.add(
            workstream=WORKSTREAM,
            summary=f"weekly compile failed for {job.project_name} (job {job.job_id}) week {week.start} ({error_class})",
            payload={
                "job_id": job.job_id, "project": job.project_name,
                "week": week.start.isoformat(), "error_class": error_class,
                "correlation_id": correlation_id,
            },
            sla_tier=review_queue.SlaTier.SAFETY_INTAKE,
            reason=review_queue.ReviewReason.OTHER,
            severity=Severity.ERROR,
            source_file=f"{job.job_id}-{week.start.isoformat()}",
        )
        summary.review_queue_entries += 1
    except Exception as exc:  # noqa: BLE001 — defensive outer catch
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"failed to write Review-Queue entry for {job.project_name}: {exc!r}",
            error_code="weekly_generate.review_queue_failed",
            correlation_id=correlation_id,
        )


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="safety_reports.weekly_generate",
        description="Compile weekly safety packets + dual-write Rollup/WSR for each Active job.",
    )
    parser.add_argument(
        "--week-start", type=lambda s: date.fromisoformat(s), default=None,
        help="Any date inside the target Sat→Fri week (backfill). Defaults to the week containing today.",
    )
    args = parser.parse_args(argv)
    main(week_start_override=args.week_start)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
