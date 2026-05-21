"""Tests for shared/picklist_sync.py + scripts/run_picklist_sync.py.

All Smartsheet boundaries are mocked. Tests cover the pure-function core
(extract, diff, hash, threshold validation), the per-mapping driver
(short-circuit, dry-run, size guards, reference-checked removals), and
the aggregate driver (failure routing, triple-fire escalation).

Integration tests against live Smartsheet are NOT in this file — the
live verification surface is `scripts/run_picklist_sync.py --smoke-test`.

Run with: pytest -q tests/test_picklist_sync.py
"""
from __future__ import annotations

import pytest

from shared import defaults, picklist_sync
from shared.picklist_sync import (
    Mapping,
    compute_diff,
    compute_hash,
    extract_unique_values,
)
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError

# ---- Fixtures + helpers --------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_error_log(tmp_path, monkeypatch, mocker):
    """Redirect error_log filesystem + mock side channels.

    Same pattern as tests/test_watchdog.py — picklist_sync's WARN/ERROR
    paths go through shared.error_log.log, which writes to LOG_DIR +
    ITS_Errors via add_rows. We redirect the file path and mock the
    Smartsheet write so tests stay hermetic.
    """
    monkeypatch.setattr("shared.error_log.LOG_DIR", tmp_path)
    mocker.patch("shared.error_log.smartsheet_client.add_rows")
    mocker.patch("shared.resend_client.send_alert")
    mocker.patch("shared.sentry_client.capture_exception")
    import shared.error_log as el
    el._in_smartsheet_write = False
    el._in_resend_alert = False
    el._in_sentry_capture = False
    yield
    el._in_smartsheet_write = False
    el._in_resend_alert = False
    el._in_sentry_capture = False


@pytest.fixture(autouse=True)
def thresholds_default(mocker):
    """Default: ITS_Config returns the brief's 200/400 values."""
    def _get(key, *, workstream):
        return {
            "picklist_sync.size_warn_threshold": "200",
            "picklist_sync.size_hard_halt_threshold": "400",
        }.get(key)
    return mocker.patch("shared.smartsheet_client.get_setting", side_effect=_get)


def _mapping(
    mapping_id: str = "test_mapping",
    source_sheet_id: int = 100,
    source_column: str = "vendor_name",
    target_sheet_id: int = 200,
    target_column: str = "vendor",
    enabled: bool = True,
    last_run_hash: str | None = None,
    row_id: int = 999,
) -> Mapping:
    return Mapping(
        mapping_id=mapping_id,
        source_sheet_id=source_sheet_id,
        source_column=source_column,
        target_sheet_id=target_sheet_id,
        target_column=target_column,
        enabled=enabled,
        last_run_at=None,
        last_run_hash=last_run_hash,
        notes=None,
        _row_id=row_id,
    )


# ---- Pure core: extract_unique_values ------------------------------------


def test_extract_unique_values_dedupe_sort():
    rows = [
        {"vendor_name": "Bravo"},
        {"vendor_name": "Acme"},
        {"vendor_name": "Bravo"},
        {"vendor_name": "Charlie"},
    ]
    assert extract_unique_values(rows, "vendor_name") == ["Acme", "Bravo", "Charlie"]


def test_extract_unique_values_skips_blank_and_whitespace():
    rows = [
        {"vendor_name": "Acme"},
        {"vendor_name": ""},
        {"vendor_name": "   "},
        {"vendor_name": None},
        {"vendor_name": "Bravo"},
    ]
    assert extract_unique_values(rows, "vendor_name") == ["Acme", "Bravo"]


def test_extract_unique_values_strips_outer_whitespace():
    rows = [
        {"vendor_name": "  Acme  "},
        {"vendor_name": "Bravo"},
    ]
    assert extract_unique_values(rows, "vendor_name") == ["Acme", "Bravo"]


def test_extract_unique_values_preserves_case():
    rows = [
        {"vendor_name": "Acme"},
        {"vendor_name": "acme"},
        {"vendor_name": "ACME"},
    ]
    # Case-sensitive — preserves operator's distinction in the master DB.
    assert extract_unique_values(rows, "vendor_name") == ["ACME", "Acme", "acme"]


def test_extract_unique_values_handles_non_string_values():
    rows = [
        {"vendor_name": "Acme"},
        {"vendor_name": 42},  # Numeric somehow
    ]
    assert extract_unique_values(rows, "vendor_name") == ["42", "Acme"]


def test_extract_unique_values_missing_column():
    rows = [{"other_col": "x"}, {"other_col": "y"}]
    assert extract_unique_values(rows, "vendor_name") == []


# ---- Pure core: compute_diff --------------------------------------------


def test_compute_diff_additions_and_removals():
    additions, removals = compute_diff(["a", "b", "c"], ["b", "c", "d"])
    assert additions == ["d"]
    assert removals == ["a"]


def test_compute_diff_no_changes():
    additions, removals = compute_diff(["a", "b"], ["a", "b"])
    assert additions == []
    assert removals == []


def test_compute_diff_all_additions():
    additions, removals = compute_diff([], ["a", "b", "c"])
    assert additions == ["a", "b", "c"]
    assert removals == []


def test_compute_diff_all_removals():
    additions, removals = compute_diff(["a", "b", "c"], [])
    assert additions == []
    assert removals == ["a", "b", "c"]


def test_compute_diff_returns_sorted():
    additions, removals = compute_diff(["z", "y"], ["a", "z"])
    assert additions == ["a"]
    assert removals == ["y"]


# ---- Pure core: compute_hash --------------------------------------------


def test_compute_hash_stable_across_input_order():
    h1 = compute_hash(["a", "b", "c"])
    h2 = compute_hash(["c", "a", "b"])
    assert h1 == h2


def test_compute_hash_changes_on_value_change():
    h1 = compute_hash(["a", "b"])
    h2 = compute_hash(["a", "c"])
    assert h1 != h2


def test_compute_hash_dedupes_input():
    assert compute_hash(["a", "b"]) == compute_hash(["a", "a", "b", "b"])


def test_compute_hash_format_sha256():
    h = compute_hash(["a"])
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ---- Pure core: _resolve_size_thresholds ---------------------------------


def test_resolve_thresholds_both_unset_silent_fallback(mocker):
    mocker.patch(
        "shared.smartsheet_client.get_setting",
        side_effect=SmartsheetNotFoundError("missing"),
    )
    log_mock = mocker.patch("shared.picklist_sync.log")

    warn, halt = picklist_sync._resolve_size_thresholds()
    assert warn == defaults.PICKLIST_SIZE_WARN_THRESHOLD
    assert halt == defaults.PICKLIST_SIZE_HARD_HALT_THRESHOLD
    log_mock.assert_not_called()


def test_resolve_thresholds_both_valid_returned(mocker):
    def _get(key, *, workstream):
        return {"picklist_sync.size_warn_threshold": "150",
                "picklist_sync.size_hard_halt_threshold": "350"}.get(key)
    mocker.patch("shared.smartsheet_client.get_setting", side_effect=_get)

    warn, halt = picklist_sync._resolve_size_thresholds()
    assert warn == 150
    assert halt == 350


def test_resolve_thresholds_non_integer_warn_falls_back_with_warn_log(mocker):
    def _get(key, *, workstream):
        return {"picklist_sync.size_warn_threshold": "abc",
                "picklist_sync.size_hard_halt_threshold": "400"}.get(key)
    mocker.patch("shared.smartsheet_client.get_setting", side_effect=_get)
    log_mock = mocker.patch("shared.picklist_sync.log")

    warn, halt = picklist_sync._resolve_size_thresholds()
    assert warn == defaults.PICKLIST_SIZE_WARN_THRESHOLD
    assert halt == defaults.PICKLIST_SIZE_HARD_HALT_THRESHOLD
    log_mock.assert_called_once()
    args = log_mock.call_args.args
    assert args[0].value == "WARN"
    assert "non-integer" in args[2]
    assert "'abc'" in args[2]


def test_resolve_thresholds_negative_falls_back(mocker):
    def _get(key, *, workstream):
        return {"picklist_sync.size_warn_threshold": "-5",
                "picklist_sync.size_hard_halt_threshold": "400"}.get(key)
    mocker.patch("shared.smartsheet_client.get_setting", side_effect=_get)
    log_mock = mocker.patch("shared.picklist_sync.log")

    warn, halt = picklist_sync._resolve_size_thresholds()
    assert warn == defaults.PICKLIST_SIZE_WARN_THRESHOLD
    assert halt == defaults.PICKLIST_SIZE_HARD_HALT_THRESHOLD
    assert "non-positive" in log_mock.call_args.args[2]


def test_resolve_thresholds_inverted_falls_back(mocker):
    def _get(key, *, workstream):
        return {"picklist_sync.size_warn_threshold": "500",
                "picklist_sync.size_hard_halt_threshold": "100"}.get(key)
    mocker.patch("shared.smartsheet_client.get_setting", side_effect=_get)
    log_mock = mocker.patch("shared.picklist_sync.log")

    warn, halt = picklist_sync._resolve_size_thresholds()
    assert warn == defaults.PICKLIST_SIZE_WARN_THRESHOLD
    assert halt == defaults.PICKLIST_SIZE_HARD_HALT_THRESHOLD
    assert "inverted" in log_mock.call_args.args[2]


def test_resolve_thresholds_over_max_falls_back(mocker):
    def _get(key, *, workstream):
        return {"picklist_sync.size_warn_threshold": "200",
                "picklist_sync.size_hard_halt_threshold": "9999"}.get(key)
    mocker.patch("shared.smartsheet_client.get_setting", side_effect=_get)
    log_mock = mocker.patch("shared.picklist_sync.log")

    warn, halt = picklist_sync._resolve_size_thresholds()
    assert warn == defaults.PICKLIST_SIZE_WARN_THRESHOLD
    assert halt == defaults.PICKLIST_SIZE_HARD_HALT_THRESHOLD
    assert "sanity ceiling" in log_mock.call_args.args[2]


def test_resolve_thresholds_half_invalid_returns_both_defaults(mocker):
    """If warn is valid but halt is invalid, we use BOTH defaults (no mixing)."""
    def _get(key, *, workstream):
        return {"picklist_sync.size_warn_threshold": "150",
                "picklist_sync.size_hard_halt_threshold": "garbage"}.get(key)
    mocker.patch("shared.smartsheet_client.get_setting", side_effect=_get)
    log_mock = mocker.patch("shared.picklist_sync.log")

    warn, halt = picklist_sync._resolve_size_thresholds()
    # Did NOT honor the configured 150 — both defaults returned.
    assert warn == defaults.PICKLIST_SIZE_WARN_THRESHOLD
    assert halt == defaults.PICKLIST_SIZE_HARD_HALT_THRESHOLD
    assert log_mock.call_count == 1


def test_resolve_thresholds_read_failure_falls_back(mocker):
    mocker.patch(
        "shared.smartsheet_client.get_setting",
        side_effect=SmartsheetError("smartsheet outage"),
    )
    log_mock = mocker.patch("shared.picklist_sync.log")

    warn, halt = picklist_sync._resolve_size_thresholds()
    assert warn == defaults.PICKLIST_SIZE_WARN_THRESHOLD
    assert halt == defaults.PICKLIST_SIZE_HARD_HALT_THRESHOLD
    log_mock.assert_called_once()


# ---- Per-mapping sync: short-circuit + happy path ------------------------


def test_sync_idempotent(mocker):
    """Second run with same source values → matching hash → no API write."""
    source_values = ["Acme", "Bravo", "Charlie"]
    source_rows = [{"vendor_name": v} for v in source_values]
    last_hash = compute_hash(source_values)

    mocker.patch("shared.smartsheet_client.get_rows", return_value=source_rows)
    list_cols = mocker.patch("shared.smartsheet_client.list_columns_with_options")
    update_col = mocker.patch("shared.smartsheet_client.update_column_options")
    update_rows = mocker.patch("shared.smartsheet_client.update_rows")

    m = _mapping(last_run_hash=last_hash)
    result = picklist_sync.sync_one_mapping(m)

    assert result.status == "skipped_unchanged"
    list_cols.assert_not_called()
    update_col.assert_not_called()
    update_rows.assert_not_called()


def test_sync_addition_propagates(mocker):
    """New row in source → new option in target picklist."""
    source_rows = [{"vendor_name": v} for v in ("Acme", "Bravo", "NEW")]
    target_cols = [
        {"id": 11, "title": "job_id", "type": "TEXT_NUMBER", "options": []},
        {"id": 22, "title": "vendor", "type": "PICKLIST",
         "options": ["Acme", "Bravo"]},
    ]
    mocker.patch("shared.smartsheet_client.get_rows", return_value=source_rows)
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 return_value=target_cols)
    update_col = mocker.patch("shared.smartsheet_client.update_column_options")
    mocker.patch("shared.smartsheet_client.update_rows")

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "applied"
    assert result.additions == ["NEW"]
    update_col.assert_called_once_with(
        200, 22, ["Acme", "Bravo", "NEW"], column_type="PICKLIST"
    )


def test_sync_removal_applied_when_safe(mocker):
    """Source removed a value not referenced anywhere → option removed."""
    source_rows = [{"vendor_name": v} for v in ("Acme", "Bravo")]
    target_cols = [
        {"id": 22, "title": "vendor", "type": "PICKLIST",
         "options": ["Acme", "Bravo", "OBSOLETE"]},
    ]
    # Reference check: zero cells use 'OBSOLETE'.
    def _get_rows(sheet_id, **kw):
        if sheet_id == 100:  # source
            return source_rows
        # target reference check
        return []
    mocker.patch("shared.smartsheet_client.get_rows", side_effect=_get_rows)
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 return_value=target_cols)
    update_col = mocker.patch("shared.smartsheet_client.update_column_options")
    mocker.patch("shared.smartsheet_client.update_rows")
    review_add = mocker.patch("shared.picklist_sync.review_queue.add")

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "applied"
    assert result.removals_applied == ["OBSOLETE"]
    assert result.removals_blocked == []
    update_col.assert_called_once_with(
        200, 22, ["Acme", "Bravo"], column_type="PICKLIST"
    )
    review_add.assert_not_called()


def test_sync_removal_blocked_by_live_cells(mocker):
    """Source removed a value but live cells use it → option kept + Review Queue row."""
    source_rows = [{"vendor_name": "Acme"}]
    target_cols = [
        {"id": 22, "title": "vendor", "type": "PICKLIST",
         "options": ["Acme", "REFERENCED"]},
    ]
    def _get_rows(sheet_id, **kw):
        if sheet_id == 100:
            return source_rows
        # target reference check: 3 cells still hold 'REFERENCED'
        if kw.get("filters", {}).get("vendor") == "REFERENCED":
            return [{}, {}, {}]
        return []
    mocker.patch("shared.smartsheet_client.get_rows", side_effect=_get_rows)
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 return_value=target_cols)
    update_col = mocker.patch("shared.smartsheet_client.update_column_options")
    mocker.patch("shared.smartsheet_client.update_rows")
    review_add = mocker.patch(
        "shared.picklist_sync.review_queue.add", return_value=12345
    )

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "applied"
    assert result.removals_blocked == ["REFERENCED"]
    assert result.removals_applied == []
    assert result.review_queue_rows == [12345]
    # The blocked option stays in the final options list.
    update_col.assert_called_once()
    written_options = update_col.call_args.args[2]
    assert "REFERENCED" in written_options
    # Review Queue called with the right reason.
    rq_kwargs = review_add.call_args.kwargs
    from shared.review_queue import ReviewReason
    assert rq_kwargs["reason"] is ReviewReason.MISMATCHED_REFERENCE
    assert rq_kwargs["payload"]["option_text"] == "REFERENCED"
    assert rq_kwargs["payload"]["in_use_count"] == 3


def test_sync_dry_run_no_api_writes(mocker):
    source_rows = [{"vendor_name": v} for v in ("Acme", "Bravo", "NEW")]
    target_cols = [{"id": 22, "title": "vendor", "type": "PICKLIST",
                    "options": ["Acme", "Bravo"]}]
    mocker.patch("shared.smartsheet_client.get_rows", return_value=source_rows)
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 return_value=target_cols)
    update_col = mocker.patch("shared.smartsheet_client.update_column_options")
    update_rows = mocker.patch("shared.smartsheet_client.update_rows")

    result = picklist_sync.sync_one_mapping(_mapping(), dry_run=True)
    assert result.status == "dry_run"
    assert result.additions == ["NEW"]
    update_col.assert_not_called()
    update_rows.assert_not_called()


# ---- Size guardrails -----------------------------------------------------


def test_sync_size_warn_logged_but_applied(mocker):
    """Proposed size > warn (200) but <= halt (400) → WARN + apply."""
    # 250 unique values.
    source_rows = [{"vendor_name": f"V{i:04d}"} for i in range(250)]
    target_cols = [{"id": 22, "title": "vendor", "type": "PICKLIST", "options": []}]
    mocker.patch("shared.smartsheet_client.get_rows", return_value=source_rows)
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 return_value=target_cols)
    update_col = mocker.patch("shared.smartsheet_client.update_column_options")
    mocker.patch("shared.smartsheet_client.update_rows")
    log_mock = mocker.patch("shared.picklist_sync.log")

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "applied"
    update_col.assert_called_once()
    # WARN logged for the over-threshold size.
    severities = [c.args[0].value for c in log_mock.call_args_list]
    assert "WARN" in severities


def test_sync_size_hard_halt(mocker):
    """Proposed size > halt (400) → ERROR, no write, status halted_oversize."""
    source_rows = [{"vendor_name": f"V{i:04d}"} for i in range(450)]
    target_cols = [{"id": 22, "title": "vendor", "type": "PICKLIST", "options": []}]
    mocker.patch("shared.smartsheet_client.get_rows", return_value=source_rows)
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 return_value=target_cols)
    update_col = mocker.patch("shared.smartsheet_client.update_column_options")
    log_mock = mocker.patch("shared.picklist_sync.log")

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "halted_oversize"
    update_col.assert_not_called()
    severities = [c.args[0].value for c in log_mock.call_args_list]
    assert "ERROR" in severities


# ---- Error paths --------------------------------------------------------


def test_sync_invalid_source_column_or_missing_target(mocker):
    """Target sheet's column list doesn't include the configured target_column."""
    source_rows = [{"vendor_name": "Acme"}]
    target_cols = [
        {"id": 11, "title": "different_col", "type": "PICKLIST", "options": []},
    ]
    mocker.patch("shared.smartsheet_client.get_rows", return_value=source_rows)
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 return_value=target_cols)

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "failed"
    assert "target column 'vendor' not found" in result.error


def test_sync_target_column_wrong_type(mocker):
    """Target column exists but isn't PICKLIST → fail with a typed message."""
    source_rows = [{"vendor_name": "Acme"}]
    target_cols = [
        {"id": 22, "title": "vendor", "type": "TEXT_NUMBER", "options": []},
    ]
    mocker.patch("shared.smartsheet_client.get_rows", return_value=source_rows)
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 return_value=target_cols)

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "failed"
    assert "TEXT_NUMBER" in result.error
    assert "expected PICKLIST" in result.error


def test_sync_invalid_target_sheet_unreachable(mocker):
    """Target sheet read raises SmartsheetError → mapping fails cleanly."""
    mocker.patch("shared.smartsheet_client.get_rows",
                 return_value=[{"vendor_name": "Acme"}])
    mocker.patch("shared.smartsheet_client.list_columns_with_options",
                 side_effect=SmartsheetError("HTTP 404: sheet missing"))

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "failed"
    assert "404" in result.error


def test_sync_source_sheet_unreachable(mocker):
    mocker.patch("shared.smartsheet_client.get_rows",
                 side_effect=SmartsheetError("HTTP 503"))

    result = picklist_sync.sync_one_mapping(_mapping())
    assert result.status == "failed"


# ---- Aggregate driver: sync_all ------------------------------------------


def test_sync_all_partial_failure_logs_error_continues(mocker):
    """One mapping fails — others still run."""
    good = _mapping("good", source_sheet_id=10, target_sheet_id=20)
    bad = _mapping("bad", source_sheet_id=11, target_sheet_id=21)
    mocker.patch("shared.picklist_sync.read_mappings_from_config",
                 return_value=[good, bad])

    def _sync_one(m, *, dry_run=False):
        if m.mapping_id == "bad":
            return picklist_sync.MappingResult(
                mapping_id="bad", status="failed", error="kaboom"
            )
        return picklist_sync.MappingResult(
            mapping_id="good", status="applied", additions=["X"]
        )
    mocker.patch("shared.picklist_sync.sync_one_mapping", side_effect=_sync_one)
    log_mock = mocker.patch("shared.picklist_sync.log")

    stats = picklist_sync.sync_all()
    assert stats.mappings_examined == 2
    assert stats.mappings_failed == 1
    assert stats.mappings_applied == 1
    # ERROR logged for the failed mapping.
    severities = [c.args[0].value for c in log_mock.call_args_list]
    assert "ERROR" in severities


def test_sync_all_under_triple_fire_threshold_no_critical(mocker):
    """2 failures < threshold of 3 → no CRITICAL escalation."""
    mappings = [
        _mapping(f"m{i}", source_sheet_id=i, target_sheet_id=100 + i)
        for i in range(2)
    ]
    mocker.patch("shared.picklist_sync.read_mappings_from_config",
                 return_value=mappings)
    mocker.patch("shared.picklist_sync.sync_one_mapping",
                 side_effect=lambda m, **_: picklist_sync.MappingResult(
                     mapping_id=m.mapping_id, status="failed", error="bad"
                 ))
    alert_mock = mocker.patch("shared.error_log._alert_critical")

    picklist_sync.sync_all()
    alert_mock.assert_not_called()


def test_sync_all_triple_fire_threshold_fires_critical(mocker):
    """3 failures → CRITICAL escalation via _alert_critical."""
    mappings = [
        _mapping(f"m{i}", source_sheet_id=i, target_sheet_id=100 + i)
        for i in range(3)
    ]
    mocker.patch("shared.picklist_sync.read_mappings_from_config",
                 return_value=mappings)
    mocker.patch("shared.picklist_sync.sync_one_mapping",
                 side_effect=lambda m, **_: picklist_sync.MappingResult(
                     mapping_id=m.mapping_id, status="failed", error="bad"
                 ))
    alert_mock = mocker.patch("shared.error_log._alert_critical")

    picklist_sync.sync_all()
    alert_mock.assert_called_once()
    args = alert_mock.call_args.args
    assert "3 picklist sync mappings failed" in args[1]


def test_sync_all_only_filters_to_one_mapping(mocker):
    mappings = [
        _mapping("a", source_sheet_id=10, target_sheet_id=20),
        _mapping("b", source_sheet_id=11, target_sheet_id=21),
    ]
    mocker.patch("shared.picklist_sync.read_mappings_from_config",
                 return_value=mappings)
    sync_one = mocker.patch(
        "shared.picklist_sync.sync_one_mapping",
        side_effect=lambda m, **_: picklist_sync.MappingResult(
            mapping_id=m.mapping_id, status="applied"
        ),
    )

    stats = picklist_sync.sync_all(only="b")
    assert stats.mappings_examined == 1
    assert sync_one.call_count == 1
    assert sync_one.call_args.args[0].mapping_id == "b"


def test_sync_all_skips_disabled_mappings(mocker):
    mappings = [
        _mapping("enabled_one", enabled=True),
        _mapping("disabled_one", enabled=False),
    ]
    mocker.patch("shared.picklist_sync.read_mappings_from_config",
                 return_value=mappings)
    sync_one = mocker.patch(
        "shared.picklist_sync.sync_one_mapping",
        side_effect=lambda m, **_: picklist_sync.MappingResult(
            mapping_id=m.mapping_id, status="applied"
        ),
    )

    stats = picklist_sync.sync_all()
    assert stats.mappings_examined == 1
    assert sync_one.call_args.args[0].mapping_id == "enabled_one"


def test_sync_all_config_read_failure_returns_empty_stats(mocker):
    mocker.patch("shared.picklist_sync.read_mappings_from_config",
                 side_effect=SmartsheetError("HTTP 500"))

    stats = picklist_sync.sync_all()
    assert stats.mappings_examined == 0
    assert stats.mappings_failed == 0


# ---- read_mappings_from_config ------------------------------------------


def test_read_mappings_skips_blank_mapping_ids(mocker):
    raw_rows = [
        {"_row_id": 1, "mapping_id": "good", "source_sheet_id": "100",
         "target_sheet_id": "200", "source_column": "a", "target_column": "b",
         "enabled": True},
        {"_row_id": 2, "mapping_id": "", "source_sheet_id": "100",
         "target_sheet_id": "200"},
        {"_row_id": 3, "mapping_id": None, "source_sheet_id": "100",
         "target_sheet_id": "200"},
    ]
    mocker.patch("shared.smartsheet_client.get_rows", return_value=raw_rows)
    mappings = picklist_sync.read_mappings_from_config()
    assert [m.mapping_id for m in mappings] == ["good"]


def test_read_mappings_skips_non_integer_sheet_ids(mocker):
    raw_rows = [
        {"_row_id": 1, "mapping_id": "broken", "source_sheet_id": "not-a-number",
         "target_sheet_id": "200"},
        {"_row_id": 2, "mapping_id": "good", "source_sheet_id": "100",
         "target_sheet_id": "200", "source_column": "a", "target_column": "b",
         "enabled": True},
    ]
    mocker.patch("shared.smartsheet_client.get_rows", return_value=raw_rows)
    log_mock = mocker.patch("shared.picklist_sync.log")

    mappings = picklist_sync.read_mappings_from_config()
    assert [m.mapping_id for m in mappings] == ["good"]
    # WARN logged for the broken row.
    severities = [c.args[0].value for c in log_mock.call_args_list]
    assert "WARN" in severities


# ---- find_cells_using_option fail-safe ----------------------------------


def test_find_cells_using_option_treats_read_failure_as_in_use(mocker):
    """If reference-check read fails, treat as in-use to gate removal."""
    mocker.patch("shared.smartsheet_client.get_rows",
                 side_effect=SmartsheetError("HTTP 503"))
    mocker.patch("shared.picklist_sync.log")

    count = picklist_sync.find_cells_using_option(200, "vendor", "X")
    # >0 → blocks removal.
    assert count > 0


def test_find_cells_using_option_returns_count_on_success(mocker):
    mocker.patch("shared.smartsheet_client.get_rows",
                 return_value=[{}, {}, {}, {}])
    assert picklist_sync.find_cells_using_option(200, "vendor", "X") == 4
