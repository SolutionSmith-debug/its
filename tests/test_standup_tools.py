"""Tests for the wipe/stand-up tool family (wipe_tenant / sheet_ids_regen /
build_legacy_workspaces / standup).

Prove-the-control-bites discipline: every fail-closed guard is exercised with a
synthetic violation — allowlist drift, loaded daemons, refused phrase, duplicate
names, a corrupted em dash — and the test asserts the REFUSAL (and that no delete
was reachable), not just a green path.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

# sys.path-driven import (scripts/ has no __init__.py) — mirrors tests/test_gap_builders.py.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import build_legacy_workspaces as legacy  # noqa: E402
import sheet_ids_regen as regen  # noqa: E402
import standup  # noqa: E402
import wipe_tenant as wipe  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---- wipe_tenant: allowlist double-match (guard 1) ------------------------


def test_allowlist_exact_match_is_deletable() -> None:
    live = [{"name": "ITS — System", "id": 680592632244100}]
    deletable, mismatches, unlisted = wipe.match_allowlist(
        live, wipe.SMARTSHEET_WORKSPACE_ALLOWLIST)
    assert [d["id"] for d in deletable] == [680592632244100]
    assert not mismatches and not unlisted


def test_allowlist_name_id_drift_refuses() -> None:
    # Same name, DIFFERENT id — the drifted-tenant case must land in mismatches.
    live = [{"name": "ITS — System", "id": 999}]
    deletable, mismatches, _ = wipe.match_allowlist(
        live, wipe.SMARTSHEET_WORKSPACE_ALLOWLIST)
    assert not deletable
    assert len(mismatches) == 1


def test_allowlist_id_reused_under_new_name_refuses() -> None:
    live = [{"name": "Totally Different", "id": 680592632244100}]
    deletable, mismatches, _ = wipe.match_allowlist(
        live, wipe.SMARTSHEET_WORKSPACE_ALLOWLIST)
    assert not deletable
    assert len(mismatches) == 1


def test_allowlist_unlisted_never_deletable() -> None:
    live = [{"name": "Customer Production Workspace", "id": 123456}]
    deletable, mismatches, unlisted = wipe.match_allowlist(
        live, wipe.SMARTSHEET_WORKSPACE_ALLOWLIST)
    assert not deletable and not mismatches
    assert unlisted == ["'Customer Production Workspace' (id=123456)"]


def test_wipe_main_refuses_on_mismatch_even_with_phrase(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard 1 bites in main(): drifted pins abort BEFORE the phrase/dump/delete."""
    monkeypatch.setattr(wipe, "require_daemons_down", lambda: None)
    monkeypatch.setattr(wipe, "_list_workspaces",
                        lambda: [{"name": "ITS — System", "id": 999}])
    monkeypatch.setattr(wipe, "_box_root_items", lambda: [])
    monkeypatch.setattr(wipe, "_confirm_phrase", lambda: True)

    def _boom(*a: Any, **k: Any) -> None:
        raise AssertionError("delete reached despite allowlist mismatch")

    monkeypatch.setattr(wipe, "_delete_workspace", _boom)
    monkeypatch.setattr(wipe, "_delete_box_folder", _boom)
    monkeypatch.setattr(sys, "argv", ["wipe_tenant.py", "--commit"])
    assert wipe.main() == 1


def test_wipe_main_refuses_without_phrase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard 3 bites: a declined phrase deletes nothing."""
    monkeypatch.setattr(wipe, "require_daemons_down", lambda: None)
    monkeypatch.setattr(wipe, "_list_workspaces",
                        lambda: [{"name": "ITS — System", "id": 680592632244100}])
    monkeypatch.setattr(wipe, "_box_root_items", lambda: [])
    monkeypatch.setattr(wipe, "_confirm_phrase", lambda: False)

    def _boom(*a: Any, **k: Any) -> None:
        raise AssertionError("delete reached despite refused phrase")

    monkeypatch.setattr(wipe, "_delete_workspace", _boom)
    monkeypatch.setattr(wipe, "_delete_box_folder", _boom)
    monkeypatch.setattr(wipe, "dump_workspace", _boom)
    monkeypatch.setattr(sys, "argv", ["wipe_tenant.py", "--commit"])
    assert wipe.main() == 1


def test_wipe_refuses_while_daemons_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard 2 bites: loaded fleet -> WipeRefusedError before anything else."""
    monkeypatch.setattr(wipe, "_loaded_its_daemons",
                        lambda: ["org.solutionsmith.its.portal-poll"])
    with pytest.raises(wipe.WipeRefusedError):
        wipe.require_daemons_down()
    monkeypatch.setattr(wipe, "_loaded_its_daemons", lambda: [])
    wipe.require_daemons_down()  # empty fleet passes


def test_wipe_plan_mode_never_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wipe, "require_daemons_down", lambda: None)
    monkeypatch.setattr(wipe, "_list_workspaces",
                        lambda: [{"name": "ITS — System", "id": 680592632244100}])
    monkeypatch.setattr(wipe, "_box_root_items", lambda: [])

    def _boom(*a: Any, **k: Any) -> None:
        raise AssertionError("plan mode reached a destructive call")

    monkeypatch.setattr(wipe, "_delete_workspace", _boom)
    monkeypatch.setattr(wipe, "_delete_box_folder", _boom)
    monkeypatch.setattr(wipe, "_confirm_phrase", _boom)
    monkeypatch.setattr(wipe, "dump_workspace", _boom)
    monkeypatch.setattr(sys, "argv", ["wipe_tenant.py"])
    assert wipe.main() == 0


# ---- sheet_ids_regen: resolution ------------------------------------------

_TREE: dict[str, Any] = {
    "id": 100,
    "name": "ITS — System",
    "folders": [
        {"id": 200, "name": "01 — Config",
         "folders": [],
         "sheets": [{"id": 300, "name": "ITS_Config"}]},
        {"id": 201, "name": "02 — Logs",
         "folders": [],
         "sheets": [{"id": 301, "name": "ITS_Errors"},
                    {"id": 302, "name": "ITS_Errors"}]},  # duplicate leaf
    ],
    "sheets": [],
}


def test_resolve_workspace_folder_sheet() -> None:
    assert regen.resolve_in_tree(_TREE, regen.Target("ITS — System")) == 100
    assert regen.resolve_in_tree(
        _TREE, regen.Target("ITS — System", ("01 — Config",))) == 200
    assert regen.resolve_in_tree(
        _TREE, regen.Target("ITS — System", ("01 — Config",), "ITS_Config")) == 300


def test_resolve_duplicate_leaf_is_ambiguous_not_first_match() -> None:
    res = regen.resolve_in_tree(
        _TREE, regen.Target("ITS — System", ("02 — Logs",), "ITS_Errors"))
    assert res == regen.AMBIGUOUS


def test_resolve_missing_required_vs_optional() -> None:
    required = regen.Target("ITS — System", ("01 — Config",), "Nope")
    optional = regen.Target("ITS — System", ("01 — Config",), "Nope", optional=True)
    assert regen.resolve_in_tree(_TREE, required) == regen.MISSING
    assert regen.resolve_in_tree(_TREE, optional) == regen.ABSENT_OPTIONAL


def test_resolve_all_duplicate_workspace_names_ambiguous() -> None:
    workspaces = [{"name": "ITS — System", "id": 100},
                  {"name": "ITS — System", "id": 101}]
    out = regen.resolve_all({"WORKSPACE_SYSTEM": regen.Target("ITS — System")},
                            workspaces, {"ITS — System": _TREE})
    assert out["WORKSPACE_SYSTEM"] == regen.AMBIGUOUS


# ---- sheet_ids_regen: rewrite mechanics -----------------------------------

_FIXTURE = """# header comment
WORKSPACE_SYSTEM       = 111   # ITS — System (operator-only)
SHEET_CONFIG              = 222  # ITS_Config
ALIAS = WORKSPACE_SYSTEM  # no digits — untouched
DAEMON_HEALTH_COLUMNS: dict[str, int] = {
    "daemon_name":                  333,
    "workstream":                  444,
}
"""


def test_rewrite_constants_preserves_structure() -> None:
    text, changed = regen.rewrite_constants(
        _FIXTURE, {"WORKSPACE_SYSTEM": 911, "SHEET_CONFIG": 222})
    assert "WORKSPACE_SYSTEM       = 911   # ITS — System (operator-only)" in text
    assert "SHEET_CONFIG              = 222  # ITS_Config" in text  # unchanged value
    assert "ALIAS = WORKSPACE_SYSTEM" in text
    assert changed == ["WORKSPACE_SYSTEM"]


def test_rewrite_dict_values() -> None:
    text, changed = regen.rewrite_dict_values(_FIXTURE, {"daemon_name": 999})
    assert '"daemon_name":                  999,' in text
    assert '"workstream":                  444,' in text
    assert changed == ["daemon_name"]


def test_integer_remap_word_boundaries() -> None:
    text = "a = 123\nb = 51234\nc = 1123\nnode(sheet_id=123)\n"
    out, _n = regen.rewrite_integer_remap(text, {123: 777})
    assert "a = 777" in out
    assert "b = 51234" in out  # 123 inside a longer number untouched
    assert "c = 1123" in out
    assert "sheet_id=777" in out


def test_integer_remap_chained_pairs_never_double_replace() -> None:
    # One pair's NEW id equals another pair's OLD id: two-phase must keep them
    # independent — sequential replacement would turn the first 1 into 3.
    out, n = regen.rewrite_integer_remap("x = 1\ny = 2\n", {1: 2, 2: 3})
    assert "x = 2" in out and "y = 3" in out
    assert n == 2


def test_read_current_values() -> None:
    vals = regen.read_current_values(_FIXTURE, ["WORKSPACE_SYSTEM", "SHEET_CONFIG",
                                                "ALIAS", "MISSING_CONST"])
    assert vals == {"WORKSPACE_SYSTEM": 111, "SHEET_CONFIG": 222}


# ---- sheet_ids_regen: registry parity teeth -------------------------------


def test_registry_covers_every_sheet_ids_constant() -> None:
    """A new WORKSPACE_/FOLDER_/SHEET_ constant in shared/sheet_ids.py MUST get a
    REGISTRY entry in the same PR — otherwise the auto-FLIP silently skips it and
    the constant goes stale on the next rebuild. This test is the teeth."""
    text = (REPO_ROOT / "shared" / "sheet_ids.py").read_text(encoding="utf-8")
    declared = set(re.findall(
        r"^((?:WORKSPACE|FOLDER|SHEET)_[A-Z0-9_]+)\s*=\s*\d+", text, re.MULTILINE))
    missing = declared - set(regen.REGISTRY)
    assert not missing, (
        f"sheet_ids.py constants missing a sheet_ids_regen.REGISTRY entry: "
        f"{sorted(missing)}")


def test_registry_daemon_health_keys_match_sheet_ids_dict() -> None:
    text = (REPO_ROOT / "shared" / "sheet_ids.py").read_text(encoding="utf-8")
    dict_body = text.split("DAEMON_HEALTH_COLUMNS", 1)[1].split("}", 1)[0]
    keys = set(re.findall(r'"([a-z_]+)":\s*\d+', dict_body))
    assert keys == set(regen.DAEMON_HEALTH_TITLE_BY_KEY)


# ---- build_legacy_workspaces ----------------------------------------------


def test_legacy_dash_assertion_bites(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silently-normalized dash (em -> en) must fail CLOSED at the assert."""
    bad = (legacy.WorkspaceSpec("ITS – Human Review", ()),)  # en dash
    monkeypatch.setattr(legacy, "WORKSPACES", bad)
    with pytest.raises(ValueError, match="canonical_name_dash_corrupted"):
        legacy._assert_canonical_dashes()


def test_legacy_schemas_match_live_capture_shape() -> None:
    """Column counts + single-primary invariant, pinned to the 2026-07-22 capture."""
    expected = {
        "WPR_PENDING_REVIEW_COLUMNS": 12,
        "TIME_OFF_COLUMNS": 6,
        "SUBCONTRACTOR_DB_COLUMNS": 11,
        "VENDOR_DB_COLUMNS": 9,
        "EQUIPMENT_MASTER_COLUMNS": 8,
        "TEMPLATE_DAILY_COLUMNS": 9,
        "TEMPLATE_ROLLUP_COLUMNS": 4,
    }
    for attr, count in expected.items():
        columns = getattr(legacy, attr)
        assert len(columns) == count, attr
        assert sum(1 for c in columns if c.get("primary")) == 1, attr


def test_legacy_not_owner_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        legacy, "_find_workspaces",
        lambda name: [{"id": 1, "name": name, "accessLevel": "ADMIN",
                       "permalink": "https://x"}])
    runner = legacy.Runner(dry_run=False)
    with pytest.raises(legacy.BuildRefusedError):
        runner.ensure_workspace(legacy.WORKSPACES[0])


def test_legacy_duplicate_workspace_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        legacy, "_find_workspaces",
        lambda name: [{"id": 1, "name": name, "accessLevel": "OWNER"},
                      {"id": 2, "name": name, "accessLevel": "OWNER"}])
    runner = legacy.Runner(dry_run=False)
    with pytest.raises(legacy.BuildRefusedError):
        runner.ensure_workspace(legacy.WORKSPACES[0])


def test_legacy_demo_tree_contains_week_templates() -> None:
    demo = next(w for w in legacy.WORKSPACES
                if w.name == "Forefront Portfolio — ITS Demo")
    field_reports = next(f for f in demo.folders
                         if f.name == "03 — Field Reports (JHA/TBT)")
    bradley = next(f for f in field_reports.folders
                   if f.name == "Bradley 1 (BBCHS 1)")
    week = next(f for f in bradley.folders if f.name == "Week of 2026-03-09")
    names = {s.name for s in week.sheets}
    assert names == {"Daily Reports — Week of 2026-03-09",
                     "Weekly Rollup — Week of 2026-03-09"}


# ---- standup: stage-graph invariants --------------------------------------


def _stage_names(dump: Path | None) -> list[str]:
    return [n for n, _ in standup.build_stages(dump, skip_shares=False)]


def test_stage_names_unique_and_final_verify_last() -> None:
    names = _stage_names(Path("/tmp/x"))
    assert len(names) == len(set(names))
    assert names[-1] == "final-verify"


def test_flip_precedes_seed_ordering() -> None:
    names = _stage_names(Path("/tmp/x"))
    # SEED follows FLIP: the config baseline seed runs only after the System
    # sheets exist AND their ids are regenerated.
    assert names.index("seed-config-baseline") > names.index("regen-system-sheets")
    # Box-root config auto-paste needs ITS_Config to exist.
    assert names.index("box-roots") > names.index("seed-config-baseline")
    # Row restore happens before the seeders (seeders skip-existing against it).
    assert names.index("restore-rows") < names.index("seeders")
    # Every builder stage is followed by a regen before any dependent stage runs.
    assert names.index("regen-system") == names.index("system-workspace") + 1
    assert names.index("regen-po") == names.index("po-workspace") + 1


def test_fresh_tenant_mode_drops_restore_stages() -> None:
    names = _stage_names(None)
    assert "restore-rows" not in names
    assert "restore-shares" not in names
    assert names[-1] == "final-verify"


def test_skipped_seeders_stay_skipped() -> None:
    """seed_its_active_jobs / seed_its_project_routing / trusted-contacts must NOT
    be in the stage table (documented skips — dropdown pollution / dead Box ids /
    dormant lane). If someone adds them, this fails and forces the conversation."""
    import inspect
    source = inspect.getsource(standup.build_stages)
    for forbidden in ("seed_its_active_jobs", "seed_its_project_routing",
                      "seed_its_trusted_contacts", "build_its_trusted_contacts_sheet"):
        assert forbidden not in source, forbidden


def test_restore_sheet_targets_are_valid_constants() -> None:
    text = (REPO_ROOT / "shared" / "sheet_ids.py").read_text(encoding="utf-8")
    for _ws, _sheet, constant in standup.RESTORE_SHEETS:
        assert re.search(rf"^{constant}\s*=", text, re.MULTILINE), constant


def test_resolve_dump_dir_refusals(tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """No dump -> refuse (never a silent fresh-tenant fallback); two dumps ->
    refuse auto-pick (a partial re-wipe's second dump is incomplete)."""
    monkeypatch.setattr(standup, "DUMP_ROOT", tmp_path)
    with pytest.raises(standup.StageFailedError, match="no prewipe"):
        standup._resolve_dump_dir(None)
    (tmp_path / "prewipe_A").mkdir()
    assert standup._resolve_dump_dir(None) == tmp_path / "prewipe_A"
    (tmp_path / "prewipe_B").mkdir()
    with pytest.raises(standup.StageFailedError, match="auto-picking is unsafe"):
        standup._resolve_dump_dir(None)
    # explicit --dump always wins, but must exist
    assert standup._resolve_dump_dir(tmp_path / "prewipe_B") == tmp_path / "prewipe_B"
    with pytest.raises(standup.StageFailedError, match="does not exist"):
        standup._resolve_dump_dir(tmp_path / "prewipe_missing")
