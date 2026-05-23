"""Unit tests for scripts/migrations/box_build_1111b_blueprint.py.

Mock-Box-client tests for the rename traversal + idempotency + error
handling. RENAME_MAP integrity checks. Live Box API is exercised by
tests/test_box_build_1111b_integration.py (gated `pytest -m integration`).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from boxsdk.exception import BoxAPIException  # type: ignore[import-untyped]

# Insert scripts/migrations/ into sys.path so the migration module is
# importable as a bare name — matches the convention in tests/test_watchdog.py
# and avoids the mypy "Source file found twice under different module names"
# error that triggers when the same file is reachable via both the bare
# path and the dotted scripts.migrations.* path.
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import box_build_1111b_blueprint as build_mod  # noqa: E402 — sys.path-driven import
from box_build_1111b_blueprint import (  # noqa: E402
    PARENT_FOLDER_ID,
    RENAME_MAP,
    TARGET_1111B_NAME,
    _resolve_path,
    apply_renames,
    ensure_1111b_clone,
)

# ---- Mock Box client builder --------------------------------------------


def _make_box_client(tree: dict[str, list[tuple[str, str, str]]]) -> Any:
    """Build a MagicMock Box client backed by an in-memory folder tree.

    `tree` shape: {folder_id: [(child_id, child_name, child_type), ...]}.
    All folder lookups + renames + copies operate on the in-memory state
    so test assertions can read it back. The mock intentionally doesn't
    implement full Box SDK semantics — only the methods the migration
    script invokes.
    """
    # Make a copy we mutate so test setup stays clean.
    state: dict[str, list[tuple[str, str, str]]] = {
        k: list(v) for k, v in tree.items()
    }

    def get_items(folder_id: str, **_kwargs: Any) -> list[Any]:
        return [
            SimpleNamespace(id=cid, name=cname, type=ctype)
            for (cid, cname, ctype) in state.get(folder_id, [])
        ]

    def rename_folder(folder_id: str, new_name: str) -> None:
        # Walk every parent's children to find this folder_id and replace its name.
        for parent_id, children in state.items():
            for i, (cid, _cname, ctype) in enumerate(children):
                if cid == folder_id:
                    state[parent_id][i] = (cid, new_name, ctype)
                    return

    def copy_folder(source_id: str, parent_id: str, name: str) -> Any:
        new_id = f"mock-clone-{name}-{len(state)}"
        # Deep-copy children recursively.
        def _deep_copy(src: str, dst: str) -> None:
            state[dst] = []
            for cid, cname, ctype in state.get(src, []):
                if ctype == "folder":
                    new_cid = f"{dst}-c{len(state[dst])}"
                    state[dst].append((new_cid, cname, "folder"))
                    _deep_copy(cid, new_cid)
                else:
                    state[dst].append((cid, cname, ctype))
        _deep_copy(source_id, new_id)
        # Add to parent's children list.
        state.setdefault(parent_id, [])
        state[parent_id].append((new_id, name, "folder"))
        return SimpleNamespace(id=new_id)

    def folder_proxy(folder_id: str) -> Any:
        proxy = MagicMock()
        proxy.get_items.side_effect = lambda **kwargs: get_items(folder_id, **kwargs)
        proxy.rename.side_effect = lambda name: rename_folder(folder_id, name)
        # copy expects (parent_folder, name=...) kwarg shape
        def _copy(parent_folder: Any, name: str) -> Any:
            return copy_folder(folder_id, parent_folder._id, name)
        proxy.copy.side_effect = _copy
        proxy._id = folder_id
        return proxy

    client = MagicMock()
    client.folder.side_effect = folder_proxy
    client._state = state  # expose for test assertions
    return client


# ---- RENAME_MAP integrity ----------------------------------------------


def test_rename_map_no_target_collisions():
    """Two source names in the same parent must not map to the same target name."""
    seen: dict[tuple[str, str], str] = {}
    for (parent_path, source_name), target_name in RENAME_MAP.items():
        key = (parent_path, target_name)
        if key in seen:
            pytest.fail(
                f"Collision at {key}: both {source_name!r} and {seen[key]!r} "
                f"target the same name"
            )
        seen[key] = source_name


def test_rename_map_paths_resolvable_via_target_chain():
    """Every parent path segment must be reachable as a target name from a shallower
    map entry, OR be one of the two unchanged top-level folder names.

    Validates that top-down traversal will be able to walk to each entry's parent.
    """
    unchanged_top_level = {
        "(Project # & Name) Field",
        "(Project # & Name) Office",
    }
    target_paths: set[str] = set()
    target_paths.add("")  # root is always reachable

    for (parent_path, _src), target_name in RENAME_MAP.items():
        # Verify each segment of parent_path is reachable
        if parent_path:
            segments = parent_path.split("/")
            for i in range(len(segments)):
                segment_name = segments[i]
                # Either this segment was a target of a prior rename (insertion order),
                # OR it's an unchanged top-level folder.
                parent_of_segment = "/".join(segments[:i])
                # The segment is reachable if (parent_of_segment, X→segment_name) is
                # in the map ahead of us, OR segment_name is unchanged-top-level.
                if (
                    segment_name in unchanged_top_level
                    or (
                        any(
                            t == segment_name
                            for (p, _s), t in RENAME_MAP.items()
                            if p == parent_of_segment
                        )
                    )
                ):
                    continue
                # Otherwise: a path segment is unreachable.
                pytest.fail(
                    f"Path segment {segment_name!r} (in {parent_path!r}) is not "
                    f"reachable as a rename target or unchanged top-level — "
                    f"top-down traversal will fail for this entry"
                )
        # Track this entry's full target path for sanity
        full_target_path = (
            f"{parent_path}/{target_name}" if parent_path else target_name
        )
        target_paths.add(full_target_path)


def test_rename_map_entry_count_matches_brief():
    """Brief listed 131 entries; assert the map's actual entry count."""
    assert len(RENAME_MAP) == 131


def test_rename_map_includes_known_typo_fixes():
    """Three known typo fixes must be present (from PR #67 session log)."""
    targets = list(RENAME_MAP.values())
    # Coorespondance → Correspondence
    assert "06. Correspondence" in targets
    assert "05. Owner Correspondence" in targets
    # Structual → Structural
    assert "04. Approved Structural Calculations" in targets


def test_rename_map_includes_known_apostrophe_fixes():
    """Possessive-S on acronym plurals retired."""
    targets = list(RENAME_MAP.values())
    assert "04. JSAs" in targets
    assert "01. DFRs" in targets
    assert "02. WPRs" in targets
    assert "01. PPAs & IAs" in targets
    assert "08. PTOs" in targets
    assert "09. CODs" in targets


def test_rename_map_includes_portfolio_prefix_additions():
    """Portfolio prefix applied uniformly to all 12 top-level Portfolio folders."""
    targets = list(RENAME_MAP.values())
    assert "05. Portfolio Engineering Gen" in targets
    assert "09. Portfolio Utility Documents Tracking" in targets
    assert "10. Portfolio Submittal Logs" in targets
    assert "11. Portfolio De-Comm Bonds" in targets


# ---- _resolve_path ------------------------------------------------------


def test_resolve_path_empty_returns_root():
    client = _make_box_client({"root": []})
    assert _resolve_path(client, "root", "") == "root"


def test_resolve_path_single_segment():
    client = _make_box_client(
        {
            "root": [("c1", "Field", "folder")],
            "c1": [],
        }
    )
    assert _resolve_path(client, "root", "Field") == "c1"


def test_resolve_path_multi_segment():
    client = _make_box_client(
        {
            "root": [("c1", "Field", "folder")],
            "c1": [("c2", "01. Onsite", "folder")],
            "c2": [("c3", "01. Safety", "folder")],
            "c3": [],
        }
    )
    assert _resolve_path(client, "root", "Field/01. Onsite/01. Safety") == "c3"


def test_resolve_path_returns_none_on_missing_segment():
    client = _make_box_client({"root": [], "c1": []})
    assert _resolve_path(client, "root", "Field/Missing") is None


# ---- ensure_1111b_clone -------------------------------------------------


def test_ensure_clone_skips_when_already_present():
    client = _make_box_client(
        {
            PARENT_FOLDER_ID: [("existing-id", TARGET_1111B_NAME, "folder")],
        }
    )
    result = ensure_1111b_clone(client)
    assert result == "existing-id"
    # No copy was attempted
    client.folder.assert_called_with(PARENT_FOLDER_ID)


def test_ensure_clone_dry_run_returns_sentinel():
    client = _make_box_client({PARENT_FOLDER_ID: []})
    result = ensure_1111b_clone(client, dry_run=True)
    assert result == "(dry-run)"


# ---- apply_renames ------------------------------------------------------


def test_apply_renames_handles_already_renamed_target():
    """If target name is already present, skip silently (idempotent re-run)."""
    # Build a tree where the source name doesn't exist but target does.
    client = _make_box_client(
        {
            "1111B": [("c1", "01. Portfolio Client Docs", "folder")],
            "c1": [],
        }
    )
    counters = apply_renames(client, "1111B")
    # The top-level entry "1. Portfolio Client Docs" → "01. Portfolio Client Docs"
    # is now in "already_renamed" since target name is present.
    assert counters["already_renamed"] >= 1


def test_apply_renames_no_op_same_name_increments_counter():
    """Source-equals-target entries skip without a Box API call."""
    # 4 known no-op entries in the brief's map — counted in no_op_same_name.
    client = _make_box_client({"1111B": []})
    counters = apply_renames(client, "1111B")
    # The 4 no-op entries are detected before any path resolution
    assert counters["no_op_same_name"] == 4


def test_apply_renames_dry_run_does_not_mutate():
    """dry_run=True logs intent but never calls Box rename."""
    client = _make_box_client(
        {
            "1111B": [("c1", "1. Portfolio Client Docs", "folder")],
            "c1": [],
        }
    )
    initial = client._state["1111B"][0][1]
    apply_renames(client, "1111B", dry_run=True)
    # State unchanged
    assert client._state["1111B"][0][1] == initial


def test_apply_renames_idempotent_re_run():
    """A second invocation against a fully-renamed tree must be a no-op."""
    # Build a tree where ALL targets are already present.
    client = _make_box_client(
        {
            "1111B": [
                ("c1", "01. Portfolio Client Docs", "folder"),
            ],
            "c1": [],
        }
    )
    counters_first = apply_renames(client, "1111B")
    counters_second = apply_renames(client, "1111B")
    # Second run should match first run (idempotent)
    assert counters_second["renamed"] == counters_first["renamed"]


def test_apply_renames_source_missing_logs_warn():
    """When neither source nor target is found, count as source_missing."""
    client = _make_box_client(
        {
            "1111B": [("c1", "UnexpectedFolder", "folder")],
            "c1": [],
        }
    )
    counters = apply_renames(client, "1111B")
    # All top-level entries should be source_missing (none of their sources are there)
    # except no_op entries which short-circuit before lookup
    assert counters["source_missing"] > 0


def test_apply_renames_actually_renames():
    """Single top-level entry: source present → renamed to target."""
    client = _make_box_client(
        {
            "1111B": [("c1", "1. Portfolio Client Docs", "folder")],
            "c1": [],
        }
    )
    apply_renames(client, "1111B")
    # After rename, the child should have the new name
    assert client._state["1111B"][0][1] == "01. Portfolio Client Docs"


# ---- transient 404 retry on rename -------------------------------------


def test_rename_retries_once_on_transient_404():
    """A 404 on first rename attempt triggers single-shot retry."""
    _rename_folder = build_mod._rename_folder

    call_count = {"n": 0}

    def _rename_side_effect(name: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise BoxAPIException(
                status=404,
                code="not_found",
                message="Folder not found",
                request_id="req-abc",
                headers={},
                url="https://api.box.com/2.0/folders/123",
                method="PUT",
                context_info={},
            )
        # second call: success

    folder_mock = MagicMock()
    folder_mock.rename.side_effect = _rename_side_effect
    client_mock = MagicMock()
    client_mock.folder.return_value = folder_mock

    _rename_folder(client_mock, "123", "new_name")
    assert call_count["n"] == 2


def test_rename_does_not_retry_on_non_404():
    """A non-404 BoxAPIException should propagate without retry."""
    _rename_folder = build_mod._rename_folder

    folder_mock = MagicMock()
    folder_mock.rename.side_effect = BoxAPIException(
        status=403,
        code="access_denied",
        message="Forbidden",
        request_id="req-abc",
        headers={},
        url="https://api.box.com/2.0/folders/123",
        method="PUT",
        context_info={},
    )
    client_mock = MagicMock()
    client_mock.folder.return_value = folder_mock

    with pytest.raises(BoxAPIException) as exc_info:
        _rename_folder(client_mock, "123", "new_name")
    assert exc_info.value.status == 403
