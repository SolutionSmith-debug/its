"""Live-API integration test for safety_reports/weekly_generate.py.

Per Op Stds v11 §30 (SDK-vs-Live Integration Test Discipline) — every
typed-SDK write path needs a parallel integration test against a real
Smartsheet sandbox row. This file exercises:

  - ensure_current_week_folder against the live tree.
  - smartsheet_client.add_rows to seed sandbox Daily Reports rows.
  - weekly_generate._run_pipeline against a single sandbox project.
  - Anthropic API call (uses ITS_ANTHROPIC_KEY).
  - WPR_Pending_Review row write + cleanup.

Default `pytest -q` SKIPS this file (per pyproject.toml addopts:
-m 'not integration'). Run with `pytest -m integration`. Requires:

  - ITS_SMARTSHEET_TOKEN in macOS Keychain.
  - ITS_ANTHROPIC_KEY in macOS Keychain.

Without either, the module-level fixture skips the entire file.

Sandbox isolation:
    - Injects a synthetic "_int_weekly_generate_sandbox" project into
      PROJECT_NAME_BY_FOLDER_ID + FIELD_REPORTS_FOLDER_BY_PROJECT,
      pointed at FOLDER_SYSTEM_CONFIG so no real project tree gets
      polluted. Restored in teardown.
    - Week start is pinned to 1970-01-05 (a Monday far from any real
      cycle) so the resulting week folder name is collision-free.
    - Try/finally cleanup deletes: the WPR row, the Daily Reports +
      Weekly Rollup sheets, and the week folder. Smartsheet folder-
      delete cascades but explicit sheet deletes keep the trail tight
      if the folder delete hits a network blip.

Cost: each run hits Anthropic once per iterated project; with
PROJECT_NAME_BY_FOLDER_ID patched to a single sandbox entry, that's
one Sonnet call (~$0.01–$0.05 depending on Daily Reports row payload).

NOT run in CI: GitHub Actions has neither keychain secret.
"""
from __future__ import annotations

from datetime import date

import pytest
import requests  # type: ignore[import-untyped]

from safety_reports import weekly_generate
from shared import keychain, sheet_ids, smartsheet_client

pytestmark = pytest.mark.integration

SANDBOX_PROJECT = "_int_weekly_generate_sandbox"
SANDBOX_WEEK_START = date(1970, 1, 5)  # arbitrary far-past Monday, collision-free.


@pytest.fixture(scope="module")
def _smartsheet_token() -> str:
    try:
        token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    except Exception as e:  # noqa: BLE001 — skip on any keychain failure
        pytest.skip(f"ITS_SMARTSHEET_TOKEN unavailable: {e!r}")
    if not token:
        pytest.skip("ITS_SMARTSHEET_TOKEN returned empty")
    return token


@pytest.fixture(scope="module")
def _anthropic_key() -> str:
    try:
        key = keychain.get_secret("ITS_ANTHROPIC_KEY")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ITS_ANTHROPIC_KEY unavailable: {e!r}")
    if not key:
        pytest.skip("ITS_ANTHROPIC_KEY returned empty")
    return key


@pytest.fixture
def _sandbox_project_map():
    """Temporarily inject a sandbox project into both lookup maps.

    Restored in teardown so subsequent tests see the canonical maps.
    """
    original_field_reports = dict(sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT)
    original_project_names = dict(sheet_ids.PROJECT_NAME_BY_FOLDER_ID)
    # Single sandbox key so weekly_generate iterates only one project.
    sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT.clear()
    sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT[SANDBOX_PROJECT] = (
        sheet_ids.FOLDER_SYSTEM_CONFIG
    )
    sheet_ids.PROJECT_NAME_BY_FOLDER_ID.clear()
    sheet_ids.PROJECT_NAME_BY_FOLDER_ID[sheet_ids.FOLDER_SYSTEM_CONFIG] = (
        SANDBOX_PROJECT
    )
    try:
        yield
    finally:
        sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT.clear()
        sheet_ids.FIELD_REPORTS_FOLDER_BY_PROJECT.update(original_field_reports)
        sheet_ids.PROJECT_NAME_BY_FOLDER_ID.clear()
        sheet_ids.PROJECT_NAME_BY_FOLDER_ID.update(original_project_names)


def _delete_sheet_rest(sheet_id: int, token: str) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/sheets/{sheet_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _delete_folder_rest(folder_id: int, token: str) -> None:
    requests.delete(
        f"https://api.smartsheet.com/2.0/folders/{folder_id}",
        headers={"Authorization": f"Bearer {token}"},
    )


def _delete_wpr_row(row_id: int) -> None:
    smartsheet_client.delete_rows(sheet_ids.SHEET_WPR_PENDING_REVIEW, [row_id])


def test_weekly_generate_end_to_end_writes_wpr_row(
    _smartsheet_token: str,
    _anthropic_key: str,
    _sandbox_project_map: None,
) -> None:
    """End-to-end: seed daily rows, run weekly_generate, assert WPR row, cleanup."""
    from safety_reports.week_folder import ensure_current_week_folder

    # 1. Create the sandbox week scaffold (folder + 2 sheets).
    scaffold = ensure_current_week_folder(SANDBOX_PROJECT, SANDBOX_WEEK_START)

    sandbox_daily_row_ids: list[int] = []
    wpr_row_id_to_cleanup: int | None = None
    try:
        # 2. Seed two Daily Reports rows in the sandbox week.
        sandbox_daily_row_ids = smartsheet_client.add_rows(
            scaffold.daily_reports_sheet_id,
            [
                {
                    "Entry #": "1",
                    "Report Date": SANDBOX_WEEK_START.isoformat(),
                    "Category": "Daily JHA",
                    "Crew or Subcontractor": "ITS-SMOKE Crew A",
                    "Safety Topic / Report Title": "PPE compliance",
                    "Summary of Events": (
                        "[ITS-SMOKE-TEST] Crew completed daily PPE check; no findings."
                    ),
                },
                {
                    "Entry #": "2",
                    "Report Date": (
                        SANDBOX_WEEK_START.replace(day=SANDBOX_WEEK_START.day + 2)
                    ).isoformat(),
                    "Category": "Tool Box Talk",
                    "Crew or Subcontractor": "ITS-SMOKE Crew A",
                    "Safety Topic / Report Title": "Trip Hazards",
                    "Summary of Events": (
                        "[ITS-SMOKE-TEST] Toolbox talk on housekeeping + trip hazards."
                    ),
                },
            ],
        )

        # 3. Run the pipeline against the sandbox project for the target week.
        result = weekly_generate._run_pipeline(
            week_start_override=SANDBOX_WEEK_START
        )

        # 4. Assert the WPR row landed.
        assert result["aborted_empty_chain"] is False
        assert result["projects_processed"] == 1
        assert result["drafts_written"] == 1

        wpr_rows = smartsheet_client.get_rows(
            sheet_ids.SHEET_WPR_PENDING_REVIEW,
            filters={
                "Job": SANDBOX_PROJECT,
                "Week": SANDBOX_WEEK_START.isoformat(),
            },
        )
        assert len(wpr_rows) == 1
        wpr_row_id_to_cleanup = wpr_rows[0]["_row_id"]
        assert wpr_rows[0].get("Approved for Send") in (False, None, "")
        assert wpr_rows[0].get("Draft Body")
    finally:
        # 5. Cleanup — best-effort, never re-raise. Tear down in
        # reverse-creation order so we don't strand orphan dependencies.
        if wpr_row_id_to_cleanup is not None:
            try:
                _delete_wpr_row(wpr_row_id_to_cleanup)
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                pass
        if sandbox_daily_row_ids:
            try:
                smartsheet_client.delete_rows(
                    scaffold.daily_reports_sheet_id, sandbox_daily_row_ids
                )
            except Exception:  # noqa: BLE001
                pass
        # Folder + sheets — REST delete bypasses the typed SDK so the
        # cleanup path doesn't depend on the same code under test.
        try:
            _delete_sheet_rest(scaffold.daily_reports_sheet_id, _smartsheet_token)
        except Exception:  # noqa: BLE001
            pass
        try:
            _delete_sheet_rest(scaffold.weekly_rollup_sheet_id, _smartsheet_token)
        except Exception:  # noqa: BLE001
            pass
        try:
            _delete_folder_rest(scaffold.folder_id, _smartsheet_token)
        except Exception:  # noqa: BLE001
            pass
