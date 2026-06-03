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

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import requests  # type: ignore[import-untyped]

from shared import keychain, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration


class _SecretToken:
    """Wraps the real ITS_SMARTSHEET_TOKEN so its value can never leak into a
    pytest failure traceback.

    pytest renders a failing test's fixture/argument values via ``repr()``.
    A fixture that returned the raw token string therefore printed the live
    secret into the traceback when one of these tests failed — which forced a
    real token rotation this session. ``__repr__`` here redacts (and ``str()``
    / f-strings fall back to it), so the value only escapes via an explicit
    ``.reveal()`` call — the REST cleanup helpers below are the sole callers.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the raw token. Call only where the real value is required
        (the ``Authorization: Bearer`` header in REST cleanup)."""
        return self._value

    def __repr__(self) -> str:
        return "<ITS_SMARTSHEET_TOKEN redacted>"


@pytest.fixture(scope="module")
def _token_available() -> _SecretToken:
    """Skip the whole module if ITS_SMARTSHEET_TOKEN isn't in Keychain.

    Returns the token wrapped in `_SecretToken` so the raw value cannot
    render in a failure traceback (see the class docstring).
    """
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return _SecretToken(token)


@pytest.fixture(scope="module", autouse=True)
def _reset_smartsheet_client() -> Iterator[None]:
    """Force a fresh real-token Smartsheet client for this module.

    `smartsheet_client._client` is a process-wide singleton built lazily from
    the keychain token. In an isolated `pytest -m integration` run the
    conftest keychain opt-out already guarantees it is built with the real
    token, so this fixture is a no-op there. But in a MIXED-process run (full
    suite / `pytest -m ''` / IDE "run all"), an earlier unit test runs with
    the autouse keychain stub active and can prime `_client` with the fake
    `"test-ITS_SMARTSHEET_TOKEN"` — which would then 401 here. Resetting on
    entry forces a rebuild from the (now real) keychain; resetting on exit
    keeps this module's real-token client from leaking into a unit test that
    runs afterward in the same process.
    """
    smartsheet_client._client = None
    yield
    smartsheet_client._client = None


def _delete_sheet_rest(sheet_id: int, token: _SecretToken) -> None:
    """Cleanup helper — direct REST DELETE (no SDK wrapper today).

    Takes the redacting `_SecretToken` (not a raw str) so the value cannot
    render in a traceback frame; `.reveal()` is called only to build the
    Authorization header.
    """
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token.reveal()}"},
    )


def _delete_folder_rest(folder_id: int, token: _SecretToken) -> None:
    """Cleanup helper — direct REST DELETE for a folder (no SDK wrapper today).

    Takes the redacting `_SecretToken` wrapper; see `_delete_sheet_rest`.
    """
    requests.delete(
        f"https://api.smartsheet.com/2.0/folders/{folder_id}",
        headers={"Authorization": f"Bearer {token.reveal()}"},
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


# ---- ensure_picklist_options: additive, idempotent, no-removal ----------


def test_ensure_picklist_options_additive_round_trip(_token_available):
    """Live §30: additive ensure preserves existing options + order, appends
    only the missing, is idempotent on re-run, and previews without writing.

    This is the SDK-vs-Live guard for the picklist-drift reconcile: a
    SimpleNamespace mock would not catch the REPLACE-style body shape that the
    additive wrapper depends on (read current → union → write the full union).
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("ensure_additive"),
        [
            {"title": "id_col", "type": "TEXT_NUMBER", "primary": True},
            {"title": "pl_col", "type": "PICKLIST",
                "options": ["seed_a", "seed_b"]},
        ],
    )
    try:
        # Add seed_b (already present) + two new — only the new two append,
        # existing seeds + order preserved.
        result = smartsheet_client.ensure_picklist_options(
            sheet_id, "pl_col", ["seed_b", "new_x", "new_y"],
        )
        # result.final_options is OUR deterministic construction (current+missing),
        # so its order is asserted exactly.
        assert result.applied is True
        assert result.added == ("new_x", "new_y")
        assert result.final_options == ("seed_a", "seed_b", "new_x", "new_y")

        live = smartsheet_client.list_columns_with_options(sheet_id)
        pl_after = next(c for c in live if c["title"] == "pl_col")
        # The LIVE re-read is compared as a SET — Smartsheet does not guarantee
        # API-side option-order preservation (see update_column_options docstring),
        # so an exact-order assert here would flake. The invariants that matter:
        # no removal (seeds survive) + the new values are present.
        assert set(pl_after["options"]) == {"seed_a", "seed_b", "new_x", "new_y"}
        assert "seed_a" in pl_after["options"] and "seed_b" in pl_after["options"]

        # Idempotent: re-running the same request issues no write.
        again = smartsheet_client.ensure_picklist_options(
            sheet_id, "pl_col", ["seed_b", "new_x", "new_y"],
        )
        assert again.applied is False
        assert again.added == ()
        assert again.final_options == ("seed_a", "seed_b", "new_x", "new_y")

        # dry_run previews the next addition without mutating the live column.
        preview = smartsheet_client.ensure_picklist_options(
            sheet_id, "pl_col", ["new_z"], dry_run=True,
        )
        assert preview.applied is False
        assert preview.added == ("new_z",)
        live2 = smartsheet_client.list_columns_with_options(sheet_id)
        pl_preview = next(c for c in live2 if c["title"] == "pl_col")
        assert "new_z" not in pl_preview["options"]
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


# ---- find_folder_by_name_in_folder + create_folder_in_folder ----------


def test_find_folder_by_name_in_folder_round_trip(_token_available):
    """Create folder → find → cleanup. Mirrors the sheet round-trip.

    Sandbox parent is FOLDER_SYSTEM_CONFIG to match the existing
    integration-test precedent (no dedicated test-only folder constant
    today; see PR description for the trade-off discussion). The
    sandbox folder is name-namespaced via `_sandbox_name`, so it's
    visually distinguishable from real config artifacts and gets
    deleted in `finally` regardless of test outcome.
    """
    name = _sandbox_name("find_folder")
    folder_id = smartsheet_client.create_folder_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG, name
    )
    try:
        found_id = smartsheet_client.find_folder_by_name_in_folder(
            sheet_ids.FOLDER_SYSTEM_CONFIG, name
        )
        assert found_id == folder_id

        # Negative case: a name that doesn't exist returns None.
        missing = smartsheet_client.find_folder_by_name_in_folder(
            sheet_ids.FOLDER_SYSTEM_CONFIG, name + "_DOES_NOT_EXIST"
        )
        assert missing is None
    finally:
        _delete_folder_rest(folder_id, _token_available)


# ---- find_row_by_primary + update_row_cells_by_id (PR #59.5) ------------


def test_find_row_by_primary_live_round_trip(_token_available):
    """Create sheet → add 2 rows → find by primary → update by ID → re-read.

    Exercises both new helpers against the live API in one cycle to catch
    body-shape drift the unit tests' SDK mocks can't catch (the PR #47/#48/#49
    failure mode this integration file was created for).
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("find_by_primary"),
        [
            {"title": "Name", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Status", "type": "TEXT_NUMBER"},
            {"title": "Count", "type": "TEXT_NUMBER"},
        ],
    )
    try:
        # Add two rows so the find_by_primary lookup has to discriminate.
        row_ids = smartsheet_client.add_rows(
            sheet_id,
            [
                {"Name": "alpha", "Status": "OK", "Count": "0"},
                {"Name": "beta",  "Status": "WARN", "Count": "5"},
            ],
        )
        assert len(row_ids) == 2

        # Look up the live columns to discover the Name column ID — the
        # primary-key lookup is by ID, so we need the live ID.
        cols = smartsheet_client.list_columns_with_options(sheet_id)
        name_col_id = next(c["id"] for c in cols if c["title"] == "Name")
        status_col_id = next(c["id"] for c in cols if c["title"] == "Status")
        count_col_id = next(c["id"] for c in cols if c["title"] == "Count")

        # find_row_by_primary returns the matching row's title-keyed dict.
        beta = smartsheet_client.find_row_by_primary(sheet_id, name_col_id, "beta")
        assert beta is not None
        assert beta["Name"] == "beta"
        assert beta["Status"] == "WARN"
        assert beta["_row_id"] == row_ids[1]

        # find_row_by_primary returns None on a missing primary value.
        gamma = smartsheet_client.find_row_by_primary(sheet_id, name_col_id, "gamma")
        assert gamma is None

        # update_row_cells_by_id updates by column ID, no title-cache lookup.
        smartsheet_client.update_row_cells_by_id(
            sheet_id,
            row_ids[1],
            {status_col_id: "OK", count_col_id: "99"},
        )

        # Re-read confirms the update landed.
        beta_after = smartsheet_client.find_row_by_primary(sheet_id, name_col_id, "beta")
        assert beta_after is not None
        assert beta_after["Status"] == "OK"
        assert beta_after["Count"] == "99"
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


def test_add_row_by_id_live_round_trip(_token_available):
    """Create sheet → add_row_by_id (ID-keyed create) → find → verify (A1).

    Guards the self-provision create path against the body-shape drift the
    unit-test SDK mocks can't catch — specifically the `result.result[0].id`
    return shape and the column_id-keyed Cell payload. Mirrors the
    find_row_by_primary round-trip; self-cleans by deleting the throwaway sheet.
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("add_row_by_id"),
        [
            {"title": "Name", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Status", "type": "TEXT_NUMBER"},
        ],
    )
    try:
        cols = smartsheet_client.list_columns_with_options(sheet_id)
        name_col_id = next(c["id"] for c in cols if c["title"] == "Name")
        status_col_id = next(c["id"] for c in cols if c["title"] == "Status")

        new_id = smartsheet_client.add_row_by_id(
            sheet_id,
            {name_col_id: "delta", status_col_id: "OK"},
        )
        assert isinstance(new_id, int)

        found = smartsheet_client.find_row_by_primary(sheet_id, name_col_id, "delta")
        assert found is not None
        assert found["_row_id"] == new_id
        assert found["Status"] == "OK"
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


def test_update_row_cells_by_id_raises_not_found_on_missing_row(_token_available):
    """A 404 on a non-existent row id surfaces as SmartsheetNotFoundError.

    Regression guard for the heartbeat-cache 404 invalidation path —
    intake_poll relies on this exception type to know when to invalidate
    the heartbeat row-id cache.
    """
    sheet_id = smartsheet_client.create_sheet_in_folder(
        sheet_ids.FOLDER_SYSTEM_CONFIG,
        _sandbox_name("not_found_row"),
        [
            {"title": "Name", "type": "TEXT_NUMBER", "primary": True},
        ],
    )
    try:
        cols = smartsheet_client.list_columns_with_options(sheet_id)
        name_col_id = next(c["id"] for c in cols if c["title"] == "Name")
        bogus_row_id = 1  # No row with id 1 exists on a fresh sheet.
        with pytest.raises(smartsheet_client.SmartsheetNotFoundError):
            smartsheet_client.update_row_cells_by_id(
                sheet_id,
                bogus_row_id,
                {name_col_id: "anything"},
            )
    finally:
        _delete_sheet_rest(sheet_id, _token_available)


def test_verify_write_capability_live(_token_available):
    """B2: the real write-capability probe creates a throwaway sheet (proving
    the live token can WRITE) and returns its id; cleanup goes through
    `delete_sheet_settling`, exercising the TIGHT back-to-back create→delete
    with NO settle wait.

    This is the regression lock for the B2-smoke finding: an immediate delete
    after create can 404 / errorCode 5036 (create→delete eventual consistency).
    The earlier version called the plain `delete_sheet` and passed only by
    winning the timing race; the settle retry makes it reliable. (Same flake
    class as the docs/tech_debt.md create→read entry.)

    A healthy read-write token passes; a read-only/mis-scoped token would raise
    SmartsheetWriteCapabilityError at the create step — which is the whole point.
    """
    sheet_id = smartsheet_client.verify_write_capability()
    assert isinstance(sheet_id, int)
    smartsheet_client.delete_sheet_settling(sheet_id)
