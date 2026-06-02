"""Live-API integration tests for the F08 circuit breaker (Op Stds v16 §30).

Why this file exists:
    The breaker now wraps all 16 `shared/smartsheet_client.py` network methods.
    `SimpleNamespace` mocks at the SDK boundary pass without exercising live
    enforcement OR the SDK's in-process runtime state, and trip/reset is exactly
    the real-behavior-sensitive logic. This file confirms, against a real
    Smartsheet sandbox, that:
      1. a guarded READ works end-to-end with the breaker in the path (CLOSED);
      2. a guarded WRITE round-trip (create → add_rows → get_rows → update_rows)
         works through the guard on typed columns/rows (the §30 class of bug);
      3. real counting failures (flowing through the real `_translate`) trip the
         breaker OPEN, the next call short-circuits with `SmartsheetCircuitOpenError`
         WITHOUT a network call, and the escape hatch (delete the local state
         file) resets it to CLOSED.

How to run:
    Default `pytest -q` SKIPS this file (pyproject addopts: -m 'not integration').
    To run:  pytest -m integration
    Requires ITS_SMARTSHEET_TOKEN in macOS Keychain. NOT run in CI (no Keychain
    in GitHub Actions).

Isolation:
    The breaker's state file is redirected to a pytest tmp_path and its config is
    pinned to a small threshold here (the conftest breaker-neutralizer opts out
    of integration-marked tests, so this module sets up the REAL breaker itself).
    No test touches the real ~/its/state/circuit_breaker.json.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import requests  # type: ignore[import-untyped]
import smartsheet.exceptions as sdk_exc  # type: ignore[import-untyped]

from shared import circuit_breaker, keychain, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration


class _SecretToken:
    """Wraps ITS_SMARTSHEET_TOKEN so the raw value can't render in a traceback."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<ITS_SMARTSHEET_TOKEN redacted>"


@pytest.fixture(scope="module")
def _token_available() -> _SecretToken:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return _SecretToken(token)


@pytest.fixture(scope="module", autouse=True)
def _reset_smartsheet_client() -> Iterator[None]:
    """Force a fresh real-token SDK client (the module-level singleton may have
    been primed with the keychain stub by an earlier unit test in a mixed run)."""
    smartsheet_client._client = None
    yield
    smartsheet_client._client = None


@pytest.fixture(autouse=True)
def _isolate_breaker(tmp_path, monkeypatch) -> None:
    """REAL breaker, isolated: state file → tmp_path; config pinned to a small
    threshold so trip/reset is deterministic. The conftest neutralizer opts out
    of integration tests, so this module configures the live breaker itself."""
    monkeypatch.setattr(circuit_breaker, "STATE_FILE", tmp_path / "circuit_breaker.json")
    monkeypatch.setattr(
        smartsheet_client,
        "_circuit_config_cache",
        # Long cooldown so the trip-test's short-circuit assertion can't flake
        # into HALF_OPEN; reset is exercised via the file-delete escape hatch.
        circuit_breaker.CircuitConfig(
            enabled=True, failure_threshold=3, cooldown_seconds=300
        ),
    )


def _delete_sheet_rest(sheet_id: int, token: _SecretToken) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token.reveal()}"},
    )


def _sandbox_name(label: str) -> str:
    ts = datetime.now(UTC).strftime("%H%M%S_%f")
    name = f"_int_{label}_{ts}"
    assert len(name) <= 50, f"sandbox name {name!r} too long ({len(name)})"
    return name


# ---- 1. guarded read, live, stays CLOSED --------------------------------


def test_guarded_read_succeeds_and_stays_closed(_token_available):
    rows = smartsheet_client.get_rows(sheet_ids.SHEET_CONFIG)
    assert isinstance(rows, list)
    assert circuit_breaker.is_open() is False
    # A healthy success writes nothing (hot-path no-op): state file absent.
    assert not circuit_breaker.STATE_FILE.exists()


# ---- 2. guarded write round-trip, live ----------------------------------


def test_guarded_write_round_trip_closed(_token_available):
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("cb_write"),
        [
            {"title": "id_col", "type": "TEXT_NUMBER", "primary": True},
            {"title": "val", "type": "TEXT_NUMBER"},
        ],
    )
    try:
        row_ids = smartsheet_client.add_rows(sheet_id, [{"id_col": "r1", "val": "100"}])
        assert len(row_ids) == 1
        rows = smartsheet_client.get_rows(sheet_id)
        assert any(r.get("id_col") == "r1" for r in rows)
        smartsheet_client.update_rows(sheet_id, [{"_row_id": row_ids[0], "val": "200"}])
        # Every guarded call succeeded → breaker still CLOSED.
        assert circuit_breaker.is_open() is False
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


# ---- 3. trip on real counting failures → short-circuit → reset ----------


def test_breaker_trips_short_circuits_then_resets(_token_available, monkeypatch):
    client = smartsheet_client.get_client()
    orig_get_sheet = client.Sheets.get_sheet

    def _boom(*args, **kwargs):
        # A base SDK exception flows through the real `_translate` → a base
        # SmartsheetError, which is a COUNTING failure (not an ignored 401/403/404).
        raise sdk_exc.SmartsheetException("simulated sustained transport failure")

    monkeypatch.setattr(client.Sheets, "get_sheet", _boom)

    # threshold = 3 consecutive counting failures → OPEN
    for _ in range(3):
        with pytest.raises(smartsheet_client.SmartsheetError):
            smartsheet_client.get_rows(sheet_ids.SHEET_CONFIG)
    assert circuit_breaker.is_open() is True

    # Next call short-circuits WITHOUT calling through (still patched to boom,
    # but we should get the circuit-open type, not the underlying error).
    with pytest.raises(smartsheet_client.SmartsheetCircuitOpenError):
        smartsheet_client.get_rows(sheet_ids.SHEET_CONFIG)

    # Escape-hatch layer 2: delete the local state file → CLOSED (works even
    # during a total outage). Restore the SDK and confirm a real call succeeds.
    circuit_breaker.STATE_FILE.unlink()
    assert circuit_breaker.is_open() is False
    monkeypatch.setattr(client.Sheets, "get_sheet", orig_get_sheet)
    rows = smartsheet_client.get_rows(sheet_ids.SHEET_CONFIG)
    assert isinstance(rows, list)
    assert circuit_breaker.is_open() is False
