"""Unit tests for scripts/migrations/reclone_projects_from_1111b.py.

Mock-Box-client tests for the cutover flow (archive + clone + verify).
Mirrors the convention from tests/test_box_build_1111b.py (sys.path-driven
import to avoid the mypy duplicate-module error).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

# sys.path-driven import (matches tests/test_box_build_1111b.py + test_watchdog.py).
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import reclone_projects_from_1111b as reclone_mod  # noqa: E402
from reclone_projects_from_1111b import (  # noqa: E402
    EXPECTED_DESCENDANT_COUNT,
    LEGACY_ARCHIVE_FOLDER_NAME,
    LEGACY_SUFFIX,
    PARENT_FOLDER_ID,
    PROJECT_SLUG_TO_NAME,
    SOURCE_1111B_ID,
    archive_one_legacy,
    ensure_legacy_archive_folder,
    reclone_one_project,
    verify_only_one_project,
)

from shared.defaults import BOX_PROJECT_FOLDERS  # noqa: E402 — after sys.path insertion above

# ---- Mock Box client backed by an in-memory tree -----------------------


def _make_box_client(
    tree: dict[str, list[tuple[str, str, str]]],
    *,
    descendant_count_override: dict[str, int] | None = None,
) -> Any:
    """Build a MagicMock Box client backed by an in-memory tree.

    `tree`: {folder_id: [(child_id, child_name, child_type), ...]}.
    `descendant_count_override`: optional {folder_id: count} to override
    the natural descendant count. Useful for tests that need a folder to
    report exactly EXPECTED_DESCENDANT_COUNT without populating all 267
    children manually.
    """
    state: dict[str, list[tuple[str, str, str]]] = {
        k: list(v) for k, v in tree.items()
    }
    counts = dict(descendant_count_override or {})

    def get_items(folder_id: str, **_kwargs: Any) -> list[Any]:
        return [
            SimpleNamespace(id=cid, name=cname, type=ctype)
            for (cid, cname, ctype) in state.get(folder_id, [])
        ]

    def move_folder(folder_id: str, new_parent_id: str, new_name: str | None) -> None:
        # Find and remove from old parent
        for parent_id, children in state.items():
            new_children = [c for c in children if c[0] != folder_id]
            if len(new_children) != len(children):
                state[parent_id] = new_children
                break
        # Add to new parent with optional rename. Default name comes from
        # the original (we don't track it; assume new_name is provided).
        state.setdefault(new_parent_id, [])
        state[new_parent_id].append((folder_id, new_name or folder_id, "folder"))

    def copy_folder(source_id: str, new_parent_id: str, name: str) -> Any:
        new_id = f"clone-of-{source_id}-as-{name}"
        # Just register the new folder with a synthetic structure. The
        # tests that use this provide their own descendant_count_override
        # via the closure or by repopulating state after.
        state.setdefault(new_parent_id, []).append((new_id, name, "folder"))
        state[new_id] = []
        return SimpleNamespace(id=new_id)

    def create_subfolder(parent_id: str, name: str) -> Any:
        new_id = f"created-{name}"
        state.setdefault(parent_id, []).append((new_id, name, "folder"))
        state[new_id] = []
        return SimpleNamespace(id=new_id)

    def folder_proxy(folder_id: str) -> Any:
        proxy = MagicMock()
        proxy.get_items.side_effect = lambda **kwargs: get_items(folder_id, **kwargs)
        proxy.move.side_effect = lambda new_parent, name=None: move_folder(
            folder_id, new_parent._id, name
        )
        proxy.copy.side_effect = lambda parent_folder, name: copy_folder(
            folder_id, parent_folder._id, name
        )
        proxy.create_subfolder.side_effect = lambda name: create_subfolder(folder_id, name)
        proxy._id = folder_id
        return proxy

    client = MagicMock()
    client.folder.side_effect = folder_proxy
    client._state = state
    client._counts = counts
    return client


# ---- Module-level invariants -------------------------------------------


def test_project_slug_to_name_has_six_entries():
    """Single source of truth for the 6-project cutover."""
    assert len(PROJECT_SLUG_TO_NAME) == 6
    assert set(PROJECT_SLUG_TO_NAME.values()) == {
        "Bradley 1",
        "Bradley 2",
        "Brimfield 1",
        "Brimfield 2",
        "Huntley",
        "Rockford",
    }


def test_project_slugs_match_box_project_folders_keys():
    """Slugs map to BOX_PROJECT_FOLDERS keys correctly."""
    for _slug, name in PROJECT_SLUG_TO_NAME.items():
        assert name in BOX_PROJECT_FOLDERS, f"{name!r} missing from BOX_PROJECT_FOLDERS"


def test_box_project_folders_has_six_entries():
    """BOX_PROJECT_FOLDERS must have exactly the 6 active projects."""
    assert len(BOX_PROJECT_FOLDERS) == 6
    assert set(BOX_PROJECT_FOLDERS.keys()) == set(PROJECT_SLUG_TO_NAME.values())


def test_expected_descendant_count_matches_1111b():
    """267 = 1111A/1111B descendant count per PR #70 live verification."""
    assert EXPECTED_DESCENDANT_COUNT == 267


def test_legacy_suffix_format():
    """Sanity check the archived-folder naming convention."""
    assert LEGACY_SUFFIX == " (legacy 1111A)"
    assert LEGACY_ARCHIVE_FOLDER_NAME == "99. Legacy 1111A Clones"


# ---- ensure_legacy_archive_folder --------------------------------------


def test_ensure_legacy_archive_folder_creates_when_missing(mocker):
    """No existing archive folder → create it."""
    client = _make_box_client({PARENT_FOLDER_ID: []})
    result = ensure_legacy_archive_folder(client)
    assert result.startswith("created-")
    # Now the parent contains the archive folder
    items = [c[1] for c in client._state[PARENT_FOLDER_ID]]
    assert LEGACY_ARCHIVE_FOLDER_NAME in items


def test_ensure_legacy_archive_folder_returns_existing_id(mocker):
    """Pre-existing archive folder → return its ID without creating."""
    client = _make_box_client(
        {PARENT_FOLDER_ID: [("archive-id", LEGACY_ARCHIVE_FOLDER_NAME, "folder")]}
    )
    result = ensure_legacy_archive_folder(client)
    assert result == "archive-id"


def test_ensure_legacy_archive_folder_dry_run_returns_sentinel():
    client = _make_box_client({PARENT_FOLDER_ID: []})
    result = ensure_legacy_archive_folder(client, dry_run=True)
    assert result == "(dry-run)"
    # State unchanged
    assert client._state[PARENT_FOLDER_ID] == []


# ---- archive_one_legacy ------------------------------------------------


def test_archive_one_legacy_moves_and_renames(mocker):
    """Legacy folder is moved to archive + renamed with (legacy 1111A) suffix."""
    legacy_id = BOX_PROJECT_FOLDERS["Bradley 1"]
    archive_id = "archive-id"
    client = _make_box_client(
        {
            PARENT_FOLDER_ID: [
                (legacy_id, "Bradley 1", "folder"),
                (archive_id, LEGACY_ARCHIVE_FOLDER_NAME, "folder"),
            ],
            archive_id: [],
            legacy_id: [],
        }
    )
    status, archived_id = archive_one_legacy(
        client, slug="bradley_1", archive_folder_id=archive_id
    )
    assert status == "archived"
    assert archived_id == legacy_id
    # Confirm move: legacy folder is no longer in ITS DATA root
    parent_names = [c[1] for c in client._state[PARENT_FOLDER_ID]]
    assert "Bradley 1" not in parent_names
    # Confirm legacy folder is now in archive with new name
    archive_contents = [c[1] for c in client._state[archive_id]]
    assert f"Bradley 1{LEGACY_SUFFIX}" in archive_contents


def test_archive_one_legacy_already_archived_skips(mocker):
    """When the archived name already exists in the archive folder, skip."""
    archive_id = "archive-id"
    archived_id = "previously-archived-id"
    client = _make_box_client(
        {
            PARENT_FOLDER_ID: [(archive_id, LEGACY_ARCHIVE_FOLDER_NAME, "folder")],
            archive_id: [(archived_id, f"Bradley 1{LEGACY_SUFFIX}", "folder")],
        }
    )
    status, returned_id = archive_one_legacy(
        client, slug="bradley_1", archive_folder_id=archive_id
    )
    assert status == "already_archived"
    assert returned_id == archived_id


def test_archive_one_legacy_missing_legacy_warns(mocker):
    """When legacy folder no longer exists under ITS DATA, log + skip."""
    archive_id = "archive-id"
    client = _make_box_client(
        {
            PARENT_FOLDER_ID: [(archive_id, LEGACY_ARCHIVE_FOLDER_NAME, "folder")],
            archive_id: [],
        }
    )
    status, returned_id = archive_one_legacy(
        client, slug="bradley_1", archive_folder_id=archive_id
    )
    assert status == "legacy_missing"
    assert returned_id is None


def test_archive_one_legacy_dry_run_does_not_mutate():
    legacy_id = BOX_PROJECT_FOLDERS["Bradley 1"]
    client = _make_box_client(
        {
            PARENT_FOLDER_ID: [(legacy_id, "Bradley 1", "folder")],
            legacy_id: [],
        }
    )
    initial_state = dict(client._state)
    archive_one_legacy(
        client, slug="bradley_1", archive_folder_id="(dry-run)", dry_run=True
    )
    # State unchanged
    assert client._state == initial_state


# ---- reclone_one_project -----------------------------------------------


def test_reclone_one_project_clones_when_target_missing(mocker):
    """Canonical name not present under ITS DATA → fresh clone."""
    client = _make_box_client(
        {
            PARENT_FOLDER_ID: [],
            SOURCE_1111B_ID: [],
        }
    )
    # Mock the expensive bits: wait_for_deep_copy_complete + _count_all_descendants
    mocker.patch.object(
        reclone_mod, "wait_for_deep_copy_complete", return_value=(True, 14)
    )
    mocker.patch.object(
        reclone_mod, "_count_all_descendants", return_value=EXPECTED_DESCENDANT_COUNT
    )
    mocker.patch("time.sleep", return_value=None)
    status, new_id = reclone_one_project(client, slug="bradley_1")
    assert status == "cloned"
    assert new_id and new_id.startswith("clone-of-")


def test_reclone_one_project_existing_matches_skips(mocker):
    """Target folder already exists with correct descendant count → skip clone."""
    existing_id = "existing-bradley-id"
    client = _make_box_client(
        {
            PARENT_FOLDER_ID: [(existing_id, "Bradley 1", "folder")],
        }
    )
    mocker.patch.object(
        reclone_mod, "_count_all_descendants", return_value=EXPECTED_DESCENDANT_COUNT
    )
    status, returned_id = reclone_one_project(client, slug="bradley_1")
    assert status == "existing_matches"
    assert returned_id == existing_id


def test_reclone_one_project_existing_mismatched_refuses(mocker):
    """Target folder exists with WRONG descendant count → refuse, do not overwrite."""
    existing_id = "existing-bradley-legacy-id"
    client = _make_box_client(
        {
            PARENT_FOLDER_ID: [(existing_id, "Bradley 1", "folder")],
        }
    )
    mocker.patch.object(reclone_mod, "_count_all_descendants", return_value=100)
    status, returned_id = reclone_one_project(client, slug="bradley_1")
    assert status == "existing_mismatched"
    assert returned_id == existing_id


def test_reclone_one_project_dry_run_does_not_mutate(mocker):
    client = _make_box_client({PARENT_FOLDER_ID: []})
    initial_state = dict(client._state)
    reclone_one_project(client, slug="bradley_1", dry_run=True)
    assert client._state == initial_state


# ---- verify_only_one_project -------------------------------------------


def test_verify_only_folder_missing(mocker, tmp_path):
    """Project folder doesn't exist under ITS DATA → report error_missing."""
    client = _make_box_client({PARENT_FOLDER_ID: []})
    # Redirect report path to tmp to avoid touching ~/its/logs
    mocker.patch.object(reclone_mod, "COMPLIANCE_REPORT_PATH_FMT", tmp_path / "r_{slug}.txt")
    result = verify_only_one_project(client, "bradley_1")
    assert result["verify_passed"] is False
    assert result["error"] == "folder_missing"


# ---- Helpers: verify_clone passing path --------------------------------


def test_verify_clone_passes_when_blueprint_matches(mocker, tmp_path):
    """If the new clone has correct descendant count AND every RENAME_MAP
    target name is present at its expected path, OVERALL PASS.

    This is a lighter-weight test than building the full 131-target tree —
    we mock _count_all_descendants + _resolve_path + _find_child so the
    verification logic gets satisfied inputs.
    """
    from reclone_projects_from_1111b import verify_clone

    client = _make_box_client({})

    # Mock _count_all_descendants to return exactly the expected count
    mocker.patch.object(
        reclone_mod, "_count_all_descendants", return_value=EXPECTED_DESCENDANT_COUNT
    )
    # Mock _resolve_path to always return a non-None id (parent always resolvable)
    mocker.patch.object(
        reclone_mod, "_resolve_path", return_value="some-parent-id"
    )
    # Mock _find_child to always return a non-None id (target always present)
    mocker.patch.object(
        reclone_mod, "_find_child", return_value="some-child-id"
    )

    passed, report = verify_clone(client, "test-root-id", "Bradley 1")
    assert passed is True
    assert "OVERALL: PASS" in report


def test_verify_clone_fails_on_wrong_descendant_count(mocker):
    """If descendant count differs from 267, OVERALL FAIL."""
    from reclone_projects_from_1111b import verify_clone

    client = _make_box_client({})
    mocker.patch.object(reclone_mod, "_count_all_descendants", return_value=200)
    mocker.patch.object(reclone_mod, "_resolve_path", return_value="some-parent-id")
    mocker.patch.object(reclone_mod, "_find_child", return_value="some-child-id")

    passed, report = verify_clone(client, "test-root-id", "Bradley 1")
    assert passed is False
    assert "OVERALL: FAIL" in report
