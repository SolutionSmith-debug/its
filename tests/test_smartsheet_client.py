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


# ---- get_settings_with_prefix -------------------------------------------


def _config_rows_sheet(rows):
    """Build a fake sheet with the ITS_Config column layout for get_rows."""
    titles = ["Setting", "Value", "Workstream"]
    cols = [_column(i + 1, t) for i, t in enumerate(titles)]
    sheet_rows = []
    for i, r in enumerate(rows, start=1):
        cells = [
            (1, r.get("Setting")),
            (2, r.get("Value")),
            (3, r.get("Workstream")),
        ]
        sheet_rows.append(_row(i, cells))
    return SimpleNamespace(columns=cols, rows=sheet_rows)


def test_get_settings_with_prefix_filters_by_prefix(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = _config_rows_sheet([
        {"Setting": "mail_intake.safety.max_idle_hours",
         "Value": "96", "Workstream": "global"},
        {"Setting": "mail_intake.procurement.max_idle_hours",
         "Value": "48", "Workstream": "global"},
        {"Setting": "system.state", "Value": "ACTIVE", "Workstream": "global"},
        {"Setting": "spend.absolute_floor_usd",
         "Value": "5.00", "Workstream": "global"},
    ])
    out = smartsheet_client.get_settings_with_prefix("mail_intake.")
    assert out == {
        "mail_intake.safety.max_idle_hours": "96",
        "mail_intake.procurement.max_idle_hours": "48",
    }


def test_get_settings_with_prefix_workstream_filter(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = _config_rows_sheet([
        {"Setting": "x.key1", "Value": "1", "Workstream": "global"},
        {"Setting": "x.key2", "Value": "2", "Workstream": "safety_reports"},
    ])
    out = smartsheet_client.get_settings_with_prefix(
        "x.", workstream="safety_reports",
    )
    assert out == {"x.key2": "2"}


def test_get_settings_with_prefix_skips_non_string_values(mocker):
    """A row whose Value cell is non-string is dropped (matches get_setting contract)."""
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = _config_rows_sheet([
        {"Setting": "x.string", "Value": "good", "Workstream": "global"},
        {"Setting": "x.numeric", "Value": 42, "Workstream": "global"},
        {"Setting": "x.none", "Value": None, "Workstream": "global"},
    ])
    out = smartsheet_client.get_settings_with_prefix("x.")
    assert out == {"x.string": "good"}


def test_get_settings_with_prefix_empty_when_no_match(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.return_value = _config_rows_sheet([
        {"Setting": "system.state", "Value": "ACTIVE", "Workstream": "global"},
    ])
    assert smartsheet_client.get_settings_with_prefix("absent.") == {}


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


# ---- list_columns_with_options / update_column_options ------------------


def test_list_columns_with_options_returns_normalized_dicts(mocker):
    client = _install_client(mocker)
    cols = [
        SimpleNamespace(id=1, title="Setting", type="TEXT_NUMBER", options=None),
        SimpleNamespace(id=2, title="Status", type="PICKLIST", options=["a", "b", "c"]),
        SimpleNamespace(id=3, title="Tags", type="MULTI_PICKLIST", options=["x", "y"]),
    ]
    sheet = SimpleNamespace(columns=cols)
    client.Sheets.get_sheet.return_value = sheet

    out = smartsheet_client.list_columns_with_options(42)

    assert out == [
        {"id": 1, "title": "Setting", "type": "TEXT_NUMBER", "options": []},
        {"id": 2, "title": "Status", "type": "PICKLIST", "options": ["a", "b", "c"]},
        {"id": 3, "title": "Tags", "type": "MULTI_PICKLIST", "options": ["x", "y"]},
    ]
    client.Sheets.get_sheet.assert_called_once_with(42, include="columns")


def test_list_columns_with_options_translates_api_error(mocker):
    client = _install_client(mocker)
    client.Sheets.get_sheet.side_effect = _api_error(404, message="sheet not found")

    with pytest.raises(SmartsheetNotFoundError):
        smartsheet_client.list_columns_with_options(42)


def test_update_column_options_calls_sdk_and_invalidates_cache(mocker):
    client = _install_client(mocker)
    # Seed the column cache so we can assert the invalidation.
    smartsheet_client._column_maps[42] = {"Status": 99}

    smartsheet_client.update_column_options(
        42, 99, ["A", "B", "C"], column_type="PICKLIST"
    )

    client.Sheets.update_column.assert_called_once()
    args, _ = client.Sheets.update_column.call_args
    assert args[0] == 42
    assert args[1] == 99
    col = args[2]
    # Smartsheet's PUT /sheets/{sheetId}/columns/{columnId} rejects `id`
    # in the request body (errorCode 1032). The column_id flows through
    # the URL path (args[1]), not the model — the body must omit `id`.
    assert col.id is None
    assert list(col.options) == ["A", "B", "C"]
    # `type` IS required (errorCode 1090) when updating options.
    assert col.type == "PICKLIST"
    # Cache invalidated after the update so subsequent reads refresh.
    assert 42 not in smartsheet_client._column_maps


def test_update_column_options_body_omits_id(mocker):
    """Regression guard: API rejects body with `id` (errorCode 1032).

    Discovered live during PR #46 picklist smoke test post-#45 merge —
    the SDK mock didn't validate body shape so the test suite passed
    but the real Smartsheet API returned HTTP 400.
    """
    client = _install_client(mocker)
    smartsheet_client.update_column_options(42, 99, ["A"], column_type="PICKLIST")

    body = client.Sheets.update_column.call_args.args[2]
    assert body.id is None
    serialized = body.to_dict()
    assert "id" not in serialized
    assert "columnId" not in serialized


def test_update_column_options_body_includes_type(mocker):
    """Regression guard: API requires `type` when updating options
    (errorCode 1090). Discovered live during PR #47 re-smoke."""
    client = _install_client(mocker)
    smartsheet_client.update_column_options(
        42, 99, ["A"], column_type="PICKLIST"
    )

    body = client.Sheets.update_column.call_args.args[2]
    assert body.type == "PICKLIST"
    assert "type" in body.to_dict()


def test_update_column_options_accepts_multi_picklist(mocker):
    client = _install_client(mocker)
    smartsheet_client.update_column_options(
        42, 99, ["A"], column_type="MULTI_PICKLIST"
    )

    body = client.Sheets.update_column.call_args.args[2]
    assert body.type == "MULTI_PICKLIST"


def test_update_column_options_translates_auth_error(mocker):
    client = _install_client(mocker)
    client.Sheets.update_column.side_effect = _api_error(401, message="bad token")

    with pytest.raises(SmartsheetAuthError):
        smartsheet_client.update_column_options(
            42, 99, ["A"], column_type="PICKLIST"
        )


def test_update_column_options_handles_empty_options_list(mocker):
    client = _install_client(mocker)

    smartsheet_client.update_column_options(42, 99, [], column_type="PICKLIST")

    args, _ = client.Sheets.update_column.call_args
    col = args[2]
    assert list(col.options) == []


# ---- find_sheet_by_name_in_folder / create_sheet_in_folder --------------


def test_find_sheet_by_name_in_folder_matches_title(mocker):
    client = _install_client(mocker)
    folder = SimpleNamespace(
        sheets=[
            SimpleNamespace(id=111, name="ITS_Errors"),
            SimpleNamespace(id=222, name="Picklist_Sync_Config"),
            SimpleNamespace(id=333, name="Other"),
        ]
    )
    client.Folders.get_folder.return_value = folder

    result = smartsheet_client.find_sheet_by_name_in_folder(7, "Picklist_Sync_Config")

    assert result == 222


def test_find_sheet_by_name_in_folder_returns_none_when_absent(mocker):
    client = _install_client(mocker)
    folder = SimpleNamespace(
        sheets=[SimpleNamespace(id=111, name="Other")]
    )
    client.Folders.get_folder.return_value = folder

    result = smartsheet_client.find_sheet_by_name_in_folder(7, "Nope")
    assert result is None


def test_find_sheet_by_name_in_folder_handles_empty_folder(mocker):
    client = _install_client(mocker)
    # A folder with no sheets list at all (defensive: API may omit the key).
    folder = SimpleNamespace(sheets=None)
    client.Folders.get_folder.return_value = folder

    assert smartsheet_client.find_sheet_by_name_in_folder(7, "Anything") is None


def test_find_sheet_by_name_in_folder_translates_permission_error(mocker):
    client = _install_client(mocker)
    client.Folders.get_folder.side_effect = _api_error(403, message="no access")

    with pytest.raises(SmartsheetPermissionError):
        smartsheet_client.find_sheet_by_name_in_folder(7, "Anything")


def test_find_sheet_by_name_in_folder_returns_first_match_on_duplicate(mocker):
    """Smartsheet doesn't enforce title uniqueness inside a folder; if a
    duplicate exists, return the first match (deterministic + cheap)."""
    client = _install_client(mocker)
    folder = SimpleNamespace(
        sheets=[
            SimpleNamespace(id=111, name="Dup"),
            SimpleNamespace(id=222, name="Dup"),
        ]
    )
    client.Folders.get_folder.return_value = folder

    assert smartsheet_client.find_sheet_by_name_in_folder(7, "Dup") == 111


def test_create_sheet_in_folder_returns_new_sheet_id(mocker):
    client = _install_client(mocker)
    created = SimpleNamespace(result=SimpleNamespace(id=555))
    client.Folders.create_sheet_in_folder.return_value = created

    columns = [
        {"title": "mapping_id", "type": "TEXT_NUMBER", "primary": True},
        {"title": "enabled", "type": "CHECKBOX"},
    ]
    sheet_id = smartsheet_client.create_sheet_in_folder(7, "Picklist_Sync_Config", columns)

    assert sheet_id == 555
    client.Folders.create_sheet_in_folder.assert_called_once()
    args, _ = client.Folders.create_sheet_in_folder.call_args
    assert args[0] == 7
    sheet_model = args[1]
    assert sheet_model.name == "Picklist_Sync_Config"
    titles = [c.title for c in sheet_model.columns]
    assert titles == ["mapping_id", "enabled"]


def test_create_sheet_in_folder_translates_api_error(mocker):
    client = _install_client(mocker)
    client.Folders.create_sheet_in_folder.side_effect = _api_error(
        429, message="rate limited"
    )

    with pytest.raises(SmartsheetRateLimitError):
        smartsheet_client.create_sheet_in_folder(7, "Whatever", [])
