"""Tests for scripts/migrations/hours_log_task_column.py (2026-07-05 Task-column change).

All Smartsheet REST calls are mocked at the three `_get/_post/_delete_json` helpers — never hits
the live API (and never calls `_headers`/Keychain). Contract under test: scoped workspace-traversal
discovery (recurse folders, filter by the ' — Hours Log' suffix, dedupe), the name-guard, idempotent
add (skip-if-present, wrong-type raise, create-at-Hours+1, preview-no-write), idempotent drop
(skip-if-absent, delete-if-present, preview-no-write).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# sys.path-driven import (scripts/ has no __init__.py) — mirrors tests/test_add_wsr_workstream_column.py.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import hours_log_task_column as mig  # noqa: E402 — sys.path-driven import


def _cols(*titles_types: tuple[str, str]) -> list[dict[str, object]]:
    """Build a columns list with sequential indexes + ids from (title, type) tuples."""
    return [
        {"title": t, "type": ty, "index": i, "id": 1000 + i}
        for i, (t, ty) in enumerate(titles_types)
    ]


# ---- discovery (scoped workspace traversal, not /search) ----------------------


def test_discover_recurses_folders_filters_suffix_dedupes(mocker):
    ws = {
        "sheets": [
            {"id": 1, "name": "Top — Hours Log"},
            {"id": 2, "name": "Not a log"},
        ],
        "folders": [
            {
                "sheets": [
                    {"id": 3, "name": "Job A — Hours Log"},
                    {"id": 3, "name": "Job A — Hours Log"},  # duplicate id → deduped
                ],
                "folders": [{"sheets": [{"id": 4, "name": "Job B — Hours Log"}]}],
            },
            {"sheets": [{"id": 5, "name": "Job C — Week 1"}]},  # wrong suffix → excluded
        ],
    }
    mocker.patch.object(mig, "_get_json", return_value=ws)
    found = mig._discover_hours_log_sheets()
    assert sorted(found) == [
        (1, "Top — Hours Log"),
        (3, "Job A — Hours Log"),
        (4, "Job B — Hours Log"),
    ]
    assert [sid for sid, _ in found].count(3) == 1  # deduped


def test_discover_handles_empty_workspace(mocker):
    mocker.patch.object(mig, "_get_json", return_value={"sheets": None, "folders": None})
    assert mig._discover_hours_log_sheets() == []


# ---- name-guard ---------------------------------------------------------------


def test_guard_rejects_non_hours_log_name():
    with pytest.raises(RuntimeError, match="name-guard"):
        mig._guard("Some Random Sheet")


def test_guard_allows_hours_log_name():
    mig._guard("Job X — Hours Log")  # no raise


def test_add_guarded_against_non_hours_log_sheet():
    # _guard runs BEFORE any GET, so a mis-targeted --sheet-id is refused up front.
    with pytest.raises(RuntimeError, match="name-guard"):
        mig._add_task(9, "Random Sheet", commit=True)


# ---- add ----------------------------------------------------------------------


def test_add_creates_task_after_hours_when_absent(mocker):
    sheet = {
        "name": "Job — Hours Log",
        "columns": _cols(("Entry", "TEXT_NUMBER"), ("Hours", "TEXT_NUMBER"), ("Notes", "TEXT_NUMBER")),
    }
    mocker.patch.object(mig, "_get_json", return_value=sheet)
    post = mocker.patch.object(mig, "_post_json", return_value={"result": [{"id": 555}]})
    assert mig._add_task(9, "Job — Hours Log", commit=True) == "created"
    # Task posted TEXT_NUMBER at index = Hours index (1) + 1 = 2
    assert post.call_args.args[1] == [{"title": "Task", "type": "TEXT_NUMBER", "index": 2}]


def test_add_skips_when_present_correct_type(mocker):
    sheet = {
        "name": "Job — Hours Log",
        "columns": _cols(("Hours", "TEXT_NUMBER"), ("Task", "TEXT_NUMBER"), ("Notes", "TEXT_NUMBER")),
    }
    mocker.patch.object(mig, "_get_json", return_value=sheet)
    post = mocker.patch.object(mig, "_post_json")
    assert mig._add_task(9, "Job — Hours Log", commit=True) == "exists"
    post.assert_not_called()


def test_add_raises_on_wrong_typed_task(mocker):
    sheet = {"name": "Job — Hours Log", "columns": _cols(("Hours", "TEXT_NUMBER"), ("Task", "PICKLIST"))}
    mocker.patch.object(mig, "_get_json", return_value=sheet)
    with pytest.raises(RuntimeError, match="not TEXT_NUMBER"):
        mig._add_task(9, "Job — Hours Log", commit=True)


def test_add_preview_does_not_post(mocker):
    sheet = {"name": "Job — Hours Log", "columns": _cols(("Hours", "TEXT_NUMBER"), ("Notes", "TEXT_NUMBER"))}
    mocker.patch.object(mig, "_get_json", return_value=sheet)
    post = mocker.patch.object(mig, "_post_json")
    assert mig._add_task(9, "Job — Hours Log", commit=False) == "would-create"
    post.assert_not_called()


# ---- drop ---------------------------------------------------------------------


def test_drop_deletes_started_and_ended_when_present(mocker):
    sheet = {
        "name": "Job — Hours Log",
        "columns": [
            {"title": "Started", "type": "TEXT_NUMBER", "id": 71},
            {"title": "Ended", "type": "TEXT_NUMBER", "id": 72},
            {"title": "Task", "type": "TEXT_NUMBER", "id": 73},
        ],
    }
    mocker.patch.object(mig, "_get_json", return_value=sheet)
    dele = mocker.patch.object(mig, "_delete_json", return_value={})
    assert mig._drop_started_ended(9, "Job — Hours Log", commit=True) == 2
    deleted = [c.args[0] for c in dele.call_args_list]
    assert "/sheets/9/columns/71" in deleted and "/sheets/9/columns/72" in deleted


def test_drop_skips_when_absent(mocker):
    sheet = {"name": "Job — Hours Log", "columns": [{"title": "Task", "type": "TEXT_NUMBER", "id": 73}]}
    mocker.patch.object(mig, "_get_json", return_value=sheet)
    dele = mocker.patch.object(mig, "_delete_json")
    assert mig._drop_started_ended(9, "Job — Hours Log", commit=True) == 0
    dele.assert_not_called()


def test_drop_preview_does_not_delete(mocker):
    sheet = {"name": "Job — Hours Log", "columns": [{"title": "Started", "type": "TEXT_NUMBER", "id": 71}]}
    mocker.patch.object(mig, "_get_json", return_value=sheet)
    dele = mocker.patch.object(mig, "_delete_json")
    assert mig._drop_started_ended(9, "Job — Hours Log", commit=False) == 1
    dele.assert_not_called()
