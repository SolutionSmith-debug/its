"""Unit + integration tests for safety_reports/intake_poll.py.

Unit tests mock graph_client + intake.process_message + filesystem
state. Integration test (gated `pytest -m integration`) drives a real
Graph send + poll cycle against the sandbox tenant.

Replaces the prior tests/test_intake_integration.py — the .eml file
pipeline is gone, so the file-based integration test became obsolete
when PR #59 cut over to Graph polling. The XOR routing assertion
(exactly one of Daily Reports row OR Review Queue row was created)
moved into this file's integration test.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from safety_reports import intake_poll
from safety_reports.intake import ProcessResult

SANDBOX_MAILBOX = "safety@evergreenmirror.com"


def _make_result(
    status: str,
    message_id: str = "msg-1",
    correlation_id: str = "corr-1",
    notes: str | None = None,
) -> ProcessResult:
    return ProcessResult(
        status=status,  # type: ignore[arg-type]
        message_id=message_id,
        correlation_id=correlation_id,
        notes=notes,
    )


# ---- Fixture: redirect state paths to tmp_path -------------------------


@pytest.fixture
def state_in_tmp(monkeypatch, tmp_path: Path):
    """Redirect SEEN_PATH / HEARTBEAT_PATH / LOCK_PATH into tmp_path.

    The poller's state files normally live under ~/its/state. Tests
    redirect them so concurrent test runs / CI don't share state. The
    fixture has no useful return value — it's a side-effect fixture.
    """
    seen = tmp_path / "safety_intake_processed.json"
    heartbeat = tmp_path / "safety_intake_heartbeat.txt"
    lock = tmp_path / "safety_intake.lock"
    monkeypatch.setattr(intake_poll, "STATE_DIR", tmp_path)
    monkeypatch.setattr(intake_poll, "SEEN_PATH", seen)
    monkeypatch.setattr(intake_poll, "HEARTBEAT_PATH", heartbeat)
    monkeypatch.setattr(intake_poll, "LOCK_PATH", lock)
    return tmp_path


@pytest.fixture
def kill_switch_active(mocker):
    """Patch the kill-switch to ACTIVE so @require_active passes through."""
    from shared.kill_switch import SystemState
    mocker.patch(
        "shared.kill_switch.check_system_state", return_value=SystemState.ACTIVE
    )


@pytest.fixture
def quiet_logs(mocker):
    """Silence error_log.log calls during poll tests — they hit Smartsheet."""
    mocker.patch("safety_reports.intake_poll.error_log.log")


@pytest.fixture
def polling_on(mocker):
    mocker.patch("safety_reports.intake_poll._polling_enabled", return_value=True)


@pytest.fixture
def mailbox_from_config(mocker):
    mocker.patch(
        "safety_reports.intake_poll._read_str_setting",
        side_effect=lambda key, fallback: SANDBOX_MAILBOX
        if key == intake_poll.CFG_MAILBOX else fallback,
    )


# ---- Disable-gate / lock tests -----------------------------------------


def test_poll_once_skips_when_disabled(mocker, state_in_tmp, kill_switch_active, quiet_logs):
    mocker.patch("safety_reports.intake_poll._polling_enabled", return_value=False)
    list_inbox = mocker.patch("safety_reports.intake_poll.graph_client.list_inbox")

    stats = intake_poll.poll_once()

    assert stats is not None  # @require_active didn't short-circuit
    assert stats.skipped_disabled is True
    list_inbox.assert_not_called()


def test_poll_once_skips_when_lock_held(
    mocker, state_in_tmp, kill_switch_active, quiet_logs, polling_on, tmp_path
):
    """If another poll_once holds the lock, the new invocation exits cleanly."""
    # Acquire the lock externally, then run poll_once — it should see
    # BlockingIOError on flock and yield acquired=False.
    import fcntl
    lockfile = intake_poll.LOCK_PATH
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    holder = lockfile.open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        list_inbox = mocker.patch(
            "safety_reports.intake_poll.graph_client.list_inbox"
        )
        stats = intake_poll.poll_once()
        assert stats.skipped_locked is True
        list_inbox.assert_not_called()
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


# ---- Per-message iteration -----------------------------------------------


def test_poll_once_iterates_unread_messages(
    mocker, state_in_tmp, kill_switch_active, quiet_logs, polling_on, mailbox_from_config
):
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[
            {"id": "msg-1", "isRead": False},
            {"id": "msg-2", "isRead": False},
        ],
    )
    proc = mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        side_effect=[
            _make_result("processed", message_id="msg-1"),
            _make_result("processed", message_id="msg-2"),
        ],
    )
    mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    stats = intake_poll.poll_once()

    assert stats.messages_fetched == 2
    assert stats.messages_processed == 2
    assert proc.call_count == 2
    assert proc.call_args_list[0].args[0] == "msg-1"
    assert proc.call_args_list[1].args[0] == "msg-2"


def test_poll_once_filters_out_already_read_messages(
    mocker, state_in_tmp, kill_switch_active, quiet_logs, polling_on, mailbox_from_config
):
    """graph_client.list_inbox returns read+unread; poller filters."""
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[
            {"id": "msg-read", "isRead": True},
            {"id": "msg-unread", "isRead": False},
        ],
    )
    proc = mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        return_value=_make_result("processed", message_id="msg-unread"),
    )
    mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    stats = intake_poll.poll_once()

    assert stats.messages_fetched == 1  # Only the unread one counted
    assert stats.messages_processed == 1
    assert proc.call_count == 1
    assert proc.call_args_list[0].args[0] == "msg-unread"


# ---- mark_read behavior -------------------------------------------------


@pytest.mark.parametrize(
    "status", ["processed", "review_queue", "quarantined", "skipped_swo_other"]
)
def test_poll_once_marks_read_on_success_statuses(
    status, mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[{"id": "msg-1", "isRead": False}],
    )
    mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        return_value=_make_result(status, message_id="msg-1"),
    )
    mark_read = mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    stats = intake_poll.poll_once()

    mark_read.assert_called_once_with(SANDBOX_MAILBOX, "msg-1")
    assert stats.messages_marked_read == 1


def test_poll_once_does_not_mark_read_on_error_status(
    mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[{"id": "msg-1", "isRead": False}],
    )
    mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        return_value=_make_result("error", message_id="msg-1"),
    )
    mark_read = mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    stats = intake_poll.poll_once()

    mark_read.assert_not_called()
    assert stats.messages_marked_read == 0
    assert stats.errors >= 1


def test_poll_once_records_error_when_mark_read_fails(
    mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    """A mark_read GraphError counts as an error but does NOT halt the loop."""
    from shared.graph_client import GraphRateLimitError
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[
            {"id": "msg-1", "isRead": False},
            {"id": "msg-2", "isRead": False},
        ],
    )
    mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        side_effect=[
            _make_result("processed", message_id="msg-1"),
            _make_result("processed", message_id="msg-2"),
        ],
    )
    mark_read = mocker.patch(
        "safety_reports.intake_poll.graph_client.mark_read",
        side_effect=[
            GraphRateLimitError("HTTP 429"),
            None,
        ],
    )

    stats = intake_poll.poll_once()

    assert mark_read.call_count == 2  # Loop continued past the first failure
    assert stats.messages_marked_read == 1
    assert stats.errors >= 1


# ---- Seen-set guard ------------------------------------------------------


def test_poll_once_skips_messages_already_in_seen_set(
    mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    # Seed the seen-set state file with msg-1 already recorded.
    seen_state = {
        "msg-1": {
            "timestamp": datetime.now(UTC).isoformat(),
            "status": "processed",
        }
    }
    intake_poll.SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    intake_poll.SEEN_PATH.write_text(json.dumps(seen_state))

    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[
            {"id": "msg-1", "isRead": False},
            {"id": "msg-2", "isRead": False},
        ],
    )
    proc = mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        return_value=_make_result("processed", message_id="msg-2"),
    )
    mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    stats = intake_poll.poll_once()

    assert stats.messages_skipped_seen == 1
    assert proc.call_count == 1
    assert proc.call_args.args[0] == "msg-2"


def test_poll_once_records_message_to_seen_after_processing(
    mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[{"id": "msg-99", "isRead": False}],
    )
    mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        return_value=_make_result("processed", message_id="msg-99"),
    )
    mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    intake_poll.poll_once()

    assert intake_poll.SEEN_PATH.exists()
    seen = json.loads(intake_poll.SEEN_PATH.read_text())
    assert "msg-99" in seen
    assert seen["msg-99"]["status"] == "processed"
    assert "timestamp" in seen["msg-99"]


def test_poll_once_records_error_status_to_seen(
    mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    """Error-status messages still get recorded in the seen-set.

    Rationale: if the same error_id is fetched again on the next cycle,
    the seen-set guard prevents re-processing — operator must explicitly
    clear the entry or rerun via the CLI. This avoids tight-loop retry
    of a permanently-broken message (e.g., malformed Graph payload).
    Wait — actually the seen-set check happens BEFORE process_message,
    so a recorded error means "we tried, it didn't work, don't try
    again." That's the right behavior for fast-failing errors. For
    transient errors (Graph 429), the operator clears the seen-set entry.
    """
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[{"id": "msg-err", "isRead": False}],
    )
    mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        return_value=_make_result("error", message_id="msg-err", notes="bad"),
    )
    mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    intake_poll.poll_once()

    seen = json.loads(intake_poll.SEEN_PATH.read_text())
    assert seen["msg-err"]["status"] == "error"


# ---- Heartbeat -----------------------------------------------------------


def test_poll_once_writes_heartbeat_after_processing(
    mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[{"id": "msg-1", "isRead": False}],
    )
    mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        return_value=_make_result("processed", message_id="msg-1"),
    )
    mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    intake_poll.poll_once()

    assert intake_poll.HEARTBEAT_PATH.exists()
    ts_str = intake_poll.HEARTBEAT_PATH.read_text()
    # Round-trip the ISO timestamp to confirm it parses.
    parsed = datetime.fromisoformat(ts_str)
    assert parsed.tzinfo is not None


def test_poll_once_writes_heartbeat_even_with_empty_inbox(
    mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    """An empty inbox still bumps the heartbeat — the trigger is alive."""
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[],
    )
    mocker.patch("safety_reports.intake_poll.intake.process_message")
    mocker.patch("safety_reports.intake_poll.graph_client.mark_read")

    intake_poll.poll_once()

    assert intake_poll.HEARTBEAT_PATH.exists()


# ---- Exception propagation ----------------------------------------------


def test_poll_once_propagates_unexpected_process_message_exception(
    mocker, state_in_tmp, kill_switch_active, quiet_logs,
    polling_on, mailbox_from_config,
):
    """process_message raises an unknown exception type — propagates up.

    Known soft failures (GraphError, SmartsheetError) get caught inside
    process_message and returned as status='error'. Anything else
    (programming bugs, third-party SDK regressions) must bubble up so
    @its_error_log on poll_once captures the traceback into ITS_Errors
    and fires the CRITICAL alert. The poll loop's `for msg in messages`
    halts on the raise — subsequent messages stay unread until the next
    cycle, which is the right behavior for a code bug.
    """
    mocker.patch(
        "safety_reports.intake_poll.graph_client.list_inbox",
        return_value=[{"id": "msg-1", "isRead": False}],
    )
    mocker.patch(
        "safety_reports.intake_poll.intake.process_message",
        side_effect=RuntimeError("unexpected bug"),
    )
    # @its_error_log on poll_once re-raises after logging; we expect the
    # exception to surface here.
    mocker.patch("shared.error_log.log")
    mocker.patch("shared.error_log._alert_critical")

    with pytest.raises(RuntimeError, match="unexpected bug"):
        intake_poll.poll_once()


# ---- State helpers (pure-function tests) -------------------------------


def test_load_seen_returns_empty_for_missing_file(state_in_tmp):
    assert not intake_poll.SEEN_PATH.exists()
    assert intake_poll._load_seen() == {}


def test_load_seen_returns_empty_for_corrupt_json(state_in_tmp):
    intake_poll.SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    intake_poll.SEEN_PATH.write_text("{not valid json")
    assert intake_poll._load_seen() == {}


def test_record_seen_trims_to_cap(state_in_tmp, monkeypatch):
    monkeypatch.setattr(intake_poll, "SEEN_CAP", 3)
    seen: dict[str, dict[str, str]] = {}
    # Insert 5 entries with monotonically increasing timestamps so the
    # FIFO trim has a deterministic answer.
    base = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)
    for i in range(5):
        seen[f"msg-{i}"] = {
            "timestamp": (base.replace(second=i)).isoformat(),
            "status": "processed",
        }
    # The next _record_seen call will append + trim. Set its timestamp
    # to slightly later than the last existing one.
    monkeypatch.setattr(
        intake_poll,
        "datetime",
        type("D", (), {"now": staticmethod(lambda tz=None: base.replace(second=10))}),
    )
    intake_poll._record_seen(seen, "msg-new", "processed")

    assert len(seen) == 3
    # Most-recent 3: msg-new (sec=10), msg-4 (sec=4), msg-3 (sec=3).
    assert "msg-new" in seen
    assert "msg-4" in seen
    assert "msg-3" in seen
    # Trimmed out:
    assert "msg-0" not in seen
    assert "msg-1" not in seen
    assert "msg-2" not in seen


def test_write_heartbeat_writes_iso_timestamp(state_in_tmp):
    intake_poll._write_heartbeat()
    raw = intake_poll.HEARTBEAT_PATH.read_text()
    parsed = datetime.fromisoformat(raw)
    assert parsed.tzinfo is not None


def test_polling_enabled_reads_from_config_default_true(mocker):
    mocker.patch(
        "safety_reports.intake_poll._read_str_setting",
        side_effect=lambda key, fallback: fallback,
    )
    assert intake_poll._polling_enabled() is True


def test_polling_enabled_reads_false_from_config(mocker):
    mocker.patch(
        "safety_reports.intake_poll._read_str_setting",
        return_value="false",
    )
    assert intake_poll._polling_enabled() is False


def test_file_lock_skips_when_held(state_in_tmp, tmp_path):
    import fcntl
    lockfile = tmp_path / "demo.lock"
    holder = lockfile.open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with intake_poll._file_lock(lockfile) as acquired:
            assert acquired is False
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_file_lock_acquires_when_free(tmp_path):
    lockfile = tmp_path / "demo.lock"
    with intake_poll._file_lock(lockfile) as acquired:
        assert acquired is True
    # Lock released after context — re-acquire succeeds.
    with intake_poll._file_lock(lockfile) as acquired:
        assert acquired is True


# ---- Integration test (gated) ------------------------------------------

# The integration test sends one message via Graph to the sandbox safety
# mailbox, runs poll_once, then verifies the message was processed end-
# to-end (Smartsheet row written, message marked as read) and cleans up.
# Reuses the helpers from the deleted tests/test_intake_integration.py
# adapted for the Graph-based path.
#
# Default `pytest -q` SKIPS this test via the pyproject `addopts = -m 'not
# integration'` mark. Operator runs with:
#
#     pytest -m integration tests/test_intake_poll.py
#
# Requires ITS_SMARTSHEET_TOKEN, ITS_ANTHROPIC_KEY, ITS_MS_* (Graph), and
# Box OAuth keychain entries. Without any of those the fixtures skip.

import requests as _integration_requests  # type: ignore[import-untyped]  # noqa: E402

from shared import box_client as _box_client  # noqa: E402
from shared import keychain as _keychain  # noqa: E402
from shared import sheet_ids as _sheet_ids  # noqa: E402
from shared import smartsheet_client as _smartsheet_client  # noqa: E402

INTEGRATION_SENDER = "intake_integration@evergreenmirror.com"
INTEGRATION_PROJECT = "Bradley 1"


@pytest.fixture(scope="module")
def _smartsheet_token() -> str:
    try:
        token = _keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN empty")
    return token


@pytest.fixture(scope="module")
def _anthropic_available() -> None:
    try:
        _keychain.get_secret("ITS_ANTHROPIC_KEY")
    except Exception as e:
        pytest.skip(f"ITS_ANTHROPIC_KEY unavailable: {e!r}")


@pytest.fixture(scope="module")
def _box_available() -> None:
    try:
        _box_client.get_client().user().get()
    except Exception as e:
        pytest.skip(f"Box OAuth unavailable: {e!r}")


@pytest.fixture(scope="module")
def _graph_available() -> None:
    try:
        _keychain.get_secret("ITS_MS_TENANT_ID")
        _keychain.get_secret("ITS_MS_CLIENT_ID")
        _keychain.get_secret("ITS_MS_CLIENT_SECRET")
    except Exception as e:
        pytest.skip(f"Graph credentials unavailable: {e!r}")


def _delete_row(sheet_id: int, row_id: int, token: str) -> None:
    _integration_requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}/rows?ids={row_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _find_daily_reports_row(sheet_id: int, marker: str) -> dict | None:
    rows = _smartsheet_client.get_rows(
        sheet_id, filters={"Safety Topic / Report Title": marker}
    )
    return rows[0] if rows else None


def _find_review_queue_row_by_message_id(message_id: str) -> dict | None:
    """Find a Review Queue row whose Source File matches the message_id.

    Per the PR #59 refactor, process_message passes the Graph message_id
    in place of the prior .eml file path on every review-queue write.
    """
    rows = _smartsheet_client.get_rows(
        _sheet_ids.SHEET_REVIEW_QUEUE,
        filters={"Source File": message_id, "Workstream": "safety_reports"},
    )
    return rows[0] if rows else None


def _add_sandbox_sender_to_allowlist(_token: str) -> int | None:
    rows = _smartsheet_client.get_rows(
        _sheet_ids.SHEET_CONFIG,
        filters={
            "Setting": "safety_reports.intake.allowed_senders",
            "Workstream": "safety_reports",
        },
    )
    if not rows:
        return None
    row = rows[0]
    row_id = int(row["_row_id"])
    original_value = row.get("Value") or "[]"
    try:
        senders = list(json.loads(original_value))
    except json.JSONDecodeError:
        senders = []
    if INTEGRATION_SENDER not in senders:
        senders.append(INTEGRATION_SENDER)
    _smartsheet_client.update_rows(
        _sheet_ids.SHEET_CONFIG,
        [{"_row_id": row_id, "Value": json.dumps(senders)}],
    )
    return row_id


def _restore_allowlist(row_id: int) -> None:
    rows = _smartsheet_client.get_rows(
        _sheet_ids.SHEET_CONFIG,
        filters={
            "Setting": "safety_reports.intake.allowed_senders",
            "Workstream": "safety_reports",
        },
    )
    if not rows:
        return
    row = rows[0]
    current_value = row.get("Value") or "[]"
    try:
        senders = list(json.loads(current_value))
    except json.JSONDecodeError:
        return
    new_senders = [s for s in senders if s != INTEGRATION_SENDER]
    _smartsheet_client.update_rows(
        _sheet_ids.SHEET_CONFIG,
        [{"_row_id": row["_row_id"], "Value": json.dumps(new_senders)}],
    )


@pytest.mark.integration
def test_poll_integration_sends_via_graph_and_processes(
    _smartsheet_token: str,
    _anthropic_available: None,
    _box_available: None,
    _graph_available: None,
    state_in_tmp,
    kill_switch_active,
) -> None:
    """End-to-end: Graph send → poll_once → Daily Reports row XOR Review Queue row.

    Sends one synthetic safety report from the sandbox sender to the
    sandbox safety mailbox, polls once, asserts exactly one of the two
    routing paths produced a row, then cleans up everything we created.

    Why XOR (not just "daily-reports row exists"): the pipeline's
    decision between Daily Reports vs Review Queue depends on the live
    model's classification confidence + anomaly self-report against the
    configured threshold + sentinel list. A synthetic test email can
    land at either path; both prove the pipeline is wired end-to-end.
    Asserting XOR catches a duplicated-write bug (both rows present) or
    a silent-drop bug (neither row present).
    """
    from shared import graph_client

    ts = datetime.now(UTC).strftime("%H%M%S")
    marker = f"_int_intake_poll_{ts}"
    subject = f"Bradley 1 — Daily JHA — {marker}"
    today_iso = date.today().isoformat()
    body = (
        f"Bradley 1 site, Daily JHA on {today_iso}. "
        f"Bradleys Solar Services crew on Block A. "
        f"Standard module replacement work, no incidents. "
        f"Report title: {marker}"
    )

    allowlist_row_id = _add_sandbox_sender_to_allowlist(_smartsheet_token)
    if allowlist_row_id is None:
        pytest.skip(
            "safety_reports.intake.allowed_senders config row missing; "
            "run scripts/migrations/seed_safety_intake_config.py first."
        )

    # Send the test message via Graph. The sender mailbox MUST be covered
    # by the app registration's Application Access Policy.
    graph_client.send_mail(
        from_mailbox=INTEGRATION_SENDER,
        to=[SANDBOX_MAILBOX],
        subject=subject,
        body=body,
        attachments=[
            {
                "name": "integration_test.pdf",
                "contentType": "application/pdf",
                "contentBytes": b"%PDF-1.4\n%integration test placeholder\n%%EOF\n",
            }
        ],
    )

    # Brief delay so Graph indexes the message before our poll lists the inbox.
    import time
    time.sleep(5)

    created_sheet_id: int | None = None
    created_box_file_ids: list[str] = []
    # We don't know the message_id until after Graph processes the send,
    # so we list the inbox to find it (newest first by receivedDateTime).
    listing = graph_client.list_inbox(
        SANDBOX_MAILBOX,
        top=10,
        fields=["id", "subject", "receivedDateTime", "isRead"],
    )
    matching = [m for m in listing if marker in (m.get("subject") or "")]
    assert matching, f"sent message not visible in inbox listing after 5s — subject contained {marker!r}"
    message_id = matching[0]["id"]

    try:
        # Run a single poll cycle. Patch _polling_enabled True (state_in_tmp
        # redirects state files), let intake.process_message hit live services.
        import unittest.mock as _mock
        with _mock.patch("safety_reports.intake_poll._polling_enabled", return_value=True):
            stats = intake_poll.poll_once()
        assert stats is not None
        assert stats.messages_fetched >= 1

        from safety_reports.week_folder import ensure_current_week_folder
        scaffold = ensure_current_week_folder(INTEGRATION_PROJECT)
        created_sheet_id = scaffold.daily_reports_sheet_id

        daily_row = _find_daily_reports_row(created_sheet_id, marker)
        review_row = _find_review_queue_row_by_message_id(message_id)

        present_count = (daily_row is not None) + (review_row is not None)
        assert present_count == 1, (
            f"intake routing-contract violated: expected exactly ONE of "
            f"Daily Reports row or Review Queue row; got "
            f"daily_row={daily_row!r}, review_row={review_row!r}."
        )

        if daily_row is not None:
            notes = daily_row.get("Notes / Action Items") or ""
            for match in re.finditer(r"app\.box\.com/file/(\d+)", notes):
                created_box_file_ids.append(match.group(1))
    finally:
        _restore_allowlist(allowlist_row_id)
        if created_sheet_id is not None:
            daily_row_cleanup = _find_daily_reports_row(created_sheet_id, marker)
            if daily_row_cleanup is not None:
                _delete_row(
                    created_sheet_id,
                    int(daily_row_cleanup["_row_id"]),
                    _smartsheet_token,
                )
        review_row_cleanup = _find_review_queue_row_by_message_id(message_id)
        if review_row_cleanup is not None:
            _delete_row(
                _sheet_ids.SHEET_REVIEW_QUEUE,
                int(review_row_cleanup["_row_id"]),
                _smartsheet_token,
            )
        for file_id in created_box_file_ids:
            try:
                _box_client.get_client().file(file_id).delete()
            except Exception:
                pass


