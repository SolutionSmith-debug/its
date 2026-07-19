"""RFQ send polling daemon — the thin RFQ entry (ADR-0004 R3).

Purpose
-------
The RFQ twin of ``po_materials.po_send_poll`` / ``safety_reports.weekly_send_poll``: the
SAME ``send_poll_core`` engine, a different ``DaemonConfig``. Discovers
``RFQ_Pending_Review`` rows with ``Send Now`` (immediate) OR ``Approve for Scheduled Send``
(the Monday-≥07:00-Pacific batch) checked, runs the F22 approval-attestation gate on the
driving checkbox against the **ITS — Purchase Orders** workspace (§46 — the SAME procurement
approver set as POs; a procurement approver who can release a PO can release an RFQ), stamps
the verified approver, and dispatches each to ``po_materials.rfq_send.send_one_row``. The
poller has zero send capability of its own; it is an iterator + dispatcher. ``poll_once()``
is the public API; the ``__main__`` guard calls it once and exits, launchd handling cadence
via ``StartInterval`` (default 900 s = 15 min, sourced from ITS_Config
``po_materials.rfq_send.poll_interval_seconds`` at install).

Ships **dark** — go-live is a FIXED high-class External-Send-Gate operator action (flip
``po_materials.rfq_send.polling_enabled`` true + ``install.sh load
org.solutionsmith.its.rfq-send``).

Invariants (§42 — why a thin binding, not a clone)
--------------------------------------------------
- **S5 parameterize-not-clone (Op Stds §14):** the dispatch BODY lives in
  ``safety_reports/send_poll_core.py`` (a required no-default ``DaemonConfig``); this module
  binds the one ``CONFIG``, constructs this daemon's ``HeartbeatReporter``, and re-exports the
  test mock seams — exactly as ``po_send_poll``/``subcontract_send_poll`` are the thin PO/
  subcontract entries. The F22 fail-closed gate, the no-double-send ``SENDING`` exclusion, and
  the per-row fence are written ONCE in the core.
- **No cross-lane mix-up:** ``poll_sheet_id`` is the **RFQ_Pending_Review** sheet;
  ``f22_workspace_id`` is the **ITS — Purchase Orders** workspace (approver set = membership of
  THAT workspace); ``send_fn`` dispatches the RFQ ``SendConfig`` (tagged ``po_materials_rfq``,
  resolving recipients only from ``ITS_Vendors``). Independent lock + heartbeat + review sheet,
  so it coexists with the safety/progress/PO/subcontract send polls without a shared mutex.
- **Invariant 1 (External Send Gate):** zero AI capability. Dispatches via ``CONFIG.send_fn``
  (bound ``rfq_send.send_one_row`` → ``weekly_send.send_one_row``); the AST gate in
  ``tests/test_capability_gating.py::SEND_SCRIPTS`` forbids ``anthropic`` / ``anthropic_client``
  **and** ``ollama_client`` in this file and ``rfq_send.py`` (send scripts are local-AI-free too,
  ADR-0004 decision 12).

Failure modes
-------------
Each candidate row is dispatched under a per-row fence; one failing row (incl. a review row with
no parseable ``rfq_number``, which ``rfq_send``'s envelope HELDs) never tears down the cycle. The
F22 gate is fail-CLOSED — a circuit-open / auth error reading the approver set propagates (cycle
aborts, zero sends) and an empty approver set blocks all sends (``EMPTY_ALLOWLIST`` — the §46 share
list of ITS — Purchase Orders must include the approvers). A disabled
``po_materials.rfq_send.polling_enabled`` ITS_Config value short-circuits the cycle (operator pause
/ dark ship). Successor-remediation tree: ``docs/runbooks/rfq_send.md`` (Op Stds §43).

Consumers
---------
- launchd daemon ``org.solutionsmith.its.rfq-send`` (the 15-min interval poll).
- Writes an ``rfq_send_poll.last_run`` watchdog marker each cycle once ACTIVE (registered in
  ``watchdog.TRACKED_JOBS`` for Check-C staleness alongside ``rfq_poll``).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from po_materials import rfq_review, rfq_send
from safety_reports import send_poll_core, weekly_send
from shared import approval_verification, sheet_ids
from shared.error_log import its_error_log
from shared.heartbeat import HeartbeatReporter, HeartbeatStatus
from shared.kill_switch import require_active
from shared.required_config import ConfigKey, resolve_and_log

# Re-export PollStats (the public return shape) at the entry for callers/tests.
PollStats = send_poll_core.PollStats

SCRIPT_NAME = "po_materials.rfq_send_poll"
WORKSTREAM = "po_materials"

# ITS_Config keys (RFQ-send-scoped; never the safety/progress/PO/subcontract keys).
CFG_POLLING_ENABLED = "po_materials.rfq_send.polling_enabled"
CFG_POLL_INTERVAL = "po_materials.rfq_send.poll_interval_seconds"
CFG_SCHEDULED_SEND_LOCAL = "po_materials.rfq_send.scheduled_send_local"
DEFAULT_SCHEDULED_SEND_LOCAL = "MON 07:00"
SEND_TZ = "America/Los_Angeles"

# CO-1 / HOUSE_REFLEXES §5 (dark-ship default-False): a MISSING/malformed
# `po_materials.rfq_send.polling_enabled` row must fail SAFE (send daemon disabled), never
# fail-open to SENDING. The seeded `false` row is load-bearing for normal dark operation;
# this default only governs the row-absent case. A send gate never fails open.
DEFAULT_POLLING_ENABLED = False
DEFAULT_POLL_INTERVAL = 900  # 15 minutes

# State paths. The heartbeat-row-id cache is the SHARED cross-daemon file (ARCH-2);
# only the liveness + lock files are per-daemon.
STATE_DIR = Path.home() / "its" / "state"
HEARTBEAT_PATH = STATE_DIR / "rfq_send_heartbeat.txt"
LOCK_PATH = STATE_DIR / "rfq_send.lock"
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"

DAEMON_NAME = "po_materials.rfq_send_poll"

WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "rfq_send_poll"

_REGISTRATION_INTERVAL_SECONDS = DEFAULT_POLL_INTERVAL
_REGISTRATION_SOURCE_ID = f"RFQ_Pending_Review ({sheet_ids.SHEET_RFQ_PENDING_REVIEW})"

# Shared ITS_Daemon_Health reporter for this daemon.
_heartbeat_reporter = HeartbeatReporter(
    script_name=SCRIPT_NAME,
    daemon_name=DAEMON_NAME,
    workstream=WORKSTREAM,
    liveness_path=HEARTBEAT_PATH,
    interval_seconds=_REGISTRATION_INTERVAL_SECONDS,
    source_id=_REGISTRATION_SOURCE_ID,
    row_state_path=HEARTBEAT_ROW_STATE_PATH,
)

# Send Status values the poller dispatches on. SENDING is EXCLUDED — the load-bearing
# no-double-send exclusion (see send_poll_core §42 + DaemonConfig __post_init__).
# PENDING + FAILED are candidates (the shared WSR/WPR/PO/SC/RFQ picklist re-exported by
# rfq_review from wsr_review).
DISPATCH_STATUSES = frozenset({rfq_review.STATUS_PENDING, rfq_review.STATUS_FAILED})

# F22 wake reasons (operator page) vs forensic-only.
_WAKE_REASONS = frozenset({
    approval_verification.VerdictReason.UNAUTHORIZED_ACTOR,
    approval_verification.VerdictReason.EMPTY_ALLOWLIST,
})


# ---- The one RFQ-send DaemonConfig ---------------------------------------
#
# NO field defaults to a safety/progress/PO/subcontract value (contamination gate,
# send_poll_core §42). `send_fn` is a LATE-BINDING lambda so `rfq_send.send_one_row` stays
# patchable; it routes through the shared `weekly_send.send_one_row` engine with the RFQ
# SendConfig (rfq_send.CONFIG), which resolves recipients only from ITS_Vendors.
# `f22_workspace_id` is the ITS — Purchase Orders workspace — the approver authority for RFQ
# sends is membership of THAT workspace (§46, the SAME procurement approver set as POs).

CONFIG = send_poll_core.DaemonConfig(
    script_name=SCRIPT_NAME,
    config_workstream=WORKSTREAM,
    daemon_name=DAEMON_NAME,
    lock_path=LOCK_PATH,
    watchdog_marker_dir=WATCHDOG_MARKER_DIR,
    watchdog_job_slug=WATCHDOG_JOB_SLUG,
    cfg_polling_enabled=CFG_POLLING_ENABLED,
    default_polling_enabled=DEFAULT_POLLING_ENABLED,
    cfg_scheduled_send_local=CFG_SCHEDULED_SEND_LOCAL,
    default_scheduled_send_local=DEFAULT_SCHEDULED_SEND_LOCAL,
    send_tz=SEND_TZ,
    poll_sheet_id=sheet_ids.SHEET_RFQ_PENDING_REVIEW,
    # RFQ_Pending_Review ships DARK as a 0 placeholder (builder-precedes-seed —
    # sheet_ids.SHEET_RFQ_PENDING_REVIEW is 0 until the operator runs
    # build_rfq_pending_review_sheet.py and flips the id). This lets the module import while
    # dark; the send gate keeps the daemon a no-op until go-live flips BOTH the sheet id and
    # po_materials.rfq_send.polling_enabled (a FIXED high-class operator action).
    allow_placeholder_sheet=True,
    f22_workspace_id=sheet_ids.WORKSPACE_PURCHASE_ORDERS,
    col_send_now=rfq_review.COL_SEND_NOW,
    col_approve_scheduled=rfq_review.COL_APPROVE_SCHEDULED,
    col_send_status=rfq_review.COL_SEND_STATUS,
    col_notes=rfq_review.COL_NOTES,
    col_approved_by=rfq_review.COL_APPROVED_BY,
    col_approved_at=rfq_review.COL_APPROVED_AT,
    dispatch_statuses=DISPATCH_STATUSES,
    status_pending=rfq_review.STATUS_PENDING,
    status_failed=rfq_review.STATUS_FAILED,
    max_send_retries=weekly_send.MAX_SEND_RETRIES,
    parse_retry_count=weekly_send._parse_retry_count,
    to_datetime=rfq_review.to_wsr_datetime,
    wake_reasons=_WAKE_REASONS,
    send_fn=lambda row_id: rfq_send.send_one_row(row_id),
)

# #336 — the ITS_Config keys this daemon resolves at RUNTIME (polling_enabled +
# scheduled_send_local on the DaemonConfig, read by send_poll_core under
# CONFIG.config_workstream='po_materials'). *.poll_interval_seconds is EXCLUDED (baked into
# the plist at install). from_mailbox IS re-declared here — this poll daemon is the
# PRODUCTION driver (send_fn → rfq_send.send_one_row reads from_mailbox every dispatch);
# rfq_send.main is the manual-rerun path, OFF the daemon.
REQUIRED_CONFIG: list[ConfigKey] = [
    ConfigKey(CONFIG.cfg_polling_enabled, CONFIG.config_workstream, CONFIG.default_polling_enabled, "bool"),
    ConfigKey(CONFIG.cfg_scheduled_send_local, CONFIG.config_workstream, CONFIG.default_scheduled_send_local, "str"),
    ConfigKey(rfq_send.CONFIG.from_mailbox_cfg_key, rfq_send.CONFIG.config_workstream,
              rfq_send.CONFIG.from_mailbox_default, "str"),
]


# ---- Test-mock seams (the suite patches these exact symbols) --------------
# The core calls these via INJECTION (resolved from this module at poll-call
# time), so patching them here still bites inside a poll cycle.


def _write_heartbeat() -> None:
    """Liveness file touch — thin delegator to the shared HeartbeatReporter."""
    _heartbeat_reporter.write_liveness()


def _write_heartbeat_row(
    *,
    status: HeartbeatStatus,
    items_processed: int,
    error_summary: str | None = None,
    correlation_id: str | None = None,
    notes: str | None = None,
    daemon_name: str = DAEMON_NAME,
) -> None:
    """ITS_Daemon_Health per-cycle row update — thin delegator to the reporter."""
    _heartbeat_reporter.write_row(
        status=status,
        items_processed=items_processed,
        error_summary=error_summary,
        correlation_id=correlation_id,
        notes=notes,
        daemon_name=daemon_name,
    )


def _write_watchdog_marker() -> None:
    """Touch the Check C freshness marker — delegates to the core with CONFIG."""
    send_poll_core._write_watchdog_marker(CONFIG)


def _stamp_approval(row_id: int, verdict: approval_verification.ApprovalVerdict) -> None:
    """Stamp the verified approver — delegates to the core with CONFIG."""
    send_poll_core._stamp_approval(CONFIG, row_id, verdict)


# Pure helpers re-exported so direct callers/tests (and the injection) resolve
# them on THIS module.
def _is_scheduled_window(now_local: datetime, spec: str) -> bool:
    return send_poll_core._is_scheduled_window(now_local, spec)


def _parse_scheduled_spec(spec: str):  # -> tuple[int, time]
    return send_poll_core._parse_scheduled_spec(spec)


def _filter_dispatch_candidates(rows):
    return send_poll_core._filter_dispatch_candidates(CONFIG, rows)


def _load_authorized_approvers():
    return send_poll_core._load_authorized_approvers(CONFIG)


def _read_str_setting(key: str, fallback: str) -> str:
    """ITS_Config string read (po_materials workstream) — kept as the smoke-test seam."""
    return send_poll_core._read_str_setting(CONFIG, key, fallback)


# The injected I/O seams are resolved from THIS module at call time (passed
# explicitly below, not via **kwargs, so mypy verifies the callable types) so the
# suite's entry-module patches apply inside a poll cycle.


def _poll_inside_lock() -> PollStats:
    """Body of poll_once under the file lock — delegates to the core with CONFIG."""
    return send_poll_core.poll_inside_lock(
        CONFIG,
        write_liveness=_write_heartbeat,
        write_row=_write_heartbeat_row,
        write_watchdog_marker=_write_watchdog_marker,
        stamp_approval=_stamp_approval,
        is_scheduled_window=_is_scheduled_window,
    )


# ---- Public API ----------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def poll_once() -> PollStats:
    """Run one poll cycle. Public API; idempotent across crashes."""
    # #336 startup observability (after @require_active, fail-open). Additive (§14).
    resolve_and_log(SCRIPT_NAME, REQUIRED_CONFIG)

    return send_poll_core.poll_once(
        CONFIG,
        write_liveness=_write_heartbeat,
        write_row=_write_heartbeat_row,
        write_watchdog_marker=_write_watchdog_marker,
        stamp_approval=_stamp_approval,
        is_scheduled_window=_is_scheduled_window,
    )


if __name__ == "__main__":
    poll_once()
