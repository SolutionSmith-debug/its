"""Tests for the bounded transient retry in shared/smartsheet_client.py.

The control being proven: a Smartsheet 5xx-with-errorCode-4000 or a requests-level
ReadTimeout — neither of which the SDK retries (see SmartsheetTransientError) — is
re-issued a bounded number of times on IDEMPOTENT READS ONLY, inside one
breaker-counted attempt, and nothing else is ever retried.

Two of these are structural guards rather than behaviour tests: a set-equality
assertion on the enrollment list and an AST guard proving no enrolled body reaches a
mutator. Together they bind FUTURE helpers, which an enumerated "these writes are
absent" assertion would not.

Run with: pytest -q tests/test_smartsheet_retry.py
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
import smartsheet.exceptions as sdk_exc

from shared import circuit_breaker, smartsheet_client
from shared.smartsheet_client import (
    RetryConfig,
    SmartsheetAuthError,
    SmartsheetCircuitOpenError,
    SmartsheetError,
    SmartsheetNotFoundError,
    SmartsheetPermissionError,
    SmartsheetRateLimitError,
    SmartsheetTransientError,
    SmartsheetValidationError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_SRC = REPO_ROOT / "shared" / "smartsheet_client.py"

# The approved enrollment list, restated here INDEPENDENTLY of the module constant so a
# code-side edit cannot silently move the goalposts. Reads / idempotent lookups only.
APPROVED_RETRY_ENROLLMENT = {
    "get_sheet",
    "get_row",
    "get_rows",
    "get_cell_history",
    "list_columns_with_options",
    "find_sheet_by_name_in_folder",
    "count_workspace_sheets",
    "find_folder_by_name_in_folder",
    "find_folder_by_name_in_workspace",
    "list_workspace_share_emails",
}

# Substrings that mean "this body can mutate remote state". An enrolled function whose
# body reaches any of these is a bug: a timed-out write may have COMMITTED, and
# Smartsheet has no idempotency key, so a blind re-issue duplicates it.
FORBIDDEN_IN_ENROLLED_BODY = (
    "add_rows", "update_rows", "delete_rows",
    "add_row", "update_row", "delete_row",
    "add_columns", "update_column", "delete_column",
    "attach_file", "add_image",
    "create_sheet", "copy_sheet", "delete_sheet", "move_sheet",
    "create_folder", "delete_folder", "move_folder",
    "requests.post", "requests.put", "requests.delete", "requests.patch",
)


@pytest.fixture
def retry_enabled(mocker):
    """Install a real (enabled) retry config, overriding conftest's neutralized one."""
    cfg = RetryConfig(
        enabled=True, max_extra_attempts=2, backoff_seconds=(2.0, 5.0),
        source_summary="enabled=default max_extra_attempts=default backoff_seconds=default",
    )
    mocker.patch.object(smartsheet_client, "_retry_config_cache", cfg)
    return cfg


@pytest.fixture
def sleeps(mocker):
    """Capture every backoff sleep without spending wall-clock."""
    return mocker.patch("shared.smartsheet_client.time.sleep")


@pytest.fixture(autouse=True)
def _clean_recovery_accumulator():
    smartsheet_client.drain_retry_recovery()
    yield
    smartsheet_client.drain_retry_recovery()


def _client(mocker):
    from unittest.mock import MagicMock

    client = MagicMock()
    mocker.patch.object(smartsheet_client, "get_client", return_value=client)
    return client


def _api_error(status: int, *, code: int = 0, message: str = "boom") -> sdk_exc.ApiError:
    result = SimpleNamespace(status_code=status, code=code, message=message)
    return sdk_exc.ApiError(SimpleNamespace(result=result), message=message)


def _sheet(*rows):
    return SimpleNamespace(columns=[SimpleNamespace(id=1, title="Key")], rows=list(rows))


# ---- retry happens for the two classes the SDK does not cover -------------


def test_retries_a_500_with_errorcode_4000(mocker, retry_enabled, sleeps):
    """The exact live signature: HTTP 500 body errorCode 4000, absent from the SDK's
    should_retry lookup, so the SDK issues ZERO retries of its own."""
    client = _client(mocker)
    client.Sheets.get_sheet.side_effect = [
        _api_error(500, code=4000, message="An unexpected error has occurred"),
        _sheet(),
    ]

    result = smartsheet_client.get_rows(123)

    assert result == []
    assert client.Sheets.get_sheet.call_count == 2


def test_retries_a_translated_read_timeout(mocker, retry_enabled, sleeps):
    client = _client(mocker)
    client.Sheets.get_sheet.side_effect = [
        sdk_exc.UnexpectedRequestError(requests.exceptions.ReadTimeout("timed out"), None),
        _sheet(),
    ]

    assert smartsheet_client.get_rows(123) == []
    assert client.Sheets.get_sheet.call_count == 2


def test_backoff_sequence_matches_config(mocker, retry_enabled, sleeps):
    client = _client(mocker)
    client.Sheets.get_sheet.side_effect = [
        _api_error(500, code=4000),
        _api_error(503),
        _sheet(),
    ]

    smartsheet_client.get_rows(123)

    assert [c.args[0] for c in sleeps.call_args_list] == [2.0, 5.0]


def test_exhaustion_reraises_the_transient_type_unchanged(mocker, retry_enabled, sleeps):
    client = _client(mocker)
    client.Sheets.get_sheet.side_effect = _api_error(500, code=4000, message="still down")

    with pytest.raises(SmartsheetTransientError, match="still down"):
        smartsheet_client.get_rows(123)

    # 1 original + max_extra_attempts, and no more.
    assert client.Sheets.get_sheet.call_count == 3
    assert sleeps.call_count == 2


# ---- everything else is NOT retried --------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, SmartsheetValidationError),
        (401, SmartsheetAuthError),
        (403, SmartsheetPermissionError),
        (404, SmartsheetNotFoundError),
        (429, SmartsheetRateLimitError),
    ],
)
def test_deterministic_classes_are_never_retried(mocker, retry_enabled, sleeps, status, expected):
    client = _client(mocker)
    client.Sheets.get_sheet.side_effect = _api_error(status)

    with pytest.raises(expected):
        smartsheet_client.get_rows(123)

    assert client.Sheets.get_sheet.call_count == 1
    sleeps.assert_not_called()


def test_circuit_open_propagates_with_zero_sleeps(mocker, retry_enabled, sleeps):
    """Retrying a short-circuit would hammer the very protection the breaker provides."""
    guard = circuit_breaker.guard(
        open_exc=SmartsheetCircuitOpenError,
        count=SmartsheetError,
        config_loader=lambda: circuit_breaker.CircuitConfig(
            enabled=True, failure_threshold=1, cooldown_seconds=300
        ),
        state_path=Path("/nonexistent/never-written.json"),
    )

    @guard
    @smartsheet_client._transient_retry
    def _inner() -> None:
        raise SmartsheetCircuitOpenError("breaker OPEN")

    with pytest.raises(SmartsheetCircuitOpenError):
        _inner()

    sleeps.assert_not_called()


def test_disabled_config_is_a_pure_passthrough(mocker, sleeps):
    mocker.patch.object(
        smartsheet_client, "_retry_config_cache",
        RetryConfig(enabled=False, max_extra_attempts=2, backoff_seconds=(2.0,),
                    source_summary="enabled=ITS_Config"),
    )
    client = _client(mocker)
    client.Sheets.get_sheet.side_effect = _api_error(500, code=4000)

    with pytest.raises(SmartsheetTransientError):
        smartsheet_client.get_rows(123)

    assert client.Sheets.get_sheet.call_count == 1
    sleeps.assert_not_called()


# ---- breaker composition (the placement invariant) -----------------------


def _composed(tmp_path, fn):
    """Wrap `fn` exactly as the module does: guard OUTSIDE, retry INSIDE."""
    guard = circuit_breaker.guard(
        open_exc=SmartsheetCircuitOpenError,
        count=SmartsheetError,
        ignore=(SmartsheetAuthError, SmartsheetPermissionError, SmartsheetNotFoundError),
        config_loader=lambda: circuit_breaker.CircuitConfig(
            enabled=True, failure_threshold=5, cooldown_seconds=300
        ),
        state_path=tmp_path / "circuit_breaker.json",
    )
    return guard(smartsheet_client._transient_retry(fn))


def test_exhausted_sequence_counts_exactly_one_breaker_failure(tmp_path, retry_enabled, sleeps):
    """Retry OUTSIDE the guard would record 3 failures for one logical call, tripping the
    breaker 3x sooner than `failure_threshold` says."""
    state = tmp_path / "circuit_breaker.json"
    calls = {"n": 0}

    def _always_transient() -> None:
        calls["n"] += 1
        raise SmartsheetTransientError("HTTP 500")

    with pytest.raises(SmartsheetTransientError):
        _composed(tmp_path, _always_transient)()

    assert calls["n"] == 3  # the retry really did run inside
    assert circuit_breaker._load_state(state)["consecutive_failures"] == 1


def test_mid_sequence_success_records_a_breaker_success(tmp_path, retry_enabled, sleeps):
    state = tmp_path / "circuit_breaker.json"
    seq = iter([SmartsheetTransientError("HTTP 500"), None])

    def _flaky() -> str:
        nxt = next(seq)
        if nxt is not None:
            raise nxt
        return "ok"

    assert _composed(tmp_path, _flaky)() == "ok"
    assert circuit_breaker._load_state(state)["consecutive_failures"] == 0


# ---- cold start (the re-entrancy hazard) ---------------------------------


def test_cold_start_with_neither_config_cache_populated(mocker, sleeps):
    """The FIRST guarded call resolves the circuit config before calling fn; that read
    descends into get_rows' retry wrapper, which loads ITS config, which reads again…
    Without the recursion guard this never terminates."""
    mocker.patch.object(smartsheet_client, "_circuit_config_cache", None)
    mocker.patch.object(smartsheet_client, "_retry_config_cache", None)
    mocker.patch.object(smartsheet_client, "_loading_retry_config", False)
    client = _client(mocker)
    client.Sheets.get_sheet.return_value = _sheet()

    assert smartsheet_client.get_rows(123) == []

    # Both caches resolved exactly once, and the guard flag is back down.
    assert smartsheet_client._retry_config_cache is not None
    assert smartsheet_client._circuit_config_cache is not None
    assert smartsheet_client._loading_retry_config is False


def test_retry_is_passthrough_while_loading_retry_config(mocker, retry_enabled, sleeps):
    mocker.patch.object(smartsheet_client, "_loading_retry_config", True)
    client = _client(mocker)
    client.Sheets.get_sheet.side_effect = _api_error(500, code=4000)

    with pytest.raises(SmartsheetTransientError):
        smartsheet_client.get_rows(123)

    assert client.Sheets.get_sheet.call_count == 1
    sleeps.assert_not_called()


def test_reentrant_config_load_returns_defaults_without_caching(mocker):
    mocker.patch.object(smartsheet_client, "_retry_config_cache", None)
    mocker.patch.object(smartsheet_client, "_loading_retry_config", True)

    cfg = smartsheet_client._load_retry_config()

    assert cfg.enabled is True and cfg.max_extra_attempts == 2
    # A defaults answer produced under re-entrancy must NOT become the cached truth —
    # the real read is still in flight and must win.
    assert smartsheet_client._retry_config_cache is None


def test_config_read_failure_falls_back_to_defaults(mocker, sleeps):
    """`_read_global_setting` swallows SmartsheetError itself; this proves the loader is
    still TOTAL if that contract ever regressed — resolving config must never be the
    thing that breaks the call it configures."""
    mocker.patch.object(smartsheet_client, "_retry_config_cache", None)
    mocker.patch.object(
        smartsheet_client, "_read_global_setting",
        side_effect=RuntimeError("config surface exploded"),
    )

    cfg = smartsheet_client._load_retry_config()

    assert cfg.enabled is True and cfg.max_extra_attempts == 2
    assert smartsheet_client._retry_config_cache is None  # a fallback is not the truth
    assert smartsheet_client._loading_retry_config is False  # flag released


def test_config_backoff_parsed_from_its_config(mocker):
    mocker.patch.object(smartsheet_client, "_retry_config_cache", None)
    values = {
        "smartsheet.retry.enabled": "true",
        "smartsheet.retry.max_extra_attempts": "4",
        "smartsheet.retry.backoff_seconds": "1.5, 3, 9",
    }
    mocker.patch.object(smartsheet_client, "_read_global_setting", side_effect=values.get)

    cfg = smartsheet_client._load_retry_config()

    assert cfg.enabled is True
    assert cfg.max_extra_attempts == 4
    assert cfg.backoff_seconds == (1.5, 3.0, 9.0)
    # Observable config resolution: the source of every key is recorded, not inferred.
    assert "enabled=ITS_Config" in cfg.source_summary


def test_config_sources_report_defaults_when_rows_missing(mocker):
    mocker.patch.object(smartsheet_client, "_retry_config_cache", None)
    mocker.patch.object(smartsheet_client, "_read_global_setting", return_value=None)

    cfg = smartsheet_client._load_retry_config()

    assert cfg.source_summary == (
        "enabled=default max_extra_attempts=default backoff_seconds=default"
    )


def test_malformed_backoff_falls_back_to_default(mocker):
    mocker.patch.object(smartsheet_client, "_retry_config_cache", None)
    mocker.patch.object(
        smartsheet_client, "_read_global_setting",
        side_effect=lambda key: "not,a,number" if key.endswith("backoff_seconds") else None,
    )

    cfg = smartsheet_client._load_retry_config()

    assert cfg.backoff_seconds == (2.0, 5.0)


# ---- recovery visibility (D3) --------------------------------------------


def test_recovery_emits_one_local_warn_and_accumulates(mocker, retry_enabled, sleeps):
    warn = mocker.patch("shared.error_log.local_log")
    client = _client(mocker)
    client.Sheets.get_sheet.side_effect = [_api_error(500, code=4000), _sheet()]

    smartsheet_client.get_rows(123)

    assert warn.call_count == 1
    message = warn.call_args.args[2]
    assert "RECOVERED on retry" in message
    assert "call=get_rows" in message and "extra_attempts=1" in message
    # The resolved config source rides the line (observable config resolution).
    assert "retry config:" in message

    drained = smartsheet_client.drain_retry_recovery()
    assert drained == {"get_rows": {"sequences": 1, "attempts": 1}}
    # Draining CLEARS — a second flush must not double-report the same recovery.
    assert smartsheet_client.drain_retry_recovery() == {}


def test_no_recovery_record_when_the_first_attempt_succeeds(mocker, retry_enabled, sleeps):
    warn = mocker.patch("shared.error_log.local_log")
    client = _client(mocker)
    client.Sheets.get_sheet.return_value = _sheet()

    smartsheet_client.get_rows(123)

    warn.assert_not_called()
    assert smartsheet_client.drain_retry_recovery() == {}


def test_recovery_accumulator_is_bounded(mocker):
    """A long-running process (the dashboard) must not grow this without limit."""
    cfg = RetryConfig(enabled=True, max_extra_attempts=1, backoff_seconds=(),
                      source_summary="x")
    mocker.patch("shared.error_log.local_log")
    for i in range(smartsheet_client._RETRY_RECOVERY_MAX_KEYS + 25):
        smartsheet_client._note_retry_recovery(f"call_{i}", 1, 0.1, SmartsheetTransientError("x"), cfg)

    drained = smartsheet_client.drain_retry_recovery()
    assert len(drained) == smartsheet_client._RETRY_RECOVERY_MAX_KEYS + 1
    overflow = drained[smartsheet_client._RETRY_RECOVERY_OVERFLOW_KEY]
    assert overflow["sequences"] == 25


# ---- is_transient_error predicate ----------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        (SmartsheetTransientError("500"), True),
        (SmartsheetError("base"), False),
        (SmartsheetCircuitOpenError("open"), False),
        (SmartsheetRateLimitError("429"), False),
        (SmartsheetAuthError("401"), False),
        (SmartsheetPermissionError("403"), False),
        (SmartsheetNotFoundError("404"), False),
        (SmartsheetValidationError("400"), False),
        (ValueError("not smartsheet at all"), False),
    ],
)
def test_is_transient_error_is_type_driven(exc, expected):
    assert smartsheet_client.is_transient_error(exc) is expected


# ---- structural enrollment guards ----------------------------------------


def test_enrollment_set_equals_the_approved_read_only_list():
    """Set EQUALITY, not containment: enrolling a new helper forces a deliberate edit
    here, which is the review moment where "is this idempotent?" gets asked."""
    assert smartsheet_client._TRANSIENT_RETRY_ENROLLED == frozenset(APPROVED_RETRY_ENROLLMENT)


def test_exactly_the_enrolled_functions_carry_the_decorator():
    """Guards the other direction: a decorator applied without updating the constant.

    Scoped to functions DEFINED in this module — the module also holds a live SDK client
    whose `__getattr__` dynamically resolves any name and answers truthy.
    """
    decorated = {
        name
        for name, obj in vars(smartsheet_client).items()
        if inspect.isfunction(obj)
        and getattr(obj, "__its_transient_retry__", False)
    }
    assert decorated == set(APPROVED_RETRY_ENROLLMENT)


def _reachable_names(fn: ast.FunctionDef) -> set[str]:
    """Every dotted name the function's CODE touches — calls and attribute reads alike.

    Deliberately NOT a substring scan of the unparsed body: that flags a mutator merely
    NAMED in a docstring (`list_columns_with_options` explains `update_column_options`),
    which is prose, not reach.
    """
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            names.add(ast.unparse(node.func))
        elif isinstance(node, ast.Attribute):
            names.add(ast.unparse(node))
    return names


def test_no_enrolled_function_body_reaches_a_mutator():
    """AST guard (the tests/test_capability_gating.py idiom). An enumerated "these named
    writes are absent" assertion would not bind a helper written next month; this does."""
    tree = ast.parse(CLIENT_SRC.read_text())
    checked = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in APPROVED_RETRY_ENROLLMENT:
            continue
        checked.add(node.name)
        reachable = _reachable_names(node)
        hits = sorted(
            {needle for needle in FORBIDDEN_IN_ENROLLED_BODY
             for name in reachable if needle in name}
        )
        assert not hits, (
            f"{node.name} is enrolled for transient retry but its body reaches {hits}. "
            "A timed-out Smartsheet write may have COMMITTED (no idempotency key), so a "
            "blind re-issue duplicates it. Either drop the enrollment or split the read out."
        )
    assert checked == set(APPROVED_RETRY_ENROLLMENT), (
        f"enrolled names not found as function defs in {CLIENT_SRC}: "
        f"{set(APPROVED_RETRY_ENROLLMENT) - checked}"
    )


def test_no_write_helper_is_enrolled():
    """The explicit companion to the AST guard — the named writes stay out."""
    writes = {
        "add_rows", "update_rows", "delete_rows", "update_row_cells_by_id", "add_row_by_id",
        "attach_pdf_to_row", "ensure_picklist_options", "create_picklist_column",
        "update_column_options", "apply_column_styles", "create_sheet_in_folder",
        "create_sheet_in_folder_from_template", "create_folder_in_folder",
        "create_folder_in_workspace", "move_sheet_to_folder", "delete_sheet",
        "verify_write_capability",
    }
    assert writes & smartsheet_client._TRANSIENT_RETRY_ENROLLED == set()
    for name in writes:
        assert hasattr(smartsheet_client, name), f"{name} no longer exists — update this list"
