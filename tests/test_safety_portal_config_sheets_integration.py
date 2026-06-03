"""Live-API integration test for the Safety Portal config sheets
(ITS_Active_Jobs + ITS_Forms_Catalog).

Per Op Stds v16 §30 (SDK-vs-Live discipline): the build migrations create typed
columns the SDK can serialize wrong — an "Active" PICKLIST (options must land as
exactly Active/Inactive/Archived) and two SYSTEM columns built via the SDK Column
model's `systemColumnType` (MODIFIED_DATE -> DATETIME, MODIFIED_BY ->
CONTACT_LIST). Mocks can't catch that body-shape drift, so verify the LIVE
built+seeded state.

Read-only: the seeded rows are PERMANENT config (not test fixtures), so this
asserts against them rather than writing/deleting throwaway rows — no orphan
risk and no dependence on write access.

Skipped automatically when:
  - ITS_SMARTSHEET_TOKEN unavailable.
  - The sheet constant is the placeholder 0 (sheet not yet built / flipped).

Run with: pytest -m integration tests/test_safety_portal_config_sheets_integration.py
"""
from __future__ import annotations

import pytest

from shared import keychain, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration

_ACTIVE_OPTIONS = {"Active", "Inactive", "Archived"}


@pytest.fixture(scope="module")
def _token_available() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


def _columns_by_title(sheet_id: int) -> dict[str, dict]:
    return {c["title"]: c for c in smartsheet_client.list_columns_with_options(sheet_id)}


def test_active_jobs_schema_and_seed(_token_available):
    """Live: ITS_Active_Jobs has the schema (incl. PICKLIST + system cols) and the 6 jobs."""
    sid = sheet_ids.SHEET_ACTIVE_JOBS
    if not sid:
        pytest.skip(
            "SHEET_ACTIVE_JOBS=0 placeholder; run "
            "scripts/migrations/build_its_active_jobs_sheet.py and flip sheet_ids.py first."
        )
    cols = _columns_by_title(sid)
    for title in (
        "Project Name", "Job ID", "Address", "Active", "Notes",
        "Last Modified", "Modified By",
    ):
        assert title in cols, f"ITS_Active_Jobs missing column {title!r}"

    assert cols["Active"]["type"] == "PICKLIST"
    assert set(cols["Active"]["options"]) == _ACTIVE_OPTIONS, (
        f"Active options drifted: {cols['Active']['options']!r}"
    )
    # System columns: type is the round-trip surface §30 guards (systemColumnType
    # sticking turns these into DATETIME / CONTACT_LIST live).
    assert cols["Last Modified"]["type"] == "DATETIME"
    assert cols["Modified By"]["type"] == "CONTACT_LIST"

    rows = smartsheet_client.get_rows(sid)
    job_ids = {r.get("Job ID") for r in rows}
    for jid in ("bradley-1", "bradley-2", "brimfield-1", "brimfield-2", "huntley", "rockford"):
        assert jid in job_ids, f"ITS_Active_Jobs missing seeded Job ID {jid!r}"


def test_forms_catalog_schema_and_seed(_token_available):
    """Live: ITS_Forms_Catalog has the schema (incl. PICKLIST) and the 4 locked forms."""
    sid = sheet_ids.SHEET_FORMS_CATALOG
    if not sid:
        pytest.skip(
            "SHEET_FORMS_CATALOG=0 placeholder; run "
            "scripts/migrations/build_its_forms_catalog_sheet.py and flip sheet_ids.py first."
        )
    cols = _columns_by_title(sid)
    for title in (
        "Form Name", "Form Code", "Active", "Description",
        "Display Order", "Available For Jobs", "Last Modified", "Modified By",
    ):
        assert title in cols, f"ITS_Forms_Catalog missing column {title!r}"

    assert cols["Active"]["type"] == "PICKLIST"
    assert set(cols["Active"]["options"]) == _ACTIVE_OPTIONS, (
        f"Active options drifted: {cols['Active']['options']!r}"
    )

    rows = smartsheet_client.get_rows(sid)
    form_codes = {r.get("Form Code") for r in rows}
    for code in (
        "jha-v1", "daily-site-safety-v1", "equipment-preinspection-v1", "toolbox-talk-v1",
    ):
        assert code in form_codes, f"ITS_Forms_Catalog missing seeded Form Code {code!r}"
