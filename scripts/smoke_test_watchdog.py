#!/usr/bin/env python3
"""Watchdog smoke runner — exercises each check against live infrastructure.

OPERATIONAL — makes REAL Smartsheet / Graph API calls and writes/deletes
rows in sandbox sheets. Sandbox-only. Each phase creates a controlled
condition, runs the relevant check, asserts expected behavior, then
cleans up via `try/finally` so a failed assertion still removes the test
artifact (matches scripts/smoke_test_review_queue.py "leaves no droppings"
discipline).

Re-run after:
  - Any change to scripts/watchdog.py
  - ITS_Config / ITS_Errors / ITS_Review_Queue schema changes
  - shared/graph_client.py changes affecting fetch_latest_inbound_timestamp
  - shared/scheduling.py changes affecting resolve_chain or TimeOffClient

Phases (one per check in scripts/watchdog.CHECKS, plus the deferred E):
  A. Stale review queue   — write back-dated PENDING row, expect WARN
  B. Open CRITICAL events — write open CRITICAL row, expect WARN
  C. Scheduled jobs       — write marker, verify _check_scheduled_jobs
                            stays INFO with TRACKED_JOBS empty
  D. Reviewer-chain fwd   — inject a synthetic empty-chain workstream,
                            expect one ANOMALY row in ITS_Review_Queue
  E. Spend trend          — DEFERRED to PR #37 (Admin API key required);
                            phase prints SKIPPED so the runner still
                            enumerates the full check set.
  F. Mail intake          — fetch the real safety@ latest-inbound
                            timestamp, run the check, log observed
                            idle-hours figure for operator triage
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

# scripts/ isn't a Python package — match the test_watchdog.py pattern.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import watchdog  # noqa: E402

from shared import review_queue, sheet_ids, smartsheet_client  # noqa: E402
from shared.error_log import Severity  # noqa: E402
from shared.review_queue import ReviewReason, ReviewStatus, SlaTier  # noqa: E402

SMOKE_TAG = f"ITS-WATCHDOG-SMOKE-{date.today().isoformat()}"


# ---- Phase helpers -------------------------------------------------------


class _LogCapture:
    """Context manager that captures all watchdog.log calls (severity, message)."""

    def __init__(self) -> None:
        self.calls: list[tuple[Severity, str]] = []
        self._patcher = None

    def __enter__(self):
        self._patcher = patch("watchdog.log", side_effect=self._record)
        self._patcher.start()
        return self

    def __exit__(self, *exc):
        assert self._patcher is not None
        self._patcher.stop()

    def _record(self, severity, _script, message, **_kw):
        self.calls.append((severity, message))

    def severities(self) -> list[Severity]:
        return [s for s, _ in self.calls]


# ---- Phase A: stale review queue ----------------------------------------


def smoke_check_a() -> None:
    """Write a PENDING row Created At=8 days ago (past 2× 48h SLA). Expect WARN."""
    today = date.today()
    backdated = (today - timedelta(days=8)).isoformat()
    row_id = smartsheet_client.add_rows(
        sheet_ids.SHEET_REVIEW_QUEUE,
        [{
            "Item ID": f"{SMOKE_TAG}-stale-A",
            "Created At": backdated,
            "Workstream": "global",
            "Summary": "smoke A: synthetic stale row — safe to delete",
            "Reason": ReviewReason.OTHER.value,
            "Severity": Severity.INFO.value,
            "SLA Tier": SlaTier.SUBCONTRACT_DRAFT.value,
            "Source File": __file__,
            "Payload": json.dumps({"smoke": "A"}),
            "Status": ReviewStatus.PENDING.value,
            "Security Flag": False,
        }],
    )[0]
    try:
        result = watchdog._check_stale_review_queue()
        assert result.severity is Severity.WARN, f"expected WARN, got {result.severity}"
        assert f"{SMOKE_TAG}-stale-A" in result.details, (
            f"expected our synthetic Item ID in details: {result.details!r}"
        )
    finally:
        smartsheet_client.delete_rows(sheet_ids.SHEET_REVIEW_QUEUE, [row_id])


# ---- Phase B: open CRITICAL events --------------------------------------


def smoke_check_b() -> None:
    """Write a CRITICAL row with blank Resolved At. Expect WARN naming it."""
    row_id = smartsheet_client.add_rows(
        sheet_ids.SHEET_ERRORS,
        [{
            "Error": f"{SMOKE_TAG}-critical-B",
            "Severity": "CRITICAL",
            "Script": __file__,
            "Message": "smoke B: synthetic CRITICAL — safe to delete",
        }],
    )[0]
    try:
        result = watchdog._check_open_criticals()
        assert result.severity is Severity.WARN, f"expected WARN, got {result.severity}"
        assert f"{SMOKE_TAG}-critical-B" in result.details, (
            f"expected our synthetic Error code in details: {result.details!r}"
        )
    finally:
        smartsheet_client.delete_rows(sheet_ids.SHEET_ERRORS, [row_id])


# ---- Phase C: scheduled jobs scaffold + marker writes -------------------


def smoke_check_c() -> None:
    """Verify write_last_run_marker writes a parseable timestamp; verify
    _check_scheduled_jobs stays INFO while TRACKED_JOBS is empty."""
    job_name = f"{SMOKE_TAG}-marker-C"
    watchdog.write_last_run_marker(job_name)
    marker = watchdog.WATCHDOG_MARKER_DIR / f"{job_name}.last_run"
    try:
        assert marker.exists(), f"marker not written: {marker}"
        parsed = datetime.fromisoformat(marker.read_text().strip())
        assert parsed.tzinfo is not None, "marker missing tzinfo"
        result = watchdog._check_scheduled_jobs()
        assert result.severity is Severity.INFO, f"expected INFO, got {result.severity}"
        assert watchdog.TRACKED_JOBS == [], (
            f"TRACKED_JOBS must be empty by design (got {watchdog.TRACKED_JOBS!r})"
        )
    finally:
        if marker.exists():
            marker.unlink()


# ---- Phase D: reviewer-chain forward scan -------------------------------


def smoke_check_d() -> None:
    """Inject an empty-chain workstream, run Check D, verify one ANOMALY
    row landed in ITS_Review_Queue. Cleanup deletes the row.

    Uses unittest.mock.patch as a context manager to substitute
    WORKSTREAMS_TO_SCAN + resolve_chain so we exercise the row-writing
    path against the real ITS_Review_Queue without polluting any real
    workstream's queue.
    """
    from types import SimpleNamespace

    def _empty_chain(*_a, **_kw):
        return SimpleNamespace(slots=(), is_empty=True)

    created_row_id: int | None = None
    original_add = review_queue.add

    def _spy_add(**kwargs):
        nonlocal created_row_id
        created_row_id = original_add(**kwargs)
        return created_row_id

    with (
        patch("watchdog.WORKSTREAMS_TO_SCAN", [f"{SMOKE_TAG}_workstream"]),
        patch("watchdog.resolve_chain", side_effect=_empty_chain),
        patch("watchdog.review_queue.add", side_effect=_spy_add),
    ):
        result = watchdog._check_reviewer_chain_forward()

    try:
        assert result.severity is Severity.INFO, f"expected INFO, got {result.severity}"
        assert "Logged" in result.summary and "anomaly row" in result.summary, (
            f"unexpected summary: {result.summary!r}"
        )
        assert created_row_id is not None, "spy_add was never called"
    finally:
        if created_row_id is not None:
            smartsheet_client.delete_rows(
                sheet_ids.SHEET_REVIEW_QUEUE, [created_row_id]
            )


# ---- Phase E: deferred --------------------------------------------------


def smoke_check_e() -> None:
    """Deferred to PR #37 — Admin API key prerequisite (sk-ant-admin01-...
    prefix; current Keychain entry holds a workspace key that 401s on
    /v1/organizations/cost_report). Phase exists so the runner enumerates
    the full check set; it prints SKIPPED and returns without exercising
    any check."""
    raise _PhaseSkippedError("deferred to PR #37 — Admin API key not provisioned")


class _PhaseSkippedError(Exception):
    """Raised by a smoke phase to indicate deliberate deferral, not failure."""


# ---- Phase F: mail intake silent-disable --------------------------------


def smoke_check_f() -> None:
    """Live: fetch real safety@ latest-inbound timestamp, then run Check F.

    Doesn't assert on the resulting Severity because that depends on
    inbox state (silent if no mail received in 96h; fresh otherwise) —
    asserts only that the check returns a valid CheckResult and that
    the Graph fetch succeeded.
    """
    from shared import graph_client
    mailbox = watchdog.WORKSTREAM_TO_MAILBOX["safety"]
    last_inbound = graph_client.fetch_latest_inbound_timestamp(mailbox)
    if last_inbound is None:
        print(f"      observed: {mailbox} has no inbound history")
    else:
        idle_h = (datetime.now(UTC) - last_inbound).total_seconds() / 3600
        print(f"      observed: {mailbox} idle {idle_h:.1f}h, threshold 96h")

    result = watchdog._check_mail_intake_silent_disable()
    assert isinstance(result, watchdog.CheckResult), f"unexpected: {result!r}"
    assert result.severity in (Severity.INFO, Severity.WARN), (
        f"unexpected severity: {result.severity}"
    )
    print(f"      check result: {result.severity.value} — {result.summary}")


# ---- Runner -------------------------------------------------------------


PHASES: list[tuple[str, Callable[[], Any]]] = [
    ("Check A (stale review queue)", smoke_check_a),
    ("Check B (open criticals)", smoke_check_b),
    ("Check C (scheduled jobs scaffold)", smoke_check_c),
    ("Check D (reviewer chain forward scan)", smoke_check_d),
    ("Check E (spend trend)", smoke_check_e),
    ("Check F (mail intake silent-disable)", smoke_check_f),
]


def main() -> None:
    print(f"ITS watchdog smoke runner — tag {SMOKE_TAG}")
    print("=" * 60)
    results: list[tuple[str, str]] = []

    for name, fn in PHASES:
        print(f"\n[{name}]")
        try:
            fn()
            results.append((name, "PASS"))
            print("      PASS")
        except _PhaseSkippedError as e:
            results.append((name, f"SKIPPED: {e}"))
            print(f"      SKIPPED: {e}")
        except AssertionError as e:
            results.append((name, f"FAIL: {e}"))
            print(f"      FAIL: {e}")
        except Exception as e:
            results.append((name, f"ERROR: {type(e).__name__}: {e}"))
            print(f"      ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print("Summary:")
    for name, status in results:
        marker = "PASS" if status == "PASS" else ("SKIPPED" if status.startswith("SKIPPED") else "FAIL")
        print(f"  [{marker:7}] {name}")

    failures = [n for n, s in results if not (s == "PASS" or s.startswith("SKIPPED"))]
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
