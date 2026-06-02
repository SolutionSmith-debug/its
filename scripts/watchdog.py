"""ITS daily watchdog — runs every morning at 7:00 AM via launchd.

Verifies operational state via per-check probes. Silent if green; surfaces
WARN / CRITICAL through `shared.error_log.log()` (which fans out to the
local log file, ITS_Errors, and the Resend/Sentry legs on CRITICAL).

Kill-switch semantics (Op Stds v9 §2):
    ACTIVE      — run all checks normally.
    MAINTENANCE — run all checks; alerts suppressed (WARN/CRITICAL results
                  downgraded to INFO before routing, so the Smartsheet row
                  still lands but Resend/Sentry legs never fire).
    PAUSED      — skip all checks; single INFO line "PAUSED — skipping".

Failure isolation (Op Stds v9 §27):
    Each check runs inside `_run_check`, which catches `Exception` and emits
    a distinguishable marker line through `error_log.log` at ERROR severity.
    A failure in one check does NOT prevent later checks from running. The
    harness itself writes to no side channel — `error_log.log()` has its
    own three recursion guards (Smartsheet, Resend, Sentry), so the harness
    does NOT need a separate recursion guard.

Checks shipped:
    A. Stale ITS_Review_Queue items (PENDING past 2× SLA) — WARN. Session 1.
    B. Open CRITICAL ITS_Errors rows (Resolved At blank) — WARN. Session 1.
    C. Scheduled-jobs last-run via marker files — Session 2+. Each entry in
       TRACKED_JOBS must have written a {slug}.last_run marker within its
       freshness window (default 24h; per-job overrides in
       TRACKED_JOB_WINDOWS). Tracked today: safety_weekly_generate,
       safety_weekly_send_poll, safety_picklist_audit, and safety_intake
       (the 60s customer-facing intake poller). A missing or stale marker
       is a WARN. The watchdog's own run-marker is intentionally NOT in
       TRACKED_JOBS — a daemon can't reliably detect its own death; that's
       the external heartbeat observer's job (see main()).
    D. 14-day reviewer-chain forward scan — Session 2. Logs an INFO ANOMALY
       row to ITS_Review_Queue per workstream with reviewer-chain gaps in
       the next 14 days (Op Stds v9 §18).
    F. Mail.app rule silent-disable inbound-mail activity check —
       Session 2. WARN when a tracked mailbox is idle beyond its per-
       workstream `mail_intake.<workstream>.max_idle_hours` threshold
       (per `docs/tech_debt.md` Mail.app entry added 2026-05-19).
    G. Alert-routing dedupe summary sweep — Session 3 (PR β). For each
       expired entry in `~/its/state/alert_dedupe.json`, fire a single
       operator summary email naming what was suppressed during the
       window, mark the entry, and delete it on the next sweep
       (two-phase deletion for crash safety). Summary emails are a
       Resend-only push notification — they do NOT write to ITS_Errors
       or Sentry (no new forensic data; the rows already exist).

       MAINTENANCE behavior: Check G receives `alerts_suppressed` via
       signature inspection in `_run_check` and DEFERS phase-1 summary
       firing (no Resend, no mark) while the kill switch is in
       MAINTENANCE. Entries stay in expired+unsummarized state; the
       first post-MAINTENANCE sweep fires the deferred digest normally.
       Phase-2 deletion (already-summarized or clean-expired entries)
       still proceeds during MAINTENANCE because that path has no push
       side-effect. Op Stds v10 §2 codifies this carve-out.
    I. weekly_generate catch-up recovery — 2026-06-01. weekly_generate is
       the one tracked daemon on a calendar schedule (StartCalendarInterval,
       Friday 14:00), so a *crashed* Friday cycle is not re-invoked by
       launchd until the next Friday — launchd treats a started-then-failed
       job as "ran" (unlike the interval pollers, whose next StartInterval
       tick IS their recovery). Check C detects the resulting marker
       staleness (8-day window) and the external UptimeRobot ping (audit
       F16) covers total-host death, but neither *recovers* the missed run.
       Check I closes that gap: on a subsequent daily run, if the current
       target week's generation did not run and we are still inside a short
       catch-up window, it re-fires the generation once. CRITICAL triple-fire
       on catch-up failure (page deferred — record kept — during MAINTENANCE
       per the push-vs-record carve-out). See the in-code rationale above
       `_check_weekly_generate_catchup`.

       (There is no Check H. Doctrine once named a heartbeat-staleness check
       "Check H", but that mechanism was never built — the marker-file Check
       C is the staleness floor. Corrected in the 2026-06-01 blueprint
       doctrine pass; the next free check letter is therefore I.)

Planned (NOT in this file, scheduled for a follow-on PR — the Check E
shipping PR; see `docs/tech_debt.md`):
    E. Anthropic spend trend. Deferred from Session 2 — the Admin API key
       provisioning is the operator's prerequisite, not a code path.

Trigger this script from a launchd plist. See `scripts/launchd/`.
"""
from __future__ import annotations

import inspect
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from safety_reports import weekly_generate
from shared import (
    alert_dedupe,
    circuit_breaker,
    defaults,
    graph_client,
    heartbeat_client,
    resend_client,
    review_queue,
    sheet_ids,
    smartsheet_client,
)
from shared.error_log import (
    Severity,
    _alert_critical,
    _maybe_fire_window_summary,
    its_error_log,
    log,
)
from shared.kill_switch import SystemState, check_system_state
from shared.review_queue import ReviewReason, SlaTier
from shared.scheduling import TimeOffClient, is_federal_holiday, resolve_chain

_SCRIPT = "scripts.watchdog"

# Caps prevent the WARN detail string from ballooning when something goes
# truly sideways (e.g., dozens of rows past SLA after a long PAUSED window).
# 5 is enough for an operator to triage; the full set is one Smartsheet
# query away anyway.
REVIEW_QUEUE_ITEM_CAP = 5
CRITICAL_ITEMS_CAP = 5

# Check C scaffold. Marker dir lives under ~/its/ so it's co-located with
# the rest of the codebase but stays out of the repo (.gitignored). Each
# scheduled job calls `write_last_run_marker(<job_name>)` on success;
# Check C verifies the markers stay fresh for everything in TRACKED_JOBS.
WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
TRACKED_JOBS: list[str] = [
    "safety_weekly_generate",
    "safety_weekly_send_poll",
    "safety_picklist_audit",
    "safety_intake",
]

# Per-job freshness windows. Jobs not in this map use the default 24h
# window — appropriate for daily cadences. Weekly (Friday) jobs use 8 days
# so a missed Friday + the following Wednesday still surface as stale, but
# a 1-day-late run does not false-positive. High-frequency pollers use
# a tight window (a couple of poll intervals) so a missed cycle surfaces
# promptly without the operator having to wait for the daily watchdog.
TRACKED_JOB_WINDOWS: dict[str, timedelta] = {
    "safety_weekly_generate": timedelta(days=8),
    # weekly_send_poll runs every 15 min (default); 30 min == 2 cycles.
    # A single missed cycle is tolerated; two consecutive misses fire.
    "safety_weekly_send_poll": timedelta(minutes=30),
    # picklist drift audit runs once weekly (Sunday afternoon per
    # operator launchd schedule). 8-day window matches the weekly_generate
    # pattern — a missed Sunday + the following Friday still surfaces.
    "safety_picklist_audit": timedelta(days=8),
    # intake_poll runs every 60s (launchd StartInterval). 5 min == ~5 cycles:
    # tolerates a few transient missed cycles (Graph hiccup, lock overlap)
    # without false-positiving, but a genuine stall surfaces at the next daily
    # watchdog run. Per the high-frequency-poller window convention above.
    "safety_intake": timedelta(minutes=5),
}
DEFAULT_TRACKED_JOB_WINDOW = timedelta(hours=24)

# Check D scan window. 14 days = ~2 weeks of forward visibility; long
# enough that PTO planned at the start of the next sprint surfaces; short
# enough that the next watchdog run inevitably re-catches anything still
# unresolved.
REVIEWER_CHAIN_SCAN_DAYS = 14

# Workstreams whose chains Check D walks every morning. Add a slug here
# when its three-tier chain goes live in ITS_Config / DEFAULT_REVIEWER_CHAINS.
WORKSTREAMS_TO_SCAN: list[str] = ["safety_reports"]

# Check F mailbox routing. Workstream slug (used in the
# `mail_intake.<workstream>.max_idle_hours` config key) → mailbox address.
# Add entries when a new workstream's intake mailbox goes live; until then
# the iteration is bounded by the ITS_Config rows seeded, so an unmapped
# workstream surfaces a WARN ("no mailbox configured") rather than failing
# silently.
WORKSTREAM_TO_MAILBOX: dict[str, str] = {
    "safety": "safety@evergreenmirror.com",
    # procurement / subcontracts / its / voice — added when activated.
}


@dataclass(frozen=True)
class CheckResult:
    severity: Severity
    summary: str
    details: str = ""


# ---- Check A: stale review queue ----------------------------------------


def _check_stale_review_queue() -> CheckResult:
    """PENDING rows past 2× SLA → WARN with capped Item ID list."""
    rows = review_queue.get_pending()
    stale = [r for r in rows if review_queue.is_past_sla(r)]

    if not stale:
        return CheckResult(
            severity=Severity.INFO,
            summary="No stale items in ITS_Review_Queue (past 2× SLA).",
        )

    item_ids = [str(r["Item ID"]) for r in stale]
    capped = item_ids[:REVIEW_QUEUE_ITEM_CAP]
    details = f"Item IDs: {', '.join(capped)}"
    if len(item_ids) > REVIEW_QUEUE_ITEM_CAP:
        details += f" (showing first {REVIEW_QUEUE_ITEM_CAP} of {len(item_ids)})"

    return CheckResult(
        severity=Severity.WARN,
        summary=f"{len(stale)} item(s) past 2× SLA in ITS_Review_Queue.",
        details=details,
    )


# ---- Check B: open CRITICAL events --------------------------------------


def _check_open_criticals() -> CheckResult:
    """CRITICAL rows in ITS_Errors with Resolved At blank → WARN.

    The `Severity` filter is passed to `get_rows` so the filtering happens
    inside the SDK layer (currently client-side post-fetch per
    smartsheet_client.get_rows — at sandbox volume that's fine). The
    `Resolved At` blank-check is local because a missing-key row dict and
    a row with `Resolved At=None` both mean "open" per the schema's
    "presence implies resolved" design.
    """
    rows = smartsheet_client.get_rows(
        sheet_ids.SHEET_ERRORS,
        filters={"Severity": "CRITICAL"},
    )
    open_rows = [r for r in rows if not r.get("Resolved At")]

    if not open_rows:
        return CheckResult(
            severity=Severity.INFO,
            summary="No open CRITICAL events in ITS_Errors.",
        )

    codes: list[str] = []
    for r in open_rows:
        code = r.get("Error")
        if code in (None, ""):
            codes.append("<no-code>")
        else:
            codes.append(str(code))
    capped = codes[:CRITICAL_ITEMS_CAP]
    details = f"Error codes: {', '.join(capped)}"
    if len(codes) > CRITICAL_ITEMS_CAP:
        details += f" (showing first {CRITICAL_ITEMS_CAP} of {len(codes)})"

    return CheckResult(
        severity=Severity.WARN,
        summary=f"{len(open_rows)} open CRITICAL event(s) in ITS_Errors.",
        details=details,
    )


# ---- Check C: scheduled-jobs last-run via marker file -------------------


def write_last_run_marker(job_name: str) -> None:
    """Write a `.last_run` marker for `job_name` with current UTC timestamp.

    Every scheduled script calls this on successful completion. Pattern:
    `<job_name>.last_run` file in `~/its/.watchdog/`, contents = ISO 8601
    UTC. Directory created on demand. Fail-soft: marker write failures log
    WARN but do not raise — a failed marker is operationally less severe
    than a failed job, and the job itself has already succeeded by the
    time this helper is called.
    """
    try:
        WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        marker = WATCHDOG_MARKER_DIR / f"{job_name}.last_run"
        marker.write_text(datetime.now(UTC).isoformat())
    except OSError as e:
        log(
            Severity.WARN,
            f"{_SCRIPT}.write_last_run_marker",
            f"failed to write marker for {job_name!r}: {e!r}",
        )


def _check_scheduled_jobs() -> CheckResult:
    """Check C: verify each TRACKED_JOBS entry has fired within its expected window.

    Each tracked job has either a per-job window in TRACKED_JOB_WINDOWS or
    falls back to DEFAULT_TRACKED_JOB_WINDOW (24h). Adding a new daily job
    is one line: append the slug to TRACKED_JOBS and ensure the job calls
    `write_last_run_marker` on success. Adding a weekly/monthly job is two
    lines: also add a per-job timedelta to TRACKED_JOB_WINDOWS.

    Returns INFO with a noop summary when TRACKED_JOBS is empty. When jobs
    are tracked, returns WARN if any marker is missing or older than that
    job's window, otherwise INFO.
    """
    if not TRACKED_JOBS:
        return CheckResult(
            severity=Severity.INFO,
            summary="No scheduled jobs tracked (TRACKED_JOBS is empty).",
        )

    now = datetime.now(UTC)
    stale: list[str] = []
    for job in TRACKED_JOBS:
        window = TRACKED_JOB_WINDOWS.get(job, DEFAULT_TRACKED_JOB_WINDOW)
        marker = WATCHDOG_MARKER_DIR / f"{job}.last_run"
        if not marker.exists():
            stale.append(f"{job} (no marker)")
            continue
        try:
            last_run = datetime.fromisoformat(marker.read_text().strip())
        except (OSError, ValueError) as e:
            stale.append(f"{job} (unreadable marker: {e!r})")
            continue
        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=UTC)
        if (now - last_run) > window:
            stale.append(f"{job} (last_run={last_run.isoformat()})")

    if not stale:
        return CheckResult(
            severity=Severity.INFO,
            summary=f"All {len(TRACKED_JOBS)} tracked scheduled job(s) fresh.",
        )
    return CheckResult(
        severity=Severity.WARN,
        summary=f"{len(stale)} of {len(TRACKED_JOBS)} tracked scheduled job(s) stale.",
        details="; ".join(stale),
    )


# ---- Check D: 14-day reviewer-chain forward scan ------------------------


def _check_reviewer_chain_forward() -> CheckResult:
    """Check D: 14-day forward scan for reviewer-chain gaps (Op Stds v9 §18).

    For each workstream in `WORKSTREAMS_TO_SCAN`, walk the next
    `REVIEWER_CHAIN_SCAN_DAYS` days. Federal holidays are skipped (the
    business doesn't need reviewer coverage on a closed day). For each
    business day, resolve the chain (PTO-aware via the live
    `TimeOffClient`); if the chain is empty, that day is a gap.

    Gaps are logged to `ITS_Review_Queue` as an INFO row per workstream
    (one row collecting all that workstream's gaps; not one row per gap).
    `reason=OTHER` and `sla_tier=SUBCONTRACT_DRAFT` chosen so Check A's
    "past 2× SLA" stale detector gives the operator a 4-day triage window
    on anomaly rows before re-WARNing.

    Known behavior: Check D does NOT deduplicate across runs. A persistent
    gap creates one new row per watchdog run. Acceptable for Session 2;
    future enhancement is to scan for an existing matching anomaly row
    before adding if the proliferation becomes painful.
    """
    time_off = TimeOffClient()  # per-instance cache: one fetch for all workstreams
    today = date.today()
    rows_written = 0

    for workstream in WORKSTREAMS_TO_SCAN:
        gaps: list[date] = []
        for offset in range(REVIEWER_CHAIN_SCAN_DAYS):
            scan_date = today + timedelta(days=offset)
            if is_federal_holiday(scan_date):
                continue
            chain = resolve_chain(workstream, scan_date, time_off=time_off)
            if chain.is_empty:
                gaps.append(scan_date)

        if gaps:
            _log_anomaly_to_review_queue(workstream, gaps)
            rows_written += 1

    if rows_written == 0:
        return CheckResult(
            severity=Severity.INFO,
            summary=(
                f"No reviewer-chain gaps in next {REVIEWER_CHAIN_SCAN_DAYS} day(s) "
                f"across {len(WORKSTREAMS_TO_SCAN)} workstream(s)."
            ),
        )
    return CheckResult(
        severity=Severity.INFO,
        summary=(
            f"Logged {rows_written} reviewer-chain anomaly row(s) to "
            f"ITS_Review_Queue."
        ),
    )


def _log_anomaly_to_review_queue(workstream: str, gaps: list[date]) -> None:
    """Write one INFO ANOMALY row to ITS_Review_Queue for a workstream's gaps.

    Schema mapping (resolved 2026-05-20 per operator pre-flight decision):
      - `workstream='global'` (the ITS_Review_Queue VALID_WORKSTREAMS set
        does not include `'watchdog'`; `'global'` is the closest fit and
        already used by the kill-switch.)
      - `reason=ReviewReason.OTHER` (no `'anomaly'` value exists in the
        live picklist).
      - `sla_tier=SlaTier.SUBCONTRACT_DRAFT` (4-day stale window so
        Check A's WARN doesn't auto-fire on these anomaly rows within
        the operator's normal triage window).
      - `severity=Severity.INFO`.

    Payload carries the actual workstream and gap dates so the operator
    can act on them; Item ID is auto-generated by `review_queue.add`.
    """
    summary = (
        f"reviewer-chain gap detected ({len(gaps)} day(s)) for "
        f"workstream={workstream!r}"
    )
    payload = {
        "type": "reviewer_chain_gap",
        "workstream": workstream,
        "gap_dates": [d.isoformat() for d in gaps],
    }
    review_queue.add(
        workstream="global",
        summary=summary,
        payload=payload,
        sla_tier=SlaTier.SUBCONTRACT_DRAFT,
        reason=ReviewReason.OTHER,
        severity=Severity.INFO,
        source_file=__file__,
        security_flag=False,
    )


# ---- Check F: Mail.app rule silent-disable ------------------------------


def _check_mail_intake_silent_disable() -> CheckResult:
    """Check F: detect mailboxes that have gone silent past their threshold.

    Iterates `ITS_Config` rows matching prefix `mail_intake.` and ending
    `.max_idle_hours`. For each, resolves the mailbox via
    `WORKSTREAM_TO_MAILBOX` and queries Graph for the most recent inbound
    timestamp. WARN summary names each silent mailbox + its idle-hours
    figure.

    Per planning decision F.2a: absolute idle-hours threshold per
    mailbox, tunable via ITS_Config — no code change needed when adding
    a new mailbox.

    Fail-soft: per-mailbox Graph errors WARN and continue; the check
    overall still surfaces other mailbox results.
    """
    intake_rows = smartsheet_client.get_settings_with_prefix("mail_intake.")
    if not intake_rows:
        return CheckResult(
            severity=Severity.INFO,
            summary="No mail_intake.* rows in ITS_Config; nothing to check.",
        )

    silent: list[str] = []
    now = datetime.now(UTC)

    for setting_key, value_str in intake_rows.items():
        if not setting_key.endswith(".max_idle_hours"):
            continue

        workstream = (
            setting_key.removeprefix("mail_intake.").removesuffix(".max_idle_hours")
        )
        try:
            threshold_hours = int(value_str)
        except ValueError:
            log(
                Severity.WARN,
                f"{_SCRIPT}._check_mail_intake_silent_disable",
                f"non-int max_idle_hours for {workstream!r}: {value_str!r}",
            )
            continue

        mailbox = WORKSTREAM_TO_MAILBOX.get(workstream)
        if not mailbox:
            log(
                Severity.WARN,
                f"{_SCRIPT}._check_mail_intake_silent_disable",
                f"no mailbox in WORKSTREAM_TO_MAILBOX for workstream {workstream!r}",
            )
            continue

        try:
            last_inbound = graph_client.fetch_latest_inbound_timestamp(mailbox)
        except graph_client.GraphError as e:
            log(
                Severity.WARN,
                f"{_SCRIPT}._check_mail_intake_silent_disable",
                f"Graph fetch failed for {mailbox}: {e!r}",
            )
            continue

        if last_inbound is None:
            # Empty mailbox is distinct from silent-disable — could be a
            # brand-new mailbox that just hasn't received its first message.
            # Treat as informational, not stale.
            continue

        idle_hours = (now - last_inbound).total_seconds() / 3600.0
        if idle_hours > threshold_hours:
            silent.append(
                f"{mailbox} idle {idle_hours:.1f}h (threshold {threshold_hours}h)"
            )

    if not silent:
        return CheckResult(
            severity=Severity.INFO,
            summary="All tracked intake mailboxes fresh.",
        )
    return CheckResult(
        severity=Severity.WARN,
        summary=f"{len(silent)} intake mailbox(es) silent past threshold.",
        details="; ".join(silent),
    )


# ---- Check G: alert-dedupe summary sweep --------------------------------

_SUMMARY_SUBJECT_PREFIX = "[ITS CRITICAL SUMMARY]"


def _compose_summary(entry: alert_dedupe.ExpiredEntry, run_ts: str) -> tuple[str, str]:
    """Build (subject, body) for one expired-window summary email.

    Subject:  [ITS CRITICAL SUMMARY] {script}: N suppressed occurrences
    Body:     Fields naming the window + filter criteria for ITS_Errors.

    The body references filter criteria rather than enumerating
    correlation IDs inline because the state file aggregates only
    (suppressed_count, timestamps) — individual correlation IDs live in
    ITS_Errors. Operator pulls detail from the sheet with the filter.

    `entry.key` is `f"{script}::{error_code}"`; we split once on `::` to
    recover the two parts for display. A key without `::` falls back to
    using the whole string as `script` and an empty error_code (the
    `record_fire` callers always build keys with `::`, so this fallback
    is defensive against hand-edited state files).
    """
    script, sep, error_code = entry.key.partition("::")
    if not sep:
        error_code = ""
    subject = (
        f"{_SUMMARY_SUBJECT_PREFIX} {script}: "
        f"{entry.suppressed_count} suppressed occurrences"
    )
    body = "\n".join(
        [
            f"Script:           {script}",
            f"Error code:       {error_code}",
            f"Window opened:    {entry.first_fired_at}",
            f"Window closed:    {entry.window_ends_at}",
            f"First fire:       {entry.first_fired_at}",
            f"Last fire:        {entry.last_fired_at}",
            f"Suppressed count: {entry.suppressed_count}",
            "",
            f"See ITS_Errors (sheet {sheet_ids.SHEET_ERRORS}) for full row detail.",
            "",
            "Filter ITS_Errors by:",
            f"  Script = {script}",
            f"  Surfaced At BETWEEN {entry.first_fired_at} AND {entry.last_fired_at}",
            "",
            f"Sent by watchdog summary sweep, {run_ts}.",
        ]
    )
    return subject, body


def _check_alert_dedupe_summaries(*, alerts_suppressed: bool = False) -> CheckResult:
    """Check G: sweep alert-dedupe state for expired windows.

    For each expired entry:
      - **Phase 1** — If `suppressed_count >= 1` AND `summarized == False`:
        fire a single Resend summary email, then `mark_summarized(key)`.
        The entry stays one more sweep before deletion (phase 2 below).
        Crash safety: a crash between send and mark causes the next
        sweep to re-fire (duplicate email is acceptable).
      - **Phase 2** — Otherwise (`summarized == True` OR
        `suppressed_count == 0`): `delete_entry(key)`. Either the entry
        was summarized in a prior sweep, or the window closed with no
        suppressions (a clean expiry needing no signal).

    **MAINTENANCE behavior (`alerts_suppressed=True`):** Phase 1 is
    DEFERRED — summary emails are not fired and the entry's `summarized`
    flag stays False, so the entry persists in expired+unsummarized
    state across the MAINTENANCE window. The first post-MAINTENANCE
    sweep fires the deferred digest normally. Phase 2 (delete of
    already-summarized or clean-expired entries) PROCEEDS during
    MAINTENANCE — that path doesn't fire push, so suppressing it would
    create unbounded state growth without any operator-visibility
    benefit. Bounded delay = MAINTENANCE window + one watchdog cadence;
    no information loss (the underlying CRITICAL events already wrote
    to ITS_Errors at occurrence time per Op Stds v9 §27). Op Stds v10
    §2 codifies this carve-out.

    Per Op Stds v9 §27 push-vs-record separation, the summary email is
    a Resend-only operational signal — it does NOT write to ITS_Errors
    (the rows already exist from PR α) and does NOT fire Sentry (this
    is not an exception event).

    Returns INFO `CheckResult` with sweep stats. The check itself never
    fails the watchdog run — `_run_check` wraps it for harness-level
    isolation, and the per-entry Resend / mark / delete calls each have
    their own try/except so one bad entry doesn't poison the sweep.
    """
    run_ts = datetime.now(UTC).isoformat()
    entries = alert_dedupe.list_expired_summaries()

    if not entries:
        return CheckResult(
            severity=Severity.INFO,
            summary="No expired alert-dedupe windows to sweep.",
        )

    summaries_fired = 0
    entries_deleted = 0
    summaries_deferred = 0
    fired_keys: list[str] = []
    deferred_keys: list[str] = []

    for entry in entries:
        is_phase_1 = entry.suppressed_count >= 1 and not entry.summarized
        if is_phase_1 and alerts_suppressed:
            # MAINTENANCE defer: skip send AND mark. Entry stays
            # summarized=False with expired window; first sweep after
            # MAINTENANCE clears fires the deferred digest normally.
            deferred_keys.append(entry.key)
            summaries_deferred += 1
            continue
        if is_phase_1:
            subject, body = _compose_summary(entry, run_ts)
            try:
                resend_client.send_alert(subject, body)
            except Exception as e:
                # Resend failure leaves entry unmarked → next sweep retries.
                # No marker rewrite here — resend_client / error_log paths
                # have their own logging; the watchdog status line below
                # surfaces the aggregate count.
                log(
                    Severity.WARN,
                    f"{_SCRIPT}._check_alert_dedupe_summaries",
                    f"[summary-send-failed] key={entry.key!r} {e!r}",
                )
                continue
            alert_dedupe.mark_summarized(entry.key)
            summaries_fired += 1
            fired_keys.append(entry.key)
        else:
            # Phase 2: proceeds during MAINTENANCE — no push side-effect.
            alert_dedupe.delete_entry(entry.key)
            entries_deleted += 1

    parts = [
        f"Examined {len(entries)} expired entr{'y' if len(entries) == 1 else 'ies'}",
        f"fired {summaries_fired} summary email(s)",
        f"deleted {entries_deleted} entr{'y' if entries_deleted == 1 else 'ies'}",
    ]
    if summaries_deferred:
        parts.append(
            f"deferred {summaries_deferred} summar"
            f"{'y' if summaries_deferred == 1 else 'ies'} during MAINTENANCE"
        )
    summary = "; ".join(parts) + "."
    detail_parts = []
    if fired_keys:
        detail_parts.append(f"Summaries fired for: {', '.join(fired_keys)}")
    if deferred_keys:
        detail_parts.append(
            f"Summaries deferred (MAINTENANCE) for: {', '.join(deferred_keys)}"
        )
    details = " | ".join(detail_parts)
    return CheckResult(severity=Severity.INFO, summary=summary, details=details)


# ---- Check I: weekly_generate catch-up recovery -------------------------
#
# Motivating finding (Tier-1 self-heal completion, 2026-06-01). Every other
# tracked daemon is interval-driven (launchd StartInterval), so a crashed
# cycle is simply re-run at the next interval — launchd re-invocation IS the
# recovery, and no KeepAlive is needed. weekly_generate is the lone
# exception: it runs Friday 14:00 via StartCalendarInterval, so a *crashed*
# Friday cycle is not re-invoked until the next Friday (launchd treats a
# started-then-failed job as "ran"; it only re-runs a calendar job that the
# host MISSED while asleep/off, not one that ran and errored). Check C
# detects the resulting marker staleness (8-day window) and the external
# UptimeRobot ping (audit F16) covers total-host death — but neither
# *recovers* the missed run; it stays missing until next Friday or a human
# acts. Check I is that recovery: a daily, self-correcting re-fire that
# brings weekly_generate up to the interval pollers' self-heal bar.
#
# This is the one open leg of the V&R Pre-Cutover Condition 4 (Tier-1
# self-heal) gate; the all-daemon Check C coverage and the F16 ping legs are
# already met. See the 2026-06-01 blueprint doctrine correction.

# The job whose Friday calendar-run Check I recovers — the same marker slug
# Check C tracks (TRACKED_JOBS[0]), but read here against the most-recent
# Friday trigger instant rather than Check C's broad 8-day staleness window.
WEEKLY_GENERATE_JOB_SLUG = "safety_weekly_generate"

# weekly_generate's launchd trigger: Friday (Python date.weekday() == 4) at
# 14:00 local time. Mirrors scripts/launchd/org.solutionsmith.its.weekly-
# generate.plist (StartCalendarInterval Weekday=5 / Hour=14); keep in sync
# if that plist's schedule ever changes.
WEEKLY_GENERATE_TRIGGER_WEEKDAY = 4
WEEKLY_GENERATE_TRIGGER_HOUR = 14

# Catch-up window measured from the Friday 14:00 trigger: through the end of
# the following Monday (the "following business day" bound). That spans the
# Saturday / Sunday / Monday daily-or-on-wake watchdog runs after a missed
# Friday, without re-firing an ancient week — a miss not recovered by Monday
# falls through to Check C's 8-day WARN → human (Tier 2/3). Re-firing is
# idempotent (weekly_generate replace-if-unapproved / refuse-if-approved),
# so the bound is about not wasting Anthropic spend on stale weeks, not
# about safety.
CATCHUP_WINDOW = timedelta(days=3)


def _local_now() -> datetime:
    """Local timezone-aware 'now'. Seam so tests can pin the clock.

    weekly_generate's launchd trigger fires in LOCAL time, so the Friday-
    trigger math runs in local time. The marker (written by weekly_generate
    as UTC) is compared as an aware datetime, which Python resolves correctly
    across zones.
    """
    return datetime.now().astimezone()


def _most_recent_friday_trigger(now: datetime) -> datetime:
    """Most recent Friday 14:00 local at or before `now` (aware, local tz)."""
    days_since_friday = (now.weekday() - WEEKLY_GENERATE_TRIGGER_WEEKDAY) % 7
    candidate = (now - timedelta(days=days_since_friday)).replace(
        hour=WEEKLY_GENERATE_TRIGGER_HOUR, minute=0, second=0, microsecond=0
    )
    if candidate > now:
        # `now` is earlier in the day than the trigger hour on a Friday →
        # this week's trigger hasn't happened; the most recent is last week.
        candidate -= timedelta(days=7)
    return candidate


def _read_marker_datetime(job_slug: str) -> datetime | None:
    """Read a `{slug}.last_run` marker as an aware datetime, or None.

    None on missing / unreadable / unparseable marker — each such case is
    treated as "did not run" by the caller (catch-up errs toward firing, not
    toward a silent miss). Mirrors Check C's contents-based read; a naive
    timestamp is assumed UTC.
    """
    marker = WATCHDOG_MARKER_DIR / f"{job_slug}.last_run"
    if not marker.exists():
        return None
    try:
        parsed = datetime.fromisoformat(marker.read_text().strip())
    except (OSError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _wpr_rows_exist_for_week(week_start: date) -> bool:
    """True iff WPR_Pending_Review has >=1 row for the target week.

    The second "did it complete" signal alongside the marker's "did it run":
    weekly_generate's marker write is fail-soft, so a successful run can
    leave a stale/missing marker. Row presence catches that and prevents a
    wasteful re-fire. Fail-soft: a read error logs WARN and returns False,
    so the decision falls back to the marker signal — and a Smartsheet
    outage that hides the rows here will resurface when the (also-Smartsheet)
    catch-up generation runs and fails loudly, never silently.
    """
    try:
        rows = smartsheet_client.get_rows(
            sheet_ids.SHEET_WPR_PENDING_REVIEW,
            filters={"Week": week_start.isoformat()},
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft to marker-only decision
        log(
            Severity.WARN,
            f"{_SCRIPT}._wpr_rows_exist_for_week",
            f"WPR_Pending_Review read failed for week {week_start}: {exc!r}",
        )
        return False
    return bool(rows)


def _check_weekly_generate_catchup(*, alerts_suppressed: bool = False) -> CheckResult:
    """Check I: re-fire a missed weekly_generate Friday run (Tier-1 self-heal).

    Catch-up fires iff ALL THREE hold for the current target week:
      (a) we are within CATCHUP_WINDOW of the most recent Friday 14:00
          trigger (don't recover an ancient week — Check C owns those);
      (b) the Check C marker is missing or older than that trigger
          ("did not run"); AND
      (c) WPR_Pending_Review has no row for that week ("produced nothing").
    A fresh marker OR existing rows means the run happened, so we do not
    re-fire. Combining the two "ran" signals with OR (fire only when BOTH
    are negative) is deliberately conservative: re-firing is safe but burns
    Anthropic spend, and a fail-soft marker write must not look like a miss.
    The "ran but every project errored" case is out of scope here — those
    runs DID complete (rows + GENERATION_FAILED placeholders exist) and are
    owned by the future generation-retry redesign (planning #1); Check I
    closes only the "calendar run never executed" Tier-1 gap.

    On fire, calls `weekly_generate._run_pipeline` directly — NOT the
    `@require_active`-decorated `main()`. The watchdog's own `main()` has
    already honored the kill switch, and `_run_pipeline` is weekly_generate's
    documented direct-invocation entry point (its `main` docstring: "Logic
    lives in `_run_pipeline` so unit tests can call it directly without the
    decorator stack"). Calling it directly is what lets a catch-up run during
    MAINTENANCE — which the Tier-1 brief requires, because generation is
    internal (no external send); the decorated `main()` would be blocked by
    `@require_active` during MAINTENANCE. Capability gate is unaffected: the
    watchdog is neither a generation nor a send script in
    tests/test_capability_gating.py (scripts/ is not walked), and Check I
    drives generation only — it adds no send capability.

    MAINTENANCE (`alerts_suppressed=True`): generation still RUNS, but a
    catch-up FAILURE's operator page (the Resend/Sentry `_alert_critical`
    legs) is DEFERRED — only the ITS_Errors record row is written
    (push-vs-record, Op Stds §3.1; same carve-out shape as Check G). The
    deferred CRITICAL resurfaces post-MAINTENANCE via Check B (open
    CRITICALs). `alerts_suppressed` is wired in by `_run_check`'s signature
    inspection.

    At most one catch-up per watchdog run (no loop): this returns after a
    single `_run_pipeline` call, and a successful run refreshes the marker so
    the next watchdog run takes the "marker fresh" early-return. A persistent
    failure re-attempts on the next daily run while still inside the window,
    then falls to Check C / a human.
    """
    now = _local_now()
    last_trigger = _most_recent_friday_trigger(now)
    target_week = (last_trigger - timedelta(days=4)).date()  # Friday → Monday

    deadline = datetime.combine(
        last_trigger.date() + CATCHUP_WINDOW, time.max, tzinfo=last_trigger.tzinfo
    )
    if now > deadline:
        return CheckResult(
            severity=Severity.INFO,
            summary=(
                f"weekly_generate catch-up: week {target_week} is past the "
                f"catch-up window (Check C covers older misses)."
            ),
        )

    marker_dt = _read_marker_datetime(WEEKLY_GENERATE_JOB_SLUG)
    if marker_dt is not None and marker_dt >= last_trigger:
        return CheckResult(
            severity=Severity.INFO,
            summary=f"weekly_generate ran for week {target_week} (marker fresh).",
        )

    if _wpr_rows_exist_for_week(target_week):
        return CheckResult(
            severity=Severity.INFO,
            summary=(
                f"weekly_generate produced WPR rows for week {target_week} "
                f"(marker stale but rows present); no catch-up."
            ),
        )

    return _fire_weekly_generate_catchup(target_week, alerts_suppressed=alerts_suppressed)


def _fire_weekly_generate_catchup(
    target_week: date, *, alerts_suppressed: bool
) -> CheckResult:
    """Re-run weekly_generate for a missed week; escalate on failure.

    Returns INFO on success, WARN on an empty-reviewer-chain abort
    (weekly_generate has already recorded its own CRITICAL row — no
    double-escalation), and INFO on a generation failure (the CRITICAL row +
    operator page are fired explicitly inside `_escalate_catchup_failure`
    with a threaded correlation_id, so the returned result is informational
    only and avoids a duplicate, correlation-id-less row).
    """
    log(
        Severity.INFO,
        _SCRIPT,
        f"[catch-up] weekly_generate did not run for week {target_week}; "
        f"re-firing generation",
    )
    try:
        result = weekly_generate._run_pipeline(week_start_override=target_week)
    except Exception as exc:  # noqa: BLE001 — convert to a MAINTENANCE-aware CRITICAL
        tb = traceback.format_exc()
        return _escalate_catchup_failure(
            target_week, exc, tb, alerts_suppressed=alerts_suppressed
        )

    if result.get("aborted_empty_chain"):
        return CheckResult(
            severity=Severity.WARN,
            summary=(
                f"weekly_generate catch-up for week {target_week} aborted: "
                f"empty reviewer chain (weekly_generate logged its own CRITICAL)."
            ),
        )

    drafts = result.get("drafts_written", 0)
    failed = result.get("drafts_failed", 0)
    return CheckResult(
        severity=Severity.INFO,
        summary=(
            f"weekly_generate catch-up fired for week {target_week}: "
            f"{drafts} draft(s) written, {failed} failed."
        ),
        details=f"correlation_id={result.get('correlation_id', '?')}",
    )


def _escalate_catchup_failure(
    target_week: date,
    exc: Exception,
    tb: str,
    *,
    alerts_suppressed: bool,
) -> CheckResult:
    """Programmatic CRITICAL triple-fire for a failed catch-up generation.

    Mirrors the canonical pattern in `shared.picklist_sync.sync_all`
    (sync_all does not raise on partial failure, so its triple-fire is
    explicit): write the ITS_Errors record row via `log(CRITICAL, ...)` and
    fire the operator-page legs via `error_log._alert_critical`, threading a
    single `correlation_id` across both so one grep recovers the full
    Smartsheet / Resend / Sentry picture.

    push-vs-record (Op Stds §3.1): the record row is ALWAYS written — even in
    MAINTENANCE, a real failure must leave a forensic trail. Only the
    Resend/Sentry PAGE is deferred under `alerts_suppressed`; the deferred
    CRITICAL resurfaces post-MAINTENANCE via Check B (open CRITICALs).
    """
    correlation_id = str(uuid.uuid4())
    message = f"weekly_generate catch-up FAILED for week {target_week}: {exc!r}"
    # A3: alert=False — the watchdog manages its own operator page below
    # (deferred under MAINTENANCE via alerts_suppressed), so the record log
    # must NOT auto-fire the alert legs or it would page during MAINTENANCE
    # and double-fire the Sentry leg.
    log(
        Severity.CRITICAL,
        _SCRIPT,
        message,
        error_code="weekly_generate_catchup_failed",
        exc_info=tb,
        correlation_id=correlation_id,
        alert=False,
    )
    if alerts_suppressed:
        log(
            Severity.INFO,
            _SCRIPT,
            f"[catch-up] CRITICAL page deferred during MAINTENANCE for week "
            f"{target_week} (corr={correlation_id[:8]}); record row written.",
        )
    else:
        _alert_critical(
            _SCRIPT,
            message,
            tb,
            correlation_id=correlation_id,
            error_code="weekly_generate_catchup_failed",
        )
    return CheckResult(
        severity=Severity.INFO,
        summary=(
            f"weekly_generate catch-up FAILED for week {target_week} — CRITICAL "
            + (
                "recorded (page deferred, MAINTENANCE)"
                if alerts_suppressed
                else "triple-fired"
            )
            + f" (corr={correlation_id[:8]})."
        ),
    )


# ---- Check J: circuit-breaker prolonged-open alert ----------------------


def _check_circuit_breaker_prolonged_open(
    *, alerts_suppressed: bool = False
) -> CheckResult:
    """Page the operator when the Smartsheet circuit breaker has been OPEN (one
    outage episode) longer than ``circuit_breaker.prolonged_open_alert_seconds``.

    Reads ``circuit_breaker.seconds_open()`` — a lock-free LOCAL-file read, so it
    works during the very Smartsheet outage this fires for. The threshold read
    DOES short-circuit during that outage, so it MUST fail open to the default
    (mirrors the heartbeat_url read in ``main()``).

    The page fires INLINE via ``_alert_critical`` — NOT a returned CRITICAL:
    ``_run_check`` routes results through ``log()``, which writes records but
    does NOT fire the Resend/Sentry legs, so a returned-CRITICAL would be a
    silent missed wake-up (see the ``log(CRITICAL)``-doesn't-page tech-debt).
    A STABLE ``error_code`` lets the per-key dedupe throttle the page to ~1/hour
    even though this runs every cycle while OPEN.

    The ``_alert_critical`` call is wrapped in ``circuit_breaker.bypass()``: its
    Resend leg reads ``system.operator_email`` from ITS_Config via the GUARDED
    ``get_setting``, which would itself short-circuit while the breaker is OPEN
    — i.e. exactly when this check fires — so without the bypass the page could
    never send (confirmed by the PR-1 smoke's ``[resend-alert-failed]
    SmartsheetCircuitOpenError`` lines). The ITS_Errors record write is
    independently bypassed (§3.1 fold-in); Resend/Sentry are HTTP, so the page
    goes out whenever Smartsheet is reachable (incl. cooldown-after-recovery).
    """
    dur = circuit_breaker.seconds_open()
    if dur is None:
        return CheckResult(Severity.INFO, "circuit breaker not open")

    try:
        raw = smartsheet_client.get_setting(
            "circuit_breaker.prolonged_open_alert_seconds", workstream="global"
        )
        threshold = (
            int(raw)
            if raw is not None
            else defaults.CIRCUIT_BREAKER_PROLONGED_OPEN_ALERT_SECONDS
        )
    except (smartsheet_client.SmartsheetError, ValueError, TypeError):
        # Fail open to the default — the ITS_Config read short-circuits during
        # the very outage this check exists for.
        threshold = defaults.CIRCUIT_BREAKER_PROLONGED_OPEN_ALERT_SECONDS

    if dur <= threshold:
        return CheckResult(
            Severity.WARN,
            f"circuit breaker OPEN for {dur:.0f}s (< {threshold}s threshold)",
        )

    correlation_id = str(uuid.uuid4())
    message = (
        f"Smartsheet circuit breaker OPEN for {dur:.0f}s (> {threshold}s) — "
        "backend degraded and not self-recovering."
    )
    # A3: alert=False — same rationale as the catch-up escalation. The page
    # below is wrapped in circuit_breaker.bypass() (the breaker is OPEN, so the
    # Resend leg's operator_email read must bypass it); log()'s auto-fire could
    # not provide that wrapper, so paging stays explicit here.
    log(
        Severity.CRITICAL,
        _SCRIPT,
        message,
        error_code="circuit_breaker_prolonged_open",
        correlation_id=correlation_id,
        alert=False,
    )
    if alerts_suppressed:
        log(
            Severity.INFO,
            _SCRIPT,
            f"[prolonged-open] CRITICAL page deferred during MAINTENANCE "
            f"(corr={correlation_id[:8]}); record row written.",
        )
    else:
        # bypass() so the Resend leg's operator_email read isn't short-circuited
        # by the very-OPEN breaker (see docstring).
        with circuit_breaker.bypass():
            _alert_critical(
                _SCRIPT,
                message,
                "",
                correlation_id=correlation_id,
                error_code="circuit_breaker_prolonged_open",
            )
    return CheckResult(
        Severity.INFO,
        f"circuit breaker OPEN for {dur:.0f}s (> {threshold}s) — CRITICAL "
        + (
            "recorded (page deferred, MAINTENANCE)"
            if alerts_suppressed
            else "triple-fired"
        )
        + f" (corr={correlation_id[:8]}).",
    )


# ---- Check K: guaranteed F09 cap-window-summary sweep -------------------


def _check_alert_rate_cap_window(
    *, alerts_suppressed: bool = False
) -> CheckResult:
    """Guarantee the F09 alerts-per-hour cap WINDOW SUMMARY fires even when no
    new alert arrives to trigger the opportunistic path in
    ``error_log._maybe_fire_window_summary``.

    Calls ``_maybe_fire_window_summary`` once per cycle → it pops the due window
    via ``alert_dedupe.pop_due_window_summary()`` (atomically marks
    ``summarized=True``) and sends the one exempt summary. The ``summarized``
    flag is the double-fire guard SHARED with the opportunistic path — calling
    from both is safe (first wins; the other gets None).

    DISTINCT from Check G (``_check_alert_dedupe_summaries``), which sweeps the
    PER-KEY dedupe summaries and explicitly skips the reserved
    ``_alerts_per_hour_window`` key. Per-key and per-hour are separate subsystems.

    MAINTENANCE-defer: when ``alerts_suppressed`` do NOT fire (matching Check G);
    the window record persists for the next sweep. The OPPORTUNISTIC path does
    not itself respect MAINTENANCE (error_log-level, pre-existing, out of scope);
    this sweep just keeps its own behavior consistent.
    """
    if alerts_suppressed:
        return CheckResult(
            Severity.INFO, "alert-rate cap-window summary sweep deferred (MAINTENANCE)"
        )
    correlation_id = uuid.uuid4().hex
    _maybe_fire_window_summary(correlation_id)
    return CheckResult(
        Severity.INFO,
        f"alert-rate cap-window summary sweep ran (corr={correlation_id[:8]}).",
    )


# ---- Failure-isolation harness ------------------------------------------


def _run_check(
    check_fn: Callable[..., CheckResult],
    *,
    alerts_suppressed: bool,
) -> None:
    """Run one check with own try/except + marker line on failure.

    Per Op Stds v9 §27. `alerts_suppressed=True` (MAINTENANCE) downgrades
    WARN/CRITICAL results to INFO before routing so the Smartsheet row
    still lands but Resend/Sentry legs (which trigger only on CRITICAL via
    `error_log._alert_critical`) never fire. ERROR (from harness catches)
    is NOT downgraded — a broken check must remain operator-visible
    regardless of operational state.

    Most checks take zero args; severity-downgrade after-the-fact is
    enough. Check G fires Resend directly inline (push side-effect during
    result computation, not via `log()` later), so it needs to know
    `alerts_suppressed` BEFORE running. Detected via signature inspection
    so heterogeneous check signatures coexist without a typed protocol.
    """
    try:
        sig = inspect.signature(check_fn)
        if "alerts_suppressed" in sig.parameters:
            result = check_fn(alerts_suppressed=alerts_suppressed)
        else:
            result = check_fn()
    except Exception as e:
        log(
            Severity.ERROR,
            _SCRIPT,
            f"[watchdog-check-failed:{check_fn.__name__}] {e!r}",
        )
        return

    severity = result.severity
    if alerts_suppressed and severity in (Severity.WARN, Severity.CRITICAL):
        severity = Severity.INFO

    message = result.summary
    if result.details:
        message = f"{result.summary} | {result.details}"

    log(severity, _SCRIPT, message)


def _check_token_write_capability() -> CheckResult:
    """Check L (B2): verify ITS_SMARTSHEET_TOKEN can WRITE, not just read.

    A read-only or mis-scoped token (e.g. after a botched rotation) passes every
    READ and only fails at the first real daemon WRITE — a silent mid-cycle 401
    that is hard to trace. This probe (create + delete a throwaway sheet) turns
    that into a LOUD daily signal: a SmartsheetWriteCapabilityError → CRITICAL,
    which (post-A3) pages the operator via `_run_check`'s `log(CRITICAL)` and is
    deferred during MAINTENANCE by the standard `alerts_suppressed` downgrade
    above. A Smartsheet OUTAGE (SmartsheetCircuitOpenError) is INFO-skipped — it
    is not a token verdict; any other transient error is WARN-inconclusive. Cost
    is one create + one delete per daily watchdog run (negligible footprint;
    the throwaway sheet is named `_its_write_probe_*` and deleted immediately).
    """
    try:
        probe_sheet_id = smartsheet_client.verify_write_capability()
    except smartsheet_client.SmartsheetWriteCapabilityError as exc:
        return CheckResult(
            Severity.CRITICAL,
            f"ITS_SMARTSHEET_TOKEN cannot write (read-only or mis-scoped?): {exc}",
        )
    except smartsheet_client.SmartsheetCircuitOpenError:
        return CheckResult(
            Severity.INFO,
            "token write-probe skipped — Smartsheet circuit breaker OPEN.",
        )
    except smartsheet_client.SmartsheetError as exc:
        return CheckResult(
            Severity.WARN,
            f"token write-probe inconclusive (transient Smartsheet error): {exc!r}",
        )
    # Created → the token can write. Clean up the throwaway probe sheet, with a
    # create→delete eventual-consistency settle retry (the immediate delete can
    # 404 / errorCode 5036 before the new sheet propagates — surfaced in the B2
    # smoke).
    try:
        smartsheet_client.delete_sheet_settling(probe_sheet_id)
    except smartsheet_client.SmartsheetError as exc:
        return CheckResult(
            Severity.WARN,
            f"token write OK, but probe sheet {probe_sheet_id} delete failed "
            f"(manual cleanup of `_its_write_probe_*` may be needed): {exc!r}",
        )
    return CheckResult(Severity.INFO, "ITS_SMARTSHEET_TOKEN write capability OK.")


# ---- Entrypoint ---------------------------------------------------------


CHECKS: list[Callable[..., CheckResult]] = [
    _check_stale_review_queue,
    _check_open_criticals,
    _check_scheduled_jobs,
    _check_reviewer_chain_forward,
    _check_mail_intake_silent_disable,
    _check_alert_dedupe_summaries,
    # Check I runs after Check C (above): Check C reports staleness; Check I
    # recovers the one daemon launchd can't self-recover (weekly_generate,
    # calendar-scheduled). It fires generation inline, so — like Check G —
    # it takes alerts_suppressed (threaded by _run_check) to defer the
    # operator page during MAINTENANCE.
    _check_weekly_generate_catchup,
    # Check J / K (F08/F09 PR 2): prolonged-open page + guaranteed cap-window
    # summary sweep. Both fire alerts inline (J via _alert_critical, K via
    # _maybe_fire_window_summary's _send_exempt_alert), so — like Check G / I —
    # they take alerts_suppressed (threaded by _run_check) to defer the operator
    # page during MAINTENANCE.
    _check_circuit_breaker_prolonged_open,
    _check_alert_rate_cap_window,
    # Check L (B2): token write-capability probe. Returns a CheckResult, so its
    # CRITICAL is paged + MAINTENANCE-deferred by _run_check (no inline alert).
    _check_token_write_capability,
    # Check E (Anthropic spend trend) deferred to a follow-on PR (the
    # Check E shipping PR) — requires an Admin API key (sk-ant-admin01-...
    # prefix) provisioned in Keychain under ITS_ANTHROPIC_ADMIN_API_KEY.
    # The current key is a workspace key (sk-ant-api03-...) which
    # /v1/organizations/cost_report rejects with 401. See
    # docs/session_logs/2026-05-20_watchdog_session_2.md for the
    # pre-flight finding.
]


@its_error_log(_SCRIPT)
def main() -> None:
    state = check_system_state()
    if state == SystemState.PAUSED:
        log(Severity.INFO, _SCRIPT, "PAUSED — skipping all checks")
        return
    alerts_suppressed = state == SystemState.MAINTENANCE
    if alerts_suppressed:
        log(
            Severity.INFO,
            _SCRIPT,
            "MAINTENANCE — checks will run but alerts suppressed",
        )
    for check in CHECKS:
        _run_check(check, alerts_suppressed=alerts_suppressed)

    # Local Check-C freshness marker for the watchdog's own run. The
    # watchdog is deliberately NOT in TRACKED_JOBS (self-tracking has a
    # chicken-and-egg hole — a dead watchdog can't flag its own staleness),
    # so this marker is unconsumed today; it's written so adding the
    # watchdog to TRACKED_JOBS later is a one-line change. The external
    # "is the watchdog (and the host) alive" signal is the heartbeat ping
    # below (audit F16) — that's the real dead-man's switch.
    write_last_run_marker("watchdog")

    # External heartbeat beacon (audit F16). Read the configured ping URL
    # and notify the external monitor (UptimeRobot) that the watchdog —
    # and by proxy the whole host — is alive. This is the ONLY external
    # detector for total-host failure (crash, disk-full, launchd unload,
    # user logout); every in-tenant signal goes silent in that scenario
    # with nothing to raise the alarm. Fail-soft end-to-end: a read failure
    # WARNs and no-ops; a missing/placeholder URL no-ops (INFO); a ping
    # failure is swallowed+logged inside heartbeat_client.ping. The watchdog
    # must complete its real work regardless of monitor health.
    #
    # MAINTENANCE behavior: the ping fires on every non-PAUSED run,
    # INCLUDING MAINTENANCE — the host IS alive during a maintenance window,
    # so suppressing the ping would trip a false "host dead" alert on the
    # external monitor. (Alert *suppression* during MAINTENANCE applies to
    # the checks' own alerts, not to this liveness beacon.) PAUSED returns
    # above before the marker and ping — a deliberately-paused system does
    # not claim liveness.
    try:
        heartbeat_url = smartsheet_client.get_setting(
            "system.heartbeat_url", workstream="global"
        )
    except smartsheet_client.SmartsheetError as exc:
        log(Severity.WARN, _SCRIPT, f"heartbeat_url read failed: {exc!r}")
        heartbeat_url = None

    # Guard: skip the ping when unconfigured. A fork that hasn't provisioned
    # a monitor leaves the seeded placeholder in place — pinging it is a
    # guaranteed failure, so no-op (INFO) rather than WARN every run. This
    # token MUST stay equal to the seed Value in scripts/seed_its_config.py.
    if heartbeat_url and heartbeat_url != "PLACEHOLDER_uptimerobot_heartbeat_url":
        heartbeat_client.ping(heartbeat_url)
    else:
        log(
            Severity.INFO,
            _SCRIPT,
            "system.heartbeat_url not configured (missing or placeholder) "
            "— skipping heartbeat ping",
        )


if __name__ == "__main__":
    main()
