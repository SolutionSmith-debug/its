"""Live-API integration test for shared/project_routing.py.

Per Op Stds v16 §30 (SDK-vs-Live discipline): writes to a typed-column sheet
(CHECKBOX Active, TEXT_NUMBER Box Folder ID + Project Name primary key) need at
least one live round-trip to catch the body-shape drift that mocks can't —
specifically the CHECKBOX read-back projection (`_row_to_route` coerces the
Active cell to bool) and the numeric-vs-string folder-ID coercion.

This test:
  1. Adds a temporary ITS_Project_Routing row for a sandbox project (Active=True).
  2. Invalidates the in-process cache.
  3. Calls `get_folder_id` and asserts it resolves the row's Box folder ID.
  4. Flips the row to Active=False, re-reads, asserts it falls through (the
     active filter is exercised live, not just in the unit mock).
  5. Cleans up the row in `finally` so no orphan state.

Skipped automatically when:
  - ITS_SMARTSHEET_TOKEN unavailable.
  - SHEET_PROJECT_ROUTING is the placeholder 0 (sheet not yet built).

Run with: pytest -m integration tests/test_project_routing_integration.py
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shared import keychain, project_routing, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def _token_available() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


@pytest.fixture(scope="module")
def _sheet_built() -> int:
    sid = sheet_ids.SHEET_PROJECT_ROUTING
    if not sid:
        pytest.skip(
            "SHEET_PROJECT_ROUTING=0 placeholder; run "
            "scripts/migrations/build_its_project_routing_sheet.py first."
        )
    return sid


def _sandbox_project() -> str:
    """Per-run unique project name so concurrent runs don't collide on primary."""
    suffix = datetime.now(UTC).strftime("%H%M%S%f")
    return f"int-test-project-{suffix}"


def test_get_folder_id_live_roundtrip(_token_available, _sheet_built):
    """Live: write Active row → resolve → flip inactive → falls through → cleanup."""
    sheet_id = _sheet_built
    project = _sandbox_project()
    folder_id = "999000111222"

    [row_id] = smartsheet_client.add_rows(
        sheet_id,
        [{
            "Project Name": project,
            "Box Folder ID": folder_id,
            "Active": True,
            "Notes": "added by tests/test_project_routing_integration.py",
        }],
    )
    try:
        project_routing.invalidate_cache()
        resolved = project_routing.get_folder_id(project)
        assert resolved == folder_id, (
            f"expected {folder_id!r} for active sandbox project, got {resolved!r}"
        )

        # Flip to inactive and confirm the active filter drops it live. The
        # sandbox project isn't in BOX_PROJECT_FOLDERS, so it resolves to "".
        smartsheet_client.update_rows(
            sheet_id,
            [{"_row_id": row_id, "Active": False}],
        )
        project_routing.invalidate_cache()
        assert project_routing.get_folder_id(project) == "", (
            "inactive row must not resolve a folder ID"
        )
    finally:
        smartsheet_client.delete_rows(sheet_id, [row_id])
        project_routing.invalidate_cache()


def test_wired_sheet_fallback_warns_live(
    _token_available, _sheet_built, monkeypatch, caplog
):
    """Live: an inactive row + a wired sheet must resolve from the
    BOX_PROJECT_FOLDERS fallback AND emit the onboarding-gap WARN.

    This is the §30 complement to the happy path: it exercises the
    `get_folder_id` warn branch (`fallback and SHEET_PROJECT_ROUTING` both
    truthy) against the LIVE read of an inactive CHECKBOX row, catching any
    drift in how an unchecked Active cell projects back from the SDK. The
    fallback dict is monkeypatched to a UNIQUE sandbox key so it can't collide
    with a real seeded project row and stays hermetic w.r.t. the live data.
    """
    import logging

    sheet_id = _sheet_built
    project = _sandbox_project()
    monkeypatch.setattr(
        project_routing.defaults,
        "BOX_PROJECT_FOLDERS",
        {project: "fallback-folder-id"},
    )

    # Inactive row: present + readable (sheet is wired) but Active=False, so the
    # active filter drops it and resolution must fall through to the fallback.
    [row_id] = smartsheet_client.add_rows(
        sheet_id,
        [{
            "Project Name": project,
            "Box Folder ID": "sheet-folder-id-should-be-ignored",
            "Active": False,
            "Notes": "added by tests/test_project_routing_integration.py",
        }],
    )
    try:
        project_routing.invalidate_cache()
        with caplog.at_level(logging.WARNING, logger="shared.project_routing"):
            resolved = project_routing.get_folder_id(project)
        assert resolved == "fallback-folder-id", (
            f"expected the BOX_PROJECT_FOLDERS fallback, got {resolved!r}"
        )
        warns = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "ITS_Project_Routing" in r.getMessage()
        ]
        assert warns, "expected an onboarding-gap WARN when wired sheet misses"
        assert project in warns[0].getMessage()
    finally:
        smartsheet_client.delete_rows(sheet_id, [row_id])
        project_routing.invalidate_cache()
