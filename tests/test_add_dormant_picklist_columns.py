"""Tests for scripts/migrations/add_dormant_picklist_columns.py (Phase 3a, D1=ADD).

All Smartsheet calls are mocked — these never hit the live API. The migration's
contract under test: preview-by-default (no write), --commit creates absent
columns with the REGISTRY's option set, title+type idempotency (skip an existing
PICKLIST, but REFUSE to silently skip a wrong-typed column).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Insert scripts/migrations/ into sys.path so the migration module imports by its
# top-level name (matches tests/test_box_build_1111b.py). Importing it as
# `scripts.migrations.X` would make mypy resolve the same file under two module
# names ("found twice") because scripts/ has no __init__.py.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import add_dormant_picklist_columns as mig  # noqa: E402 — sys.path-driven import

from shared import picklist_validation, sheet_ids  # noqa: E402


def _patch_live(mocker, cols):
    """Patch list_columns_with_options to return `cols` for every sheet."""
    return mocker.patch.object(
        mig.smartsheet_client, "list_columns_with_options", return_value=cols,
    )


def _patch_create(mocker):
    return mocker.patch.object(
        mig.smartsheet_client, "create_picklist_column", return_value=12345,
    )


def test_registry_options_sorted():
    # Pulled from the registry, sorted for stable display.
    opts = mig._registry_options(sheet_ids.SHEET_QUARANTINE, "Disposition")
    assert opts == sorted(
        picklist_validation.REGISTRY[sheet_ids.SHEET_QUARANTINE]["Disposition"]
    )
    assert opts == ["DELETE", "ESCALATE", "RELEASE"]


def test_registry_options_unregistered_raises():
    with pytest.raises(RuntimeError, match="not in picklist_validation.REGISTRY"):
        mig._registry_options(sheet_ids.SHEET_ERRORS, "NoSuchColumn")


def test_preview_default_issues_no_write(mocker):
    _patch_live(mocker, [])  # both target columns absent
    create = _patch_create(mocker)

    added, skipped = mig.add_dormant_columns(commit=False)

    assert (added, skipped) == (2, 0)  # both TARGETS would be created
    create.assert_not_called()  # preview never writes


def test_commit_creates_absent_with_registry_options(mocker):
    _patch_live(mocker, [])  # absent everywhere
    create = _patch_create(mocker)

    added, skipped = mig.add_dormant_columns(commit=True)

    assert (added, skipped) == (2, 0)
    assert create.call_count == len(mig.TARGETS) == 2
    # Each create call carries the registry's sorted option set for that target.
    for call, (sheet_id, column) in zip(
        create.call_args_list, mig.TARGETS, strict=True
    ):
        assert call.args[0] == sheet_id
        assert call.args[1] == column
        assert call.args[2] == sorted(
            picklist_validation.REGISTRY[sheet_id][column]
        )


def test_skip_when_present_as_picklist(mocker):
    # Every target column already exists as a PICKLIST → idempotent skip.
    _patch_live(mocker, [
        {"id": 1, "title": "Workstream", "type": "PICKLIST", "options": []},
        {"id": 2, "title": "Disposition", "type": "PICKLIST", "options": []},
    ])
    create = _patch_create(mocker)

    added, skipped = mig.add_dormant_columns(commit=True)

    assert (added, skipped) == (0, 2)
    create.assert_not_called()


def test_wrong_typed_existing_column_raises(mocker):
    # M1 guard: a column that exists with the wrong type must NOT be silently
    # skipped (that leaves the schema broken + the audit still failing).
    _patch_live(mocker, [
        {"id": 1, "title": "Workstream", "type": "TEXT_NUMBER", "options": []},
    ])
    _patch_create(mocker)

    with pytest.raises(RuntimeError, match="not PICKLIST"):
        mig.add_dormant_columns(commit=True)
