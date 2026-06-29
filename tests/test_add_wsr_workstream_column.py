"""Tests for scripts/migrations/add_wsr_workstream_column.py (P1b).

All Smartsheet calls mocked — never hits the live API. Contract under test:
preview-by-default (no write), --commit creates the absent PICKLIST column with the
REGISTRY option set, backfills ONLY blank rows to 'safety', title+type idempotency,
and the wrong-typed-column refusal.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# sys.path-driven import (scripts/ has no __init__.py) — mirrors
# tests/test_add_dormant_picklist_columns.py.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import add_wsr_workstream_column as mig  # noqa: E402 — sys.path-driven import

from shared import picklist_validation, sheet_ids  # noqa: E402


def _patch_cols(mocker, cols):
    return mocker.patch.object(mig.smartsheet_client, "list_columns_with_options", return_value=cols)


def _patch_create(mocker):
    return mocker.patch.object(mig.smartsheet_client, "create_picklist_column", return_value=999)


def _patch_rows(mocker, rows):
    return mocker.patch.object(mig.smartsheet_client, "get_rows", return_value=rows)


def _patch_update(mocker):
    return mocker.patch.object(mig.smartsheet_client, "update_rows")


def test_registry_options_is_safety_only():
    assert mig._registry_options() == ["safety"]
    assert mig.BACKFILL_VALUE == "safety"
    # The column the migration writes is the same constant weekly_send's guard reads.
    assert mig.COLUMN == "Workstream"


def test_registry_options_unregistered_raises(monkeypatch):
    # Defensive: a missing REGISTRY entry must refuse rather than invent options.
    monkeypatch.delitem(
        picklist_validation.REGISTRY[sheet_ids.SHEET_WSR_HUMAN_REVIEW], "Workstream"
    )
    with pytest.raises(RuntimeError, match="not in picklist_validation.REGISTRY"):
        mig._registry_options()


def test_preview_default_no_write(mocker):
    _patch_cols(mocker, [])  # column absent
    create = _patch_create(mocker)
    _patch_rows(mocker, [{"_row_id": 1}, {"_row_id": 2}])
    update = _patch_update(mocker)

    present = mig.ensure_column(commit=False)
    set_count, _already = mig.backfill(commit=False, column_present=present)

    assert present is False
    create.assert_not_called()
    update.assert_not_called()
    assert set_count == 2  # would backfill the 2 rows


def test_commit_creates_column_with_registry_options(mocker):
    _patch_cols(mocker, [])  # absent
    create = _patch_create(mocker)

    present = mig.ensure_column(commit=True)

    assert present is True
    create.assert_called_once()
    assert create.call_args.args[0] == sheet_ids.SHEET_WSR_HUMAN_REVIEW
    assert create.call_args.args[1] == "Workstream"
    assert create.call_args.args[2] == ["safety"]


def test_idempotent_skip_when_present_picklist(mocker):
    _patch_cols(mocker, [{"id": 1, "title": "Workstream", "type": "PICKLIST", "options": ["safety"]}])
    create = _patch_create(mocker)

    present = mig.ensure_column(commit=True)

    assert present is True
    create.assert_not_called()


def test_wrong_typed_column_raises(mocker):
    _patch_cols(mocker, [{"id": 1, "title": "Workstream", "type": "TEXT_NUMBER", "options": []}])
    _patch_create(mocker)
    with pytest.raises(RuntimeError, match="not PICKLIST"):
        mig.ensure_column(commit=True)


def test_backfill_sets_blank_rows_only(mocker):
    _patch_cols(mocker, [{"id": 1, "title": "Workstream", "type": "PICKLIST", "options": ["safety"]}])
    rows = [
        {"_row_id": 10, "Workstream": ""},        # blank → backfilled
        {"_row_id": 11},                          # missing → backfilled
        {"_row_id": 12, "Workstream": "safety"},  # already tagged → skipped
    ]
    _patch_rows(mocker, rows)
    update = _patch_update(mocker)

    set_count, already = mig.backfill(commit=True, column_present=True)

    assert (set_count, already) == (2, 1)
    update.assert_called_once()
    payload = update.call_args.args[1]
    assert {p["_row_id"] for p in payload} == {10, 11}
    assert all(p["Workstream"] == "safety" for p in payload)


def test_backfill_noop_when_all_tagged(mocker):
    _patch_cols(mocker, [{"id": 1, "title": "Workstream", "type": "PICKLIST", "options": ["safety"]}])
    _patch_rows(mocker, [{"_row_id": 10, "Workstream": "safety"}])
    update = _patch_update(mocker)

    set_count, already = mig.backfill(commit=True, column_present=True)

    assert (set_count, already) == (0, 1)
    update.assert_not_called()
