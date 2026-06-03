"""Tests for shared/project_routing.py — get_folder_id + row projection + cache.

All Smartsheet calls are mocked at the boundary; no live sheet hits. The
fallback paths monkeypatch `project_routing.defaults.BOX_PROJECT_FOLDERS` and
`project_routing.sheet_ids.SHEET_PROJECT_ROUTING` so the tests are hermetic
(independent of the live defaults dict and the sheet-wiring placeholder).

Run with: pytest -q tests/test_project_routing.py
"""
from __future__ import annotations

import logging
import time

import pytest

from shared import project_routing, smartsheet_client
from shared.project_routing import (
    CACHE_TTL_SECONDS,
    ProjectRoute,
    _row_to_route,
    get_folder_id,
    invalidate_cache,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Each test starts (and ends) with an empty cache."""
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def fallback(monkeypatch):
    """A hermetic BOX_PROJECT_FOLDERS fallback dict."""
    table = {"Bradley 1": "383795291728", "Huntley": "383796738311"}
    monkeypatch.setattr(project_routing.defaults, "BOX_PROJECT_FOLDERS", table)
    return table


@pytest.fixture
def sheet_wired(monkeypatch):
    """Make SHEET_PROJECT_ROUTING look wired (nonzero) for warn-branch tests."""
    monkeypatch.setattr(project_routing.sheet_ids, "SHEET_PROJECT_ROUTING", 999001)
    return 999001


@pytest.fixture
def sheet_unwired(monkeypatch):
    """Force the pre-cutover unwired state (SHEET_PROJECT_ROUTING == 0).

    Post-E1-cutover (2026-06-03) the module constant is a real sheet id, so the
    pre-cutover "sheet not yet built" behavior must be SIMULATED rather than
    relying on the default placeholder. Mirrors `sheet_wired`.
    """
    monkeypatch.setattr(project_routing.sheet_ids, "SHEET_PROJECT_ROUTING", 0)
    return 0


def _row(
    *,
    project_name: str = "Bradley 1",
    box_folder_id="383795291728",
    active: bool = True,
    notes: str = "",
    row_id: int = 1000,
) -> dict:
    return {
        "_row_id": row_id,
        "Project Name": project_name,
        "Box Folder ID": box_folder_id,
        "Active": active,
        "Notes": notes,
    }


def _patch_get_rows(mocker, rows):
    return mocker.patch(
        "shared.project_routing.smartsheet_client.get_rows",
        return_value=rows,
    )


# ---- get_folder_id() — sheet hit -----------------------------------------


def test_active_sheet_row_resolves_folder_id(mocker, sheet_wired):
    _patch_get_rows(mocker, [_row(project_name="Bradley 1", box_folder_id="111")])
    assert get_folder_id("Bradley 1") == "111"


def test_sheet_row_shadows_box_project_folders_fallback(mocker, fallback, sheet_wired):
    # Sheet says 111; the BOX_PROJECT_FOLDERS fallback says 383795291728.
    # The sheet (canonical) must win.
    _patch_get_rows(mocker, [_row(project_name="Bradley 1", box_folder_id="111")])
    assert get_folder_id("Bradley 1") == "111"


def test_inactive_row_is_skipped_and_falls_back(mocker, fallback, sheet_wired, caplog):
    # Active=False → not a match. Falls through to the BOX_PROJECT_FOLDERS
    # fallback (which is the wired-but-missing → warn path).
    _patch_get_rows(
        mocker, [_row(project_name="Bradley 1", box_folder_id="111", active=False)],
    )
    with caplog.at_level(logging.WARNING):
        assert get_folder_id("Bradley 1") == "383795291728"
    assert any(r.levelname == "WARNING" for r in caplog.records)


# ---- get_folder_id() — fallback paths ------------------------------------


def test_pre_cutover_sheet_unwired_falls_back_silently(
    mocker, fallback, sheet_unwired, caplog
):
    # SHEET_PROJECT_ROUTING == 0 (unwired, simulated post-cutover) → get_rows
    # raises NotFound. The fallback is expected pre-cutover, so NO warning is
    # emitted (the warn branch requires SHEET_PROJECT_ROUTING truthy).
    mocker.patch(
        "shared.project_routing.smartsheet_client.get_rows",
        side_effect=smartsheet_client.SmartsheetNotFoundError("id 0 → 404"),
    )
    with caplog.at_level(logging.WARNING):
        assert get_folder_id("Huntley") == "383796738311"
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_wired_but_project_absent_warns_and_falls_back(mocker, fallback, sheet_wired, caplog):
    # Sheet is wired and readable but has no row for this project → fallback
    # resolves it AND a WARN flags the onboarding gap.
    _patch_get_rows(mocker, [_row(project_name="Some Other Project")])
    with caplog.at_level(logging.WARNING):
        assert get_folder_id("Huntley") == "383796738311"
    warns = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warns) == 1
    assert "Huntley" in warns[0].getMessage()
    assert "ITS_Project_Routing" in warns[0].getMessage()


def test_total_miss_returns_empty_string(mocker, fallback, sheet_wired):
    # Not in the sheet and not in the fallback → "" (caller soft-fails).
    _patch_get_rows(mocker, [_row(project_name="Some Other Project")])
    assert get_folder_id("Nonexistent Project") == ""


def test_transient_read_failure_warns_and_falls_back(mocker, fallback, sheet_wired, caplog):
    # A non-404 SmartsheetError is transient: degrade to fallback, never crash,
    # and emit the read-failed WARN.
    mocker.patch(
        "shared.project_routing.smartsheet_client.get_rows",
        side_effect=smartsheet_client.SmartsheetRateLimitError("429"),
    )
    with caplog.at_level(logging.WARNING):
        assert get_folder_id("Bradley 1") == "383795291728"
    assert any(
        "read failed" in r.getMessage() for r in caplog.records
        if r.levelname == "WARNING"
    )


# ---- _row_to_route() projection ------------------------------------------


def test_row_projection_typed_fields():
    route = _row_to_route(_row(notes="seed note", row_id=42))
    assert route == ProjectRoute(
        project_name="Bradley 1",
        box_folder_id="383795291728",
        active=True,
        notes="seed note",
        row_id=42,
    )


def test_numeric_folder_id_coerced_to_digit_string():
    # Smartsheet may hand back a TEXT_NUMBER folder ID as an int/float.
    assert _row_to_route(_row(box_folder_id=383795291728)).box_folder_id == "383795291728"
    assert _row_to_route(_row(box_folder_id=383795291728.0)).box_folder_id == "383795291728"


def test_bool_folder_id_is_not_treated_as_a_number():
    # bool is an int subclass; a stray checkbox value must not become "1"/"0".
    assert _row_to_route(_row(box_folder_id=True)).box_folder_id == ""


def test_blank_active_cell_is_inactive():
    # A blank/absent CHECKBOX reads falsy → deny-by-default for a half-filled row.
    assert _row_to_route(_row(active=None)).active is False
    row = _row()
    del row["Active"]
    assert _row_to_route(row).active is False


def test_row_without_project_name_is_dropped():
    row = _row()
    del row["Project Name"]
    assert _row_to_route(row) is None
    assert _row_to_route(_row(project_name="   ")) is None


def test_row_without_row_id_is_dropped():
    row = _row()
    del row["_row_id"]
    assert _row_to_route(row) is None


# ---- cache behavior ------------------------------------------------------


def test_cache_hit_skips_second_smartsheet_call(mocker, sheet_wired):
    get_rows = _patch_get_rows(mocker, [_row()])
    get_folder_id("Bradley 1")
    get_folder_id("Bradley 1")
    assert get_rows.call_count == 1


def test_cache_expires_and_refetches(mocker, sheet_wired):
    get_rows = _patch_get_rows(mocker, [_row()])
    get_folder_id("Bradley 1")
    assert get_rows.call_count == 1
    assert project_routing._cache is not None
    routes, _expires = project_routing._cache
    project_routing._cache = (routes, time.monotonic() - 1.0)
    get_folder_id("Bradley 1")
    assert get_rows.call_count == 2


# ---- module hygiene ------------------------------------------------------


def test_cache_ttl_is_60_seconds():
    # Pinned to the documented 60s TTL — change requires updating both the
    # constant AND the module docstring.
    assert CACHE_TTL_SECONDS == 60.0


def test_dataclass_is_frozen_hashable():
    route = _row_to_route(_row())
    assert route is not None
    # Frozen dataclasses are hashable — usable as a set/dict key.
    assert isinstance(hash(route), int)
