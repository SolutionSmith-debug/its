"""Tests for shared/smartsheet_client.py.

All SDK calls are mocked — these tests never hit Smartsheet's API and never
read the real Keychain. The module-level client + column-map caches are
reset between tests via the autouse `reset_state` fixture.

Run with: pytest -q tests/test_smartsheet_client.py
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import smartsheet.exceptions as sdk_exc

from shared import sheet_ids, smartsheet_client
from shared.smartsheet_client import (
    SmartsheetAuthError,
    SmartsheetError,
    SmartsheetNotFoundError,
    SmartsheetPermissionError,
    SmartsheetRateLimitError,
)

# ---- Fixtures + helpers --------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state(mocker):
    """Reset module-level caches and stub keychain reads for every test."""
    mocker.patch.object(smartsheet_client, "_client", None)
    smartsheet_client._column_maps.clear()
    mocker.patch(
        "shared.smartsheet_client.keychain.get_secret",
        side_effect=lambda key, *a, **kw: f"fake-{key}",
    )


def _api_error(status_code: int, *, code: int = 0, message: str = "boom") -> sdk_exc.ApiError:
    """Build a realistic ApiError with status_code on its nested result."""
    result = SimpleNamespace(status_code=status_code, code=code, message=message)
    error = SimpleNamespace(result=result)
    return sdk_exc.ApiError(error, message=message)


def _install_client(mocker) -> MagicMock:
    """Patch get_client() to return a mock SDK client."""
    client = MagicMock()
    mocker.patch.object(smartsheet_client, "get_client", return_value=client)
    return client


def _column(col_id: int, title: str) -> SimpleNamespace:
    return SimpleNamespace(id=col_id, title=title)


def _row(row_id: int, cells: list[tuple[int, object]]) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        cells=[SimpleNamespace(column_id=cid, value=v) for cid, v in cells],
    )


# ---- get_client lazy init -------------------------------------------------


def test_get_client_lazy_inits_once(mocker):
    sdk = mocker.patch("shared.smartsheet_client.smartsheet.Smartsheet")
    sdk.return_value = MagicMock()

    c1 = smartsheet_client.get_client()
    c2 = smartsheet_client.get_client()

    assert c1 is c2
    assert sdk.call_count == 1
    # Token must come from keychain, not env or hardcode.
    sdk.assert_called_with("fake-ITS_SMARTSHEET_TOKEN", user_agent="its")
    sdk.return_value.errors_as_exceptions.assert_called_once_with(True)


# ---- Exception translation -----------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, SmartsheetAuthError),
        (403, SmartsheetPermissionError),
        (404, SmartsheetNotFoundError),
        (429, SmartsheetRateLimitError),
        (500, SmartsheetError),
    ],
)
def test_api_error_translated_by_status(mocker, status, expected):
    client = _install_client(mocker)
    client.Sheets.get_sheet.side_effect = _api_error(status, message="nope")

    with pytest.raises(expected, match="nope"):
        smartsheet_client.get_sheet(123)


def test_http_error_translated(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.side_effect = sdk_exc.HttpError(502, b"bad gateway")

    with pytest.raises(SmartsheetError, match="502"):
        smartsheet_client.get_sheet(123)


# ---- Column-map cache ----------------------------------------------------


def test_column_map_cached_after_first_fetch(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(1, "Setting"), _column(2, "Value")],
        rows=[],
    )

    assert smartsheet_client._column_map(99) == {"Setting": 1, "Value": 2}
    assert smartsheet_client._column_map(99) == {"Setting": 1, "Value": 2}

    assert client.Sheets.get_sheet.call_count == 1


def test_column_map_refetched_when_title_missing(mocker):
    # Cache built from sheet v1 (no "NewCol"); a row with "NewCol" forces a refetch
    # which now finds it. This is the *addition* case the doc covers.
    client = _install_client(mocker)
    v1 = SimpleNamespace(columns=[_column(1, "Setting"), _column(2, "Value")], rows=[])
    v2 = SimpleNamespace(
        columns=[_column(1, "Setting"), _column(2, "Value"), _column(3, "NewCol")],
        rows=[],
    )
    client.Sheets.get_sheet.side_effect = [v1, v2]

    smartsheet_client._column_map(7)  # warm the cache
    cells = smartsheet_client._resolve_cells(7, {"NewCol": "hi"})

    assert [c.column_id for c in cells] == [3]
    assert client.Sheets.get_sheet.call_count == 2


def test_renamed_column_still_keyerrors_after_refetch(mocker):
    # The rename failure mode the module docstring calls out: a refetch can't
    # invent the old title back into existence, so callers fail fast.
    client = _install_client(mocker)
    renamed = SimpleNamespace(columns=[_column(1, "NewName")], rows=[])
    client.Sheets.get_sheet.return_value = renamed

    # Cache is empty; _resolve_cells will fetch once (gets the renamed schema),
    # see "OldName" is missing, invalidate, refetch — and still miss.
    with pytest.raises(KeyError, match="OldName"):
        smartsheet_client._resolve_cells(7, {"OldName": "x"})

    # Refetch path means at least two get_sheet calls — confirms the
    # invalidate-and-refetch happened before we gave up.
    assert client.Sheets.get_sheet.call_count >= 2


def test_invalidate_column_cache_targeted_and_global(mocker):
    smartsheet_client._column_maps[1] = {"A": 10}
    smartsheet_client._column_maps[2] = {"B": 20}

    smartsheet_client.invalidate_column_cache(1)
    assert 1 not in smartsheet_client._column_maps
    assert 2 in smartsheet_client._column_maps

    smartsheet_client.invalidate_column_cache()
    assert smartsheet_client._column_maps == {}


# ---- get_rows + filtering -------------------------------------------------


def test_get_rows_returns_title_keyed_dicts(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(1, "Setting"), _column(2, "Value"), _column(3, "Workstream")],
        rows=[
            _row(101, [(1, "kill_switch_state"), (2, "ACTIVE"), (3, "global")]),
            _row(102, [(1, "anomaly_threshold"), (2, "0.85"), (3, "safety_reports")]),
        ],
    )

    rows = smartsheet_client.get_rows(42)

    assert rows == [
        {
            "_row_id": 101,
            "Setting": "kill_switch_state",
            "Value": "ACTIVE",
            "Workstream": "global",
        },
        {
            "_row_id": 102,
            "Setting": "anomaly_threshold",
            "Value": "0.85",
            "Workstream": "safety_reports",
        },
    ]


def test_get_rows_filters_match_all_keys_and_equality(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(1, "Setting"), _column(2, "Workstream")],
        rows=[
            _row(1, [(1, "x"), (2, "global")]),
            _row(2, [(1, "x"), (2, "safety_reports")]),
            _row(3, [(1, "y"), (2, "global")]),
        ],
    )

    rows = smartsheet_client.get_rows(
        42, filters={"Setting": "x", "Workstream": "global"}
    )
    assert [r["_row_id"] for r in rows] == [1]


# ---- get_setting ---------------------------------------------------------


def test_get_setting_returns_value_for_matching_workstream(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(1, "Setting"), _column(2, "Value"), _column(3, "Workstream")],
        rows=[
            _row(1, [(1, "kill_switch_state"), (2, "ACTIVE"), (3, "global")]),
            _row(2, [(1, "kill_switch_state"), (2, "PAUSED"), (3, "safety_reports")]),
        ],
    )

    assert (
        smartsheet_client.get_setting("kill_switch_state", workstream="safety_reports")
        == "PAUSED"
    )


def test_get_setting_requires_workstream_kwarg():
    # Positional call must fail — defending against the "silently default to
    # global" footgun that PR feedback called out.
    with pytest.raises(TypeError):
        smartsheet_client.get_setting("kill_switch_state", "global")  # type: ignore[misc]


def test_get_setting_missing_row_raises_not_found(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(1, "Setting"), _column(2, "Value"), _column(3, "Workstream")],
        rows=[],
    )

    with pytest.raises(SmartsheetNotFoundError, match="missing_key"):
        smartsheet_client.get_setting("missing_key", workstream="global")


def test_get_setting_queries_config_sheet(mocker):
    # Wiring check: the call must hit sheet_ids.SHEET_CONFIG, not a hardcoded
    # ID or some other sheet.
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(1, "Setting"), _column(2, "Value"), _column(3, "Workstream")],
        rows=[_row(1, [(1, "k"), (2, "v"), (3, "global")])],
    )

    smartsheet_client.get_setting("k", workstream="global")

    client.Sheets.get_sheet.assert_called_with(sheet_ids.SHEET_CONFIG)


# ---- add_rows / update_rows ----------------------------------------------


def test_add_rows_builds_payload_with_resolved_column_ids(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[
            _column(11, "Error"),
            _column(12, "Severity"),
            _column(13, "Script"),
        ],
        rows=[],
    )
    client.Sheets.add_rows.return_value = SimpleNamespace(
        result=[SimpleNamespace(id=9001), SimpleNamespace(id=9002)]
    )

    new_ids = smartsheet_client.add_rows(
        sheet_ids.SHEET_ERRORS,
        [
            {"Error": "boom", "Severity": "WARN", "Script": "watchdog"},
            {"Error": "second", "Severity": "INFO", "Script": "watchdog"},
        ],
    )

    assert new_ids == [9001, 9002]
    call_sheet_id, call_rows = client.Sheets.add_rows.call_args.args
    assert call_sheet_id == sheet_ids.SHEET_ERRORS
    assert len(call_rows) == 2
    # First row's resolved cells, by column_id:
    first_cells = {c.column_id: c.value for c in call_rows[0].cells}
    assert first_cells == {11: "boom", 12: "WARN", 13: "watchdog"}
    assert call_rows[0].to_bottom is True


def test_add_rows_empty_input_is_noop(mocker):
    client = _install_client(mocker)
    assert smartsheet_client.add_rows(42, []) == []
    client.Sheets.add_rows.assert_not_called()


def test_update_rows_requires_row_id(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(11, "Error")], rows=[]
    )
    with pytest.raises(ValueError, match="_row_id"):
        smartsheet_client.update_rows(42, [{"Error": "x"}])


def test_update_rows_strips_row_id_from_cells(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(11, "Error"), _column(12, "Severity")],
        rows=[],
    )

    smartsheet_client.update_rows(
        42, [{"_row_id": 500, "Error": "msg", "Severity": "INFO"}]
    )

    sent_rows = client.Sheets.update_rows.call_args.args[1]
    assert sent_rows[0].id == 500
    cell_titles = {c.column_id for c in sent_rows[0].cells}
    assert cell_titles == {11, 12}  # no spurious cell for _row_id


def test_write_error_translated(mocker):
    # add_rows must use the same translation path as reads.
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = SimpleNamespace(
        columns=[_column(11, "Error")], rows=[]
    )
    client.Sheets.add_rows.side_effect = _api_error(403, message="no write here")

    with pytest.raises(SmartsheetPermissionError, match="no write here"):
        smartsheet_client.add_rows(42, [{"Error": "x"}])


# ---- SDK 404 noise filter ------------------------------------------------


def _sdk_log_record(level: int, status_code: int) -> logging.LogRecord:
    """Build a LogRecord shaped exactly like the SDK's _log_request emission.

    The SDK calls `self._log.error('{...}', status_code, reason, content)` —
    the format-string's first positional arg is the status code. The filter
    inspects `record.args[0]`, so the record we build here is the minimum
    fixture that exercises that path.
    """
    return logging.LogRecord(
        name=smartsheet_client.SDK_LOGGER_NAME,
        level=level,
        pathname=__file__,
        lineno=0,
        msg='{"response": {"statusCode": %d, "reason": "%s", "content": %s}}',
        args=(status_code, "Not Found", '{"errorCode": 1006, "message": "Not Found"}'),
        exc_info=None,
    )


def test_404_filter_installed_on_sdk_logger():
    sdk_logger = logging.getLogger(smartsheet_client.SDK_LOGGER_NAME)
    assert any(
        isinstance(f, smartsheet_client._Suppress404JSON) for f in sdk_logger.filters
    ), "_Suppress404JSON filter was not installed at module-import time"


def test_404_filter_drops_404_error_record():
    filt = smartsheet_client._Suppress404JSON()
    record = _sdk_log_record(logging.ERROR, 404)

    assert filt.filter(record) is False


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_404_filter_passes_other_status_codes(status):
    filt = smartsheet_client._Suppress404JSON()
    record = _sdk_log_record(logging.ERROR, status)

    assert filt.filter(record) is True


def test_404_filter_passes_non_error_levels():
    # INFO/DEBUG/WARN records pass through even when args[0] would match —
    # only ERROR is the noise we care about.
    filt = smartsheet_client._Suppress404JSON()
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.CRITICAL):
        record = _sdk_log_record(level, 404)
        assert filt.filter(record) is True, f"level {level} was unexpectedly dropped"


def test_404_filter_passes_records_with_no_args():
    # Defensive: an ERROR record with no formatting args has nothing to inspect.
    filt = smartsheet_client._Suppress404JSON()
    record = logging.LogRecord(
        name=smartsheet_client.SDK_LOGGER_NAME,
        level=logging.ERROR,
        pathname=__file__,
        lineno=0,
        msg="some bare string",
        args=None,
        exc_info=None,
    )
    assert filt.filter(record) is True


def test_404_filter_suppresses_emission_through_real_logger(caplog):
    # End-to-end via the actual logger: emit through the installed pipeline
    # and confirm caplog never sees the 404 record. caplog hooks into the
    # logger's filter chain, so a filter-dropped record stays out.
    with caplog.at_level(logging.ERROR, logger=smartsheet_client.SDK_LOGGER_NAME):
        sdk_logger = logging.getLogger(smartsheet_client.SDK_LOGGER_NAME)
        sdk_logger.error(
            '{"response": {"statusCode": %d, "reason": "%s", "content": %s}}',
            404,
            "Not Found",
            '{"errorCode": 1006}',
        )
        sdk_logger.error(
            '{"response": {"statusCode": %d, "reason": "%s", "content": %s}}',
            500,
            "Server Error",
            '{"errorCode": 9999}',
        )

    # The 500 record should pass; the 404 record should not.
    messages = [r.getMessage() for r in caplog.records]
    assert any("500" in m for m in messages)
    assert not any('"statusCode": 404' in m for m in messages)
