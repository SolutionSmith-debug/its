"""Live-API integration tests for shared/smartsheet_client.py helpers.

Why this file exists:
    PRs #47/#48/#49 each surfaced one body-shape mismatch the SDK accepted
    silently but the live Smartsheet API rejected. The class of bug:
    `SimpleNamespace`-based mocks at the SDK boundary don't enforce the
    live API's contract on body shape, required fields, or value
    wrapping (e.g. EnumeratedValue vs plain string). Three consecutive
    hotfix PRs is too many.

    This file exercises the full create → list → update → delete cycle
    against a real Smartsheet sandbox sheet. Any future shape drift
    surfaces here in one pass instead of three iterations.

How to run:
    Default `pytest -q` SKIPS this file (per pyproject.toml addopts:
    -m 'not integration'). To run:

        pytest -m integration

    Requires ITS_SMARTSHEET_TOKEN in macOS Keychain (the same source
    the runtime SDK uses). Without that, the test module-level
    `_token_available` fixture skips the whole module cleanly.

    Each test creates a sandbox sheet, exercises one cycle, then
    deletes the sheet in its `finally` block — no orphan state, even
    on test failure.

When to run:
    - Before merging any change to shared/smartsheet_client.py.
    - Before merging any change to shared/picklist_sync.py that touches
      the SDK call sites.
    - Periodically (operator judgment) to catch upstream SDK drift.

NOT run in CI: GitHub Actions doesn't have access to the operator's
Keychain. Running these in CI would require a sandbox token in
repository secrets, which is a deliberate decision the operator
hasn't made.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import requests  # type: ignore[import-untyped]

from shared import keychain, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _token_available() -> str:
    """Skip the whole module if ITS_SMARTSHEET_TOKEN isn't in Keychain."""
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


def _delete_sheet_rest(sheet_id: int, token: str) -> None:
    """Cleanup helper — direct REST DELETE (no SDK wrapper today)."""
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _sandbox_name(label: str) -> str:
    """Build a sandbox sheet name <= 50 chars (Smartsheet's hard limit
    on sheet.name; surfaced live during the first integration-test run
    as errorCode 1041).

    Layout: `_int_<label>_HHMMSS_µµµµµµ` — drops the date prefix to save
    9 chars and shortens the namespace prefix from `_integration_` (12)
    to `_int_` (5). HHMMSS + microseconds keeps uniqueness within a run.
    For `label="update_round_trip_multi"` (the longest label here): 5 +
    23 + 1 + 13 = 42 chars. Plenty of headroom for any new label up to
    ~30 chars before bumping the ceiling again.
    """
    ts = datetime.now(UTC).strftime("%H%M%S_%f")
    name = f"_int_{label}_{ts}"
    assert len(name) <= 50, (
        f"sandbox name {name!r} is {len(name)} chars; Smartsheet sheet "
        f"names must be <= 50 (errorCode 1041). Shorten label."
    )
    return name


# ---- list_columns_with_options: type normalization ---------------------


def test_list_columns_with_options_unwraps_picklist_type(_token_available):
    """list_columns_with_options must return col['type'] as plain str.

    Regression guard for PR #49: the live SDK wraps `type` for
    option-bearing columns in an `EnumeratedValue`. If the helper
    doesn't unwrap, downstream `update_column_options` calls send a
    body without `type` (the SDK strips the wrapped value silently)
    and the API rejects with errorCode 1090.

    MULTI_PICKLIST coverage is NOT exercised here: surfaced live during
    the PR #51 integration-test run, Smartsheet returns
    `type=TEXT_NUMBER` for MULTI_PICKLIST columns when read back after
    sheet creation. Whether that's a render-vs-storage distinction or
    a separate creation flow (sheet-create vs `add_column` POST) is a
    Smartsheet API quirk, not a defect in `list_columns_with_options`.
    Unit-level MULTI_PICKLIST coverage stays in
    tests/test_smartsheet_client.py::test_update_column_options_accepts_multi_picklist.
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("type_unwrap"),
        [
            {"title": "id_col", "type": "TEXT_NUMBER", "primary": True},
            {"title": "pl_col", "type": "PICKLIST", "options": ["seed"]},
        ],
    )
    try:
        cols = smartsheet_client.list_columns_with_options(sheet_id)
        by_title = {c["title"]: c for c in cols}

        # type must be a plain str for both columns.
        assert isinstance(by_title["id_col"]["type"], str)
        assert by_title["id_col"]["type"] == "TEXT_NUMBER"

        assert isinstance(by_title["pl_col"]["type"], str)
        assert by_title["pl_col"]["type"] == "PICKLIST"
        assert by_title["pl_col"]["options"] == ["seed"]
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


# ---- update_column_options: full round-trip ----------------------------


def test_update_column_options_round_trip_picklist(_token_available):
    """Full add cycle: create sheet → list → update options → list → verify.

    Verifies the body shape requirements landed by PRs #47, #48, #49 all
    hold end-to-end against the live API:
      - id NOT in body (PR #47, errorCode 1032)
      - type IS in body (PR #48, errorCode 1090)
      - type is plain str (PR #49)
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("update_round_trip"),
        [
            {"title": "id_col", "type": "TEXT_NUMBER", "primary": True},
            {"title": "pl_col", "type": "PICKLIST", "options": ["seed"]},
        ],
    )
    try:
        cols = smartsheet_client.list_columns_with_options(sheet_id)
        pl_col = next(c for c in cols if c["title"] == "pl_col")
        new_options = ["Alpha", "Bravo", "Charlie"]

        smartsheet_client.update_column_options(
            sheet_id, pl_col["id"], new_options, column_type=pl_col["type"]
        )

        cols = smartsheet_client.list_columns_with_options(sheet_id)
        pl_col_after = next(c for c in cols if c["title"] == "pl_col")
        assert sorted(pl_col_after["options"]) == sorted(new_options)
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


# MULTI_PICKLIST round-trip intentionally not exercised at integration
# level: Smartsheet returns type=TEXT_NUMBER for MULTI_PICKLIST columns
# read back after sheet creation (live-API quirk; see the unwrap_picklist_type
# docstring above). Unit-level coverage in
# tests/test_smartsheet_client.py::test_update_column_options_accepts_multi_picklist
# verifies the helper's body shape; the round-trip would need a separate
# `add_column` POST flow to land MULTI_PICKLIST distinguishably, deferred.


def test_update_column_options_replaces_not_appends(_token_available):
    """The API replaces the whole options list — confirm seed value is gone."""
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("replace_semantics"),
        [
            {"title": "id_col", "type": "TEXT_NUMBER", "primary": True},
            {"title": "pl_col", "type": "PICKLIST",
                "options": ["original_seed_value"]},
        ],
    )
    try:
        cols = smartsheet_client.list_columns_with_options(sheet_id)
        pl_col = next(c for c in cols if c["title"] == "pl_col")
        assert "original_seed_value" in pl_col["options"]

        smartsheet_client.update_column_options(
            sheet_id, pl_col["id"], ["NewOnly"], column_type=pl_col["type"]
        )

        cols = smartsheet_client.list_columns_with_options(sheet_id)
        pl_col_after = next(c for c in cols if c["title"] == "pl_col")
        # Original seed gone — replace semantics, not append.
        assert pl_col_after["options"] == ["NewOnly"]
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


# ---- find_sheet_by_name_in_folder + create_sheet_in_folder ------------


def test_find_sheet_by_name_in_folder_round_trip(_token_available):
    """Create → find → confirm match → cleanup. Idempotency-helper contract."""
    name = _sandbox_name("find_round_trip")
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        name,
        [{"title": "id_col", "type": "TEXT_NUMBER", "primary": True}],
    )
    try:
        found_id = smartsheet_client.find_sheet_by_name_in_folder(
            sheet_ids.FOLDER_SYSTEM_CONFIG, name
        )
        assert found_id == sheet_id

        # Negative case: a name that doesn't exist returns None.
        missing = smartsheet_client.find_sheet_by_name_in_folder(
            sheet_ids.FOLDER_SYSTEM_CONFIG, name + "_DOES_NOT_EXIST"
        )
        assert missing is None
    finally:
        _delete_sheet_rest(sheet_id, _token_available)
