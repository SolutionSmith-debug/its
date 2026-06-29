"""Safety Reports weekly compile — DETERMINISTIC weekly packet + WSR dual-write.

Generation half of the External Send Gate two-process model (Foundation Mission v11
Invariant 1) for the Safety Portal pull flow. **Zero send capability. Zero AI.**

Phase-5 rewrite (2026-06-05): the legacy WPR flow drafted a per-project narrative via
Anthropic and wrote `WPR_Pending_Review`. The portal flow is DETERMINISTIC — there is
no narrative to draft and no LLM call. This module now COMPILES: for each Active job's
Saturday→Friday week it gathers the per-submission PDFs recorded on the week sheet,
merges them (`form_pdf.merge_pdfs`) into one weekly packet, files the packet to an
`ITS`-prefixed Box week folder as a DISTINCT, version-numbered file (`<Job>_week of
<Sat>_WSR.pdf`; recompiles bump `_v2`/`_v3`…), and APPENDS (operator decision
2026-06-09 — append-only, never overwrite; naming refined 2026-06-17):
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

from safety_reports import compile_core, form_pdf, safety_naming, week_sheet, wsr_review
from shared import (
    active_jobs,
    box_client,
    compile_mutex,
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

# A6 single-host hardening (Stage-0): the per-job wall-clock budget + the pre-merge memory
# ceiling, both deploy-tunable via ITS_Config. Defaults are GENEROUS — a hung SDK call or a
# pathological week is the target, never normal operation — so the happy path is byte-identical
# (the fences are no-ops below these bounds). See safety_reports.compile_core.
CFG_JOB_TIMEOUT = "safety_reports.weekly_generate.job_timeout_seconds"
DEFAULT_JOB_TIMEOUT = 600  # 10 min/job — one hung Box/Smartsheet call can't block the whole run
CFG_MEMORY_CEILING = "safety_reports.weekly_generate.merge_memory_ceiling_bytes"
DEFAULT_MEMORY_CEILING = 256 * 1024 * 1024  # 256 MiB of gathered source PDFs before merge

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
    review_queue_entries: int = 0  # "fenced" jobs (any per-job failure routed to Review Queue)
    timed_out: int = 0  # A6: jobs killed by the per-job SIGALRM wall-clock budget
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


def _read_int_setting(key: str, fallback: int) -> int:
    """Defensive int ITS_Config read, layered on `_read_str_setting` (so a missing row /
    circuit-open / non-int value all resolve to `fallback`, never raising into the compile).
    Reuses `_read_str_setting` deliberately — it is the one config seam the tests already mock."""
    try:
        return int(_read_str_setting(key, str(fallback)).strip())
    except (TypeError, ValueError):
        return fallback


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


_MAX_PACKET_VERSIONS = 200  # bound the recompile _vN probe; an absurd ceiling (real weeks see ≤ a handful)


def _packet_basename(project_name: str, week: safety_week.SafetyWeek) -> str:
    """Clean per-(job, week) packet base name (no version, no extension):
    `<Job>_week of <Sat>_WSR`. Operator naming rule (2026-06-17): job-prefixed +
    week-of-<Saturday> + `WSR` tag, so every packet is self-identifying and never collides
    across jobs or weeks. Reuses `safety_naming` so the prefix + week key match the
    Box/Smartsheet folder naming exactly. Recompiles of the SAME (job, week) get a
    `_v2`/`_v3`… suffix in `_upload_packet` — each compile is still a DISTINCT Box file
    (append-only master record), just with clean version-numbered names instead of the old
    compiled-at timestamp."""
    return (
        f"{safety_naming.job_folder_name(project_name)}_"
        f"{safety_naming.week_label(week.start)}_WSR"
    )


def _upload_packet(
    folder_id: str, basename: str, compiled: bytes, stamp: str
) -> tuple[str, str]:
    """Upload the compiled packet as a DISTINCT file with a clean, version-numbered name;
    return `(filename, box_file_id)`. First compile → `<basename>.pdf`; each recompile of
    the SAME (job, week) → `<basename>_v2.pdf`, `_v3.pdf`, … (the next name not already in
    the Box week folder, found by trying in order and catching the 409). APPEND-ONLY: a
    recompile NEVER overwrites a prior packet — Box is the master record. `stamp`
    (compiled-at) is the last-resort disambiguator if the version ceiling is ever hit, so a
    compile can NEVER silently lose a packet to an unresolved 409."""
    for n in range(1, _MAX_PACKET_VERSIONS + 1):
        name = f"{basename}.pdf" if n == 1 else f"{basename}_v{n}.pdf"
        try:
            meta = box_client.upload_bytes(folder_id, name, compiled)
            return name, str(meta["id"])
        except box_client.BoxConflictError:
            continue
    name = f"{basename}_{stamp}.pdf"  # ceiling hit (never expected) — fall back, never lose the packet
    meta = box_client.upload_bytes(folder_id, name, compiled)
    return name, str(meta["id"])


def _form_display_name(form_code: str) -> str:
    """Human form name for the weekly index, resolved from the form definition (the
    same source the renderer uses). Falls back to the raw code if unresolvable —
    deterministic, no network (load_definition reads a committed JSON file)."""
    try:
        definition = form_pdf.load_definition(form_code)
    except Exception:  # noqa: BLE001 — index naming must never break the compile
        definition = None
    if not definition:
        return form_code
    # form_name is already variant-distinct (e.g. "Equipment Pre-Inspection — Skid Steer"),
    # so it is NOT re-suffixed with variant_label (avoids "… — Telehandler/Forklift" tails).
    name = definition.get("form_name") or definition.get("branding", {}).get("title") or form_code
    return str(name)


def _work_date_display(iso: str) -> str:
    """'2026-06-12' → 'Fri, Jun 12, 2026' for the date-grouped index. Falls back to the
    raw string if it isn't a parseable ISO date."""
    try:
        return date.fromisoformat(iso[:10]).strftime("%a, %b %-d, %Y")
    except (ValueError, TypeError):
        return iso or "—"


def _gather_submission_pdfs(
    submission_rows: list[dict[str, Any]], summary: RunSummary, correlation_id: str
) -> tuple[list[bytes], list[str], list[dict[str, Any]]]:
    """Download each submission's per-submission PDF from Box (by its sheet-recorded
    link). Returns (pdf_bytes_ordered, manifest_parts, metas) where `metas` is aligned
    1:1 with `pdfs` (only the rows that made it into the packet) and carries the
    date/form-name used to build the weekly index. A row with no link or a failed
    download is SKIPPED + announced (never silently dropped) — the manifest records the
    gap so the operator can see the packet is short."""
    pdfs: list[bytes] = []
    manifest: list[str] = []
    metas: list[dict[str, Any]] = []
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
            metas.append({
                "date_display": _work_date_display(str(row.get(week_sheet.COL_WORK_DATE) or "")),
                "form_name": _form_display_name(form_code),
            })
        except box_client.BoxError as exc:
            summary.download_errors += 1
            manifest.append(f"{form_code}[download-failed]")
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"compile: Box download failed for file {file_id} (form={form_code}): {exc!r}; excluded",
                error_code="weekly_generate.submission_download_failed",
                correlation_id=correlation_id,
            )
    return pdfs, manifest, metas


# ---- Weekly packet front matter (branded cover + date-grouped index) ------


def _week_label(week: safety_week.SafetyWeek) -> str:
    """'Week of Jun 7 – 13, 2026' (collapsed when start/end share a month/year)."""
    s, e = week.start, week.end
    if s.year == e.year and s.month == e.month:
        return f"Week of {s.strftime('%b %-d')} – {e.strftime('%-d, %Y')}"
    if s.year == e.year:
        return f"Week of {s.strftime('%b %-d')} – {e.strftime('%b %-d, %Y')}"
    return f"Week of {s.strftime('%b %-d, %Y')} – {e.strftime('%b %-d, %Y')}"


def _build_weekly_packet(
    project_name: str,
    week: safety_week.SafetyWeek,
    pdfs: list[bytes],
    metas: list[dict[str, Any]],
    compiled_dt: datetime,
    correlation_id: str,
) -> bytes:
    """Assemble the compiled weekly packet: a branded COVER page + a date-grouped
    CONTENTS index + the per-submission PDFs (in the Sat→Fri packet order). The index
    page numbers are ABSOLUTE packet pages (they match a PDF viewer's page counter), so
    the index page count is resolved by iterating render→measure until it is stable.

    FENCED: any front-matter failure degrades to the prior behaviour — a plain
    forms-only `merge_pdfs(pdfs)` — so beautification can NEVER break the live compile/
    send path. merge_pdfs itself stays a pure concatenation."""
    try:
        week_label = _week_label(week)
        compiled_display = compiled_dt.strftime("%b %-d, %Y %-I:%M %p %Z").strip()
        cover = form_pdf.render_weekly_cover(
            project_name, week_label, len(pdfs), compiled_display=compiled_display
        )
        cover_pages = form_pdf.page_count(cover)
        counts = [form_pdf.page_count(b) for b in pdfs]

        def make_index(index_pages: int) -> bytes:
            cur = cover_pages + index_pages + 1  # first form's 1-based packet page
            entries: list[dict[str, Any]] = []
            for m, c in zip(metas, counts, strict=True):
                entries.append({"date_display": m.get("date_display", ""),
                                "form_name": m.get("form_name", ""), "start_page": cur})
                cur += c
            return form_pdf.render_weekly_index(project_name, week_label, entries)

        # Resolve the index's own page count: render→measure until the page count the
        # start-page maths assumed equals the page count actually produced.
        index_pages = 1
        index_pdf = make_index(index_pages)
        for _ in range(4):
            actual = form_pdf.page_count(index_pdf)
            if actual == index_pages:
                break
            index_pages = actual
            index_pdf = make_index(index_pages)
        else:
            raise RuntimeError("weekly index pagination did not converge")
        return form_pdf.merge_pdfs([cover, index_pdf, *pdfs])
    except Exception as exc:  # noqa: BLE001 — front matter must never break the compile
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"compile: weekly cover/index build failed ({exc!r}); packet falls back to forms-only",
            error_code="weekly_generate.front_matter_failed",
            correlation_id=correlation_id,
        )
        return form_pdf.merge_pdfs(pdfs)


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
    memory_ceiling: int = 0,
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
        # Commit-point ordering (A6, §42): the WSR row FIRST, the Rollup row (the no-new-docs
        # watermark) LAST. A crash before the watermark recompiles next run rather than leaving
        # an empty week "compiled" but with no WSR row for weekly_send to HELD.
        _write_wsr(job, week, packet_link="", manifest="no submissions this week",
                   summary=summary, correlation_id=correlation_id)
        week_sheet.append_rollup_row(
            sheet_id, packet_link="", compiled_at=compiled_at,
            manifest_note="0 submissions this week (empty-week placeholder)",
        )
        week_sheet.clear_compile_now_on_rollups(sheet_id, rollup_rows)
        return

    pdfs, manifest_parts, metas = _gather_submission_pdfs(submissions, summary, correlation_id)
    # A6 memory guard (§42): fence an oversized week BEFORE merge_pdfs (which ~doubles peak
    # memory) so a pathological week is routed to the Review Queue rather than OOMing the host.
    # No-op on a normal week (total << ceiling) → byte-identical happy path. A breach raises
    # compile_core.CompileMemoryExceededError → the per-job fence in run_per_job routes it to Review.
    compile_core.enforce_memory_budget((len(p) for p in pdfs), memory_ceiling)
    packet_link = ""
    packet_name = ""
    compiled: bytes | None = None
    if pdfs:
        compiled = _build_weekly_packet(project_name, week, pdfs, metas, compiled_dt,
                                        correlation_id)
        folder_id = _ensure_its_week_folder(project_name, week, correlation_id)
        # APPEND-ONLY (operator decision 2026-06-09, naming refined 2026-06-17): each
        # compilation files a DISTINCT Box file, so a recompile NEVER overwrites the prior
        # packet — Box is the master record and must keep every weekly compilation as its own
        # file. The name is the clean `<Job>_week of <Sat>_WSR.pdf`; recompiles of the same
        # (job, week) bump `_v2`/`_v3`… (see _upload_packet), replacing the old compiled-at
        # timestamp while keeping the append-only / never-overwrite guarantee.
        packet_name, file_id = _upload_packet(
            folder_id, _packet_basename(project_name, week), compiled, stamp
        )
        packet_link = f"https://app.box.com/file/{file_id}"
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
    # Commit-point ordering (A6 resumable watermark, §42): write the WSR row FIRST, then the
    # Rollup snapshot row (whose compiled_at is the no-new-docs watermark the next run reads)
    # LAST. A crash mid-compile then leaves NO advanced watermark, so the (job, week) recompiles
    # next run (a visible duplicate PENDING WSR row, caught at human approval) instead of being
    # SILENTLY skipped with no WSR row ever written. The Box packet is already filed (append-only
    # distinct file), so a WSR row never points at a missing packet. Happy-path output is
    # unchanged — same rows, same content; only the write order hardens the crash path.
    wsr_row_id = _write_wsr(job, week, packet_link=packet_link, manifest=manifest_note,
                            summary=summary, correlation_id=correlation_id)
    rollup_row_id = week_sheet.append_rollup_row(
        sheet_id, packet_link=packet_link, compiled_at=compiled_at,
        manifest_note=manifest_note,
    )
    week_sheet.clear_compile_now_on_rollups(sheet_id, rollup_rows)

    # Supplementary: attach the compiled packet inline on the Rollup row (the
    # week-sheet preview) + the WSR_human_review row (the approve/send surface), so
    # a reviewer sees the packet without a Box round-trip. Box stays the SoR (the
    # Compiled-PDF link cells are unchanged). Best-effort + only when a real packet
    # exists (an empty / all-downloads-failed week has no packet to attach).
    if compiled is not None:
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

    # A6: per-run config for the single-host fences (defensive int reads → defaults in tests).
    job_timeout = _read_int_setting(CFG_JOB_TIMEOUT, DEFAULT_JOB_TIMEOUT)
    memory_ceiling = _read_int_setting(CFG_MEMORY_CEILING, DEFAULT_MEMORY_CEILING)

    def _start(job: ActiveJob) -> None:
        summary.jobs_processed += 1

    def _record(job: ActiveJob, error_class: str, exc: BaseException) -> None:
        summary.errors_per_job[job.project_name] = f"{error_class}: {exc!r}"

    def _on_timeout(job: ActiveJob, error_class: str, exc: BaseException) -> None:
        # A6: a per-job SIGALRM fence fired — surface it (never a silent hang), then continue.
        summary.timed_out += 1
        _record(job, error_class, exc)
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"compile timed out for {job.project_name} (job {job.job_id}) week {week.start}: {exc!r}",
            error_code="weekly_generate.compile_timeout",
            correlation_id=correlation_id,
        )
        _safe_review_queue(job, week, error_class, correlation_id, summary)

    def _on_infra(job: ActiveJob, error_class: str, exc: BaseException) -> None:
        _record(job, error_class, exc)
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"compile failed for {job.project_name} (job {job.job_id}) week {week.start}: {exc!r}",
            error_code="weekly_generate.compile_failed",
            correlation_id=correlation_id,
        )
        _safe_review_queue(job, week, error_class, correlation_id, summary)

    def _on_unexpected(job: ActiveJob, error_class: str, exc: BaseException) -> None:
        # Catches CompileMemoryExceededError (A6 memory fence) + any other unexpected per-job error.
        _record(job, error_class, exc)
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"unexpected compile error for {job.project_name}: {exc!r}",
            error_code="weekly_generate.compile_unexpected",
            correlation_id=correlation_id,
        )
        _safe_review_queue(job, week, error_class, correlation_id, summary)

    # Instantiate the shared hardened core (A6): the per-job SIGALRM budget + per-job error
    # fence live ONCE in compile_core; the future progress compile re-instantiates the same
    # loop. one bad job never kills the run — semantics identical to the prior inline loop.
    #
    # P4-core: serialize against any concurrent (future progress) compile on the host-level
    # mutex so the two never contend on the Smartsheet rate limit. FAIL-OPEN for safety — we
    # ignore the acquired flag and run REGARDLESS; on contention hold() logs a single WARN
    # (compile_mutex.contended) and we proceed UNLOCKED, because blocking the live-critical
    # Friday compile is worse than a rare contention window (A3 precedent).
    with compile_mutex.hold(role="safety"):
        compile_core.run_per_job(
            active_jobs.list_active_jobs(),
            lambda job: _compile_job_week(
                job, week, summary, correlation_id, memory_ceiling=memory_ceiling
            ),
            fences=compile_core.JobFences(
                on_timeout=_on_timeout,
                on_infra_error=_on_infra,
                on_unexpected=_on_unexpected,
                infra_errors=(SmartsheetError, box_client.BoxError),
            ),
            job_timeout_seconds=job_timeout,
            on_job_start=_start,
        )

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
