"""Tests for shared/job_sheet.py — the per-job tracking folder + sheet scaffold.

All Smartsheet calls are mocked — these tests never hit the API (mirrors
tests/test_week_folder.py). The mandatory live mirror smoke (new shared
infrastructure — feedback: mandatory-live-smoke) is run by the operator session
before merge, not here.

Run with: pytest -q tests/test_job_sheet.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared import job_sheet
from shared.error_log import Severity
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

PARENT = 111
TEMPLATE = 222


@pytest.fixture
def stub_smartsheet(mocker) -> dict[str, MagicMock]:
    """Patch the five smartsheet_client helpers used by job_sheet.

    `get_rows` is the create-path readiness probe (default: sheet readable
    immediately); per-test side_effects simulate the 1006 propagation window.
    """
    return {
        "find_folder": mocker.patch.object(
            job_sheet.smartsheet_client, "find_folder_by_name_in_folder"
        ),
        "create_folder": mocker.patch.object(
            job_sheet.smartsheet_client, "create_folder_in_folder"
        ),
        "find_sheet": mocker.patch.object(
            job_sheet.smartsheet_client, "find_sheet_by_name_in_folder"
        ),
        "copy_sheet": mocker.patch.object(
            job_sheet.smartsheet_client,
            "create_sheet_in_folder_from_template",
        ),
        "get_rows": mocker.patch.object(
            job_sheet.smartsheet_client, "get_rows", return_value=[]
        ),
    }


@pytest.fixture
def stub_sleep(mocker) -> MagicMock:
    """Stub the module-level `_sleep` seam so probe tests never wall-clock."""
    return mocker.patch.object(job_sheet, "_sleep")


@pytest.fixture
def stub_error_log(mocker) -> MagicMock:
    return mocker.patch.object(job_sheet.error_log, "log")


# ---- happy paths ---------------------------------------------------------


def test_existing_folder_and_sheet_no_creates(stub_smartsheet):
    """When the per-job folder + sheet already exist, no create calls fire."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].return_value = 42

    sid = job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")

    assert sid == 42
    stub_smartsheet["find_folder"].assert_called_once_with(PARENT, "Sunrise Solar")
    stub_smartsheet["find_sheet"].assert_called_once_with(500, "Subcontracts")
    stub_smartsheet["create_folder"].assert_not_called()
    stub_smartsheet["copy_sheet"].assert_not_called()


def test_first_time_creates_folder_and_clones_template(stub_smartsheet):
    """First invocation creates the folder, then clones the flat Log structure-only."""
    # find_folder: None (initial) then None again (race check post-create).
    stub_smartsheet["find_folder"].side_effect = [None, None]
    stub_smartsheet["create_folder"].return_value = 600
    # find_sheet: None (initial) then the just-created id (race check post-create).
    stub_smartsheet["find_sheet"].side_effect = [None, 4242]
    stub_smartsheet["copy_sheet"].return_value = 4242

    sid = job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Purchase Orders")

    assert sid == 4242
    stub_smartsheet["create_folder"].assert_called_once_with(PARENT, "Sunrise Solar")
    stub_smartsheet["copy_sheet"].assert_called_once_with(
        folder_id=600,
        name="Purchase Orders",
        template_sheet_id=TEMPLATE,
        include=[],
    )


def test_folder_exists_sheet_missing_only_clones_sheet(stub_smartsheet):
    """Orphan folder (e.g. a prior partial run): reuse it, clone only the sheet."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [None, 8888]
    stub_smartsheet["copy_sheet"].return_value = 8888

    sid = job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")

    assert sid == 8888
    stub_smartsheet["create_folder"].assert_not_called()
    stub_smartsheet["copy_sheet"].assert_called_once()


# ---- race-condition paths -------------------------------------------------


def test_folder_race_warns_and_uses_first_match(stub_smartsheet, stub_error_log):
    """Race: pre-create find None, create returns A, post-create find returns B (≠ A).
    WARN with the stable error_code and adopt B."""
    stub_smartsheet["find_folder"].side_effect = [None, 700]  # pre-find, post-find
    stub_smartsheet["create_folder"].return_value = 999
    stub_smartsheet["find_sheet"].return_value = 42

    sid = job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")

    assert sid == 42
    # The sheet lookup ran against the SURVIVOR folder, not the just-created orphan.
    stub_smartsheet["find_sheet"].assert_called_once_with(700, "Subcontracts")

    stub_error_log.assert_called_once()
    call = stub_error_log.call_args
    assert call.args[0] == Severity.WARN
    assert call.kwargs.get("error_code") == "job_sheet_folder_race_duplicate"
    # Both IDs appear in the message for operator cleanup.
    assert "999" in call.args[2] and "700" in call.args[2]


def test_sheet_race_warns_and_uses_first_match(stub_smartsheet, stub_error_log):
    """Race at the sheet level: copy returns A, post-create find returns B (≠ A)."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [None, 4300]  # pre-find, post-find
    stub_smartsheet["copy_sheet"].return_value = 4299

    sid = job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")

    assert sid == 4300  # the survivor, not the just-created 4299.

    stub_error_log.assert_called_once()
    call = stub_error_log.call_args
    assert call.args[0] == Severity.WARN
    assert call.kwargs.get("error_code") == "job_sheet_sheet_race_duplicate"
    assert "4299" in call.args[2] and "4300" in call.args[2]


# ---- defensive sheet-name cap ---------------------------------------------


def test_sheet_name_truncated_to_50_char_cap(stub_smartsheet):
    """A composite name over the Smartsheet cap (errorCode 1041) is truncated
    defensively before find AND create, so both legs agree on the key."""
    long_name = "X" * 60
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [None, 4242]
    stub_smartsheet["copy_sheet"].return_value = 4242

    job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", long_name)

    expected = "X" * job_sheet.SHEET_NAME_MAX
    assert stub_smartsheet["find_sheet"].call_args_list[0].args[1] == expected
    assert stub_smartsheet["copy_sheet"].call_args.kwargs["name"] == expected


# ---- error propagation -----------------------------------------------------


def test_smartsheet_error_propagates_to_caller(stub_smartsheet):
    """SmartsheetError propagates — the daemons' fenced per-job helpers classify it."""
    stub_smartsheet["find_folder"].side_effect = SmartsheetError("boom")
    with pytest.raises(SmartsheetError):
        job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")
    stub_smartsheet["copy_sheet"].assert_not_called()


# ---- create-path readiness probe (2026-07-13 live-smoke 1006 finding) ------


def test_create_path_retries_readiness_probe_on_1006_then_succeeds(
    stub_smartsheet, stub_sleep
):
    """The live-smoke class: add_rows-visible 404 (errorCode 1006) for a few
    seconds after the clone. The probe absorbs it — two not-ready probes, then
    readable — and the id is returned once the sheet answers."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [None, 4242]
    stub_smartsheet["copy_sheet"].return_value = 4242
    stub_smartsheet["get_rows"].side_effect = [
        SmartsheetNotFoundError("HTTP 404: errorCode 1006"),
        SmartsheetNotFoundError("HTTP 404: errorCode 1006"),
        [],  # readable
    ]

    sid = job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")

    assert sid == 4242
    assert stub_smartsheet["get_rows"].call_count == 3
    assert all(c.args == (4242,) for c in stub_smartsheet["get_rows"].call_args_list)
    assert stub_sleep.call_count == 2
    assert all(
        c.args == (job_sheet.READY_PROBE_DELAY_SECONDS,)
        for c in stub_sleep.call_args_list
    )


def test_find_path_never_probes_or_sleeps(stub_smartsheet, stub_sleep):
    """The probe is CREATE-path only — the hot find path (established sheets,
    long readable) stays zero-cost: no get_rows, no sleep."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].return_value = 42

    sid = job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")

    assert sid == 42
    stub_smartsheet["get_rows"].assert_not_called()
    stub_sleep.assert_not_called()


def test_readiness_probe_exhaustion_still_returns_id_and_warns(
    stub_smartsheet, stub_sleep, stub_error_log
):
    """Bounded, never hangs: after READY_PROBE_ATTEMPTS not-ready probes the id
    is returned anyway (the caller's fence absorbs a residual 404) with a WARN
    naming the stable error_code."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [None, 4242]
    stub_smartsheet["copy_sheet"].return_value = 4242
    stub_smartsheet["get_rows"].side_effect = SmartsheetNotFoundError("1006")

    sid = job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")

    assert sid == 4242
    assert stub_smartsheet["get_rows"].call_count == job_sheet.READY_PROBE_ATTEMPTS
    # Sleeps BETWEEN probes only — never after the final one (no dead wait).
    assert stub_sleep.call_count == job_sheet.READY_PROBE_ATTEMPTS - 1

    stub_error_log.assert_called_once()
    call = stub_error_log.call_args
    assert call.args[0] == Severity.WARN
    assert call.kwargs.get("error_code") == "job_sheet_ready_probe_exhausted"
    assert "4242" in call.args[2]


def test_readiness_probe_reraises_non_404(stub_smartsheet, stub_sleep):
    """Only SmartsheetNotFoundError means not-ready-yet; any other error is a
    real fault and re-raises immediately (no retry, no sleep)."""
    stub_smartsheet["find_folder"].return_value = 500
    stub_smartsheet["find_sheet"].side_effect = [None, 4242]
    stub_smartsheet["copy_sheet"].return_value = 4242
    stub_smartsheet["get_rows"].side_effect = SmartsheetError("500 server error")

    with pytest.raises(SmartsheetError):
        job_sheet.ensure_job_sheet(PARENT, TEMPLATE, "Sunrise Solar", "Subcontracts")

    stub_smartsheet["get_rows"].assert_called_once()
    stub_sleep.assert_not_called()
