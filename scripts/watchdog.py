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
    C. Scheduled-jobs last-run via marker files — Session 2. Infrastructure
       only per planning decision C1: TRACKED_JOBS is empty by design today
       (only one scheduled job exists — watchdog itself, and self-tracking
       has a chicken-and-egg hole better solved by external heartbeat).
       The marker-write helper is wired so adding a real job is one line.
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

Planned (NOT in this file, scheduled for a follow-on PR — the Check E
shipping PR; see `docs/tech_debt.md`):
    E. Anthropic spend trend. Deferred from Session 2 — the Admin API key
       provisioning is the operator's prerequisite, not a code path.

Trigger this script from a launchd plist. See `scripts/launchd/`.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from shared import (
    alert_dedupe,
    graph_client,
    resend_client,
    review_queue,
    sheet_ids,
    smartsheet_client,
)
from shared.error_log import Severity, its_error_log, log
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
TRACKED_JOBS: list[str] = ["safety_weekly_generate", "safety_weekly_send_poll"]

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
            summary="No scheduled jobs tracked (TRACKED_JOBS is empty by design).",
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


# ---- Entrypoint ---------------------------------------------------------


CHECKS: list[Callable[..., CheckResult]] = [
    _check_stale_review_queue,
    _check_open_criticals,
    _check_scheduled_jobs,
    _check_reviewer_chain_forward,
    _check_mail_intake_silent_disable,
    _check_alert_dedupe_summaries,
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
    # Mark our own run so an external observer (UptimeRobot, etc.) can
    # detect "watchdog itself stopped firing". Not consumed by Check C
    # today — TRACKED_JOBS is empty by design — but the marker is here
    # the moment that decision changes.
    write_last_run_marker("watchdog")


if __name__ == "__main__":
    main()
