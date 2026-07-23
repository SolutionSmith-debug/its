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
from typing import Any, cast

import pytest

# sys.path-driven import (scripts/ has no __init__.py) — mirrors tests/test_gap_builders.py.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import _rest_retry  # noqa: E402
import build_legacy_workspaces as legacy  # noqa: E402
import requests  # noqa: E402
import sheet_ids_regen as regen  # noqa: E402
import standup  # noqa: E402
import wipe_tenant as wipe  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---- wipe_tenant: allowlist double-match (guard 1) ------------------------


def test_allowlist_exact_match_is_deletable() -> None:
    # Fixture derived FROM the allowlist: the pins are historical (the wiped
    # tenant's ids, deliberately never remapped), so the test must never
    # hardcode an id that can drift from them.
    pin_name, pin_id = wipe.SMARTSHEET_WORKSPACE_ALLOWLIST[0]
    live = [{"name": pin_name, "id": pin_id}]
    deletable, mismatches, unlisted = wipe.match_allowlist(
        live, wipe.SMARTSHEET_WORKSPACE_ALLOWLIST)
    assert [d["id"] for d in deletable] == [pin_id]
    assert not mismatches and not unlisted


def test_allowlist_name_id_drift_refuses() -> None:
    # Same name, DIFFERENT id — the drifted-tenant case must land in mismatches.
    live = [{"name": wipe.SMARTSHEET_WORKSPACE_ALLOWLIST[0][0], "id": 999}]
    deletable, mismatches, _ = wipe.match_allowlist(
        live, wipe.SMARTSHEET_WORKSPACE_ALLOWLIST)
    assert not deletable
    assert len(mismatches) == 1


def test_allowlist_id_reused_under_new_name_refuses() -> None:
    live = [{"name": "Totally Different",
             "id": wipe.SMARTSHEET_WORKSPACE_ALLOWLIST[0][1]}]
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
                        lambda: [{"name": wipe.SMARTSHEET_WORKSPACE_ALLOWLIST[0][0],
                                  "id": 999}])
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
                        lambda: [dict(zip(("name", "id"),
                                          wipe.SMARTSHEET_WORKSPACE_ALLOWLIST[0],
                                          strict=True))])
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
    # plan mode calls _loaded_its_daemons directly (WARN-only branch) — stub it
    # rather than require_daemons_down, or Linux CI dies on a missing launchctl.
    monkeypatch.setattr(wipe, "_loaded_its_daemons", lambda: [])
    monkeypatch.setattr(wipe, "_list_workspaces",
                        lambda: [dict(zip(("name", "id"),
                                          wipe.SMARTSHEET_WORKSPACE_ALLOWLIST[0],
                                          strict=True))])
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


def test_missing_required_excludes_ambiguous_and_optional() -> None:
    """Only MISSING drives the propagation probe: AMBIGUOUS is a real conflict
    (retrying cannot fix a duplicate name) and absent-optional is by design."""
    res: dict[str, int | str] = {"A": regen.MISSING, "B": regen.AMBIGUOUS,
                                 "C": regen.ABSENT_OPTIONAL, "D": 123}
    ext: dict[str, dict[str, int | str]] = {"f.py": {"E": regen.MISSING, "F": 9}}
    assert regen.missing_required(res, ext) == {"A", "f.py:E"}


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


def test_remap_scope_includes_doctrine_manifest() -> None:
    """docs/doctrine_manifest.yaml MUST stay in the --write remap scope: its
    canonical_sheets ids are what check_doctrine_drift M4 (CI-BLOCKING)
    compares against sheet_ids.py, and the *.py-only remap missed it on the
    2026-07-23 rebuild (PR #670 hand-fixed it). Same parity-teeth pattern as
    test_regen_expect_table_matches_registry_and_stages."""
    paths = regen.remap_file_paths()
    assert regen.DOCTRINE_MANIFEST_PATH in paths
    assert regen.DOCTRINE_MANIFEST_PATH.is_file()
    # The wipe tool must ALSO be in the list (its exclusion happens at the
    # write loop, never by dropping it from the glob — a reordering that
    # silently removed the exemption comment's anchor would be invisible).
    assert (REPO_ROOT / "scripts" / "migrations" / "wipe_tenant.py") in paths


def test_integer_remap_rewrites_yaml_content() -> None:
    """The two-phase remap is format-agnostic — prove it flips a manifest-shaped
    yaml fragment (the PR #670 miss, synthetically re-created)."""
    yaml_text = (
        "canonical_sheets:\n"
        "  SHEET_CONFIG:\n"
        "    id: 8933909738770308\n"
        "    source: \"shared/sheet_ids.py:SHEET_CONFIG\"\n"
    )
    out, n = regen.rewrite_integer_remap(yaml_text, {8933909738770308: 12345})
    assert "id: 12345" in out
    assert n == 1


def test_sweep_covers_yaml_and_md_report_only(tmp_path: Path) -> None:
    """The widened sweep surfaces replaced ids in yaml/yml/md (report-only) —
    and never rewrites them (file content asserted unchanged)."""
    (tmp_path / "runbook.md").write_text("old sheet id 111222333 lives here\n")
    (tmp_path / "conf.yaml").write_text("sheet_id: 111222333\n")
    (tmp_path / "pin.py").write_text("SHEET = 111222333\n")
    (tmp_path / "other.txt").write_text("111222333\n")  # outside SWEEP_GLOBS
    hits = regen.sweep_repo_for_old_ids(
        {111222333: 999}, skip=set(), root=tmp_path)
    hit_files = {h.split(":")[0] for h in hits}
    assert hit_files == {"runbook.md", "conf.yaml", "pin.py"}
    # report-only: nothing rewritten
    assert "111222333" in (tmp_path / "runbook.md").read_text()
    assert "111222333" in (tmp_path / "conf.yaml").read_text()


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


def test_regen_expect_table_matches_registry_and_stages() -> None:
    """Every REGEN_EXPECT key is a real stage; every expected constant is a real
    regen REGISTRY / EXTERNAL_CONSTANTS name (a typo would make --expect abort
    the whole stand-up at that stage); every regen stage has an expect list."""
    names = _stage_names(Path("/tmp/x"))
    known = set(regen.REGISTRY) | {
        f"{path}:{c}" for path, consts in regen.EXTERNAL_CONSTANTS.items()
        for c in consts}
    for stage, expects in standup.REGEN_EXPECT.items():
        assert stage in names, stage
        for const in expects:
            assert const in known, (stage, const)
    regen_stages = {n for n in names if n.startswith("regen-")}
    assert regen_stages == set(standup.REGEN_EXPECT)


def test_every_vc03_config_row_has_a_seeder() -> None:
    """Every load-bearing ITS_Config row verify_cutover VC-03 asserts must be
    SEEDED by some script — the 2026-07-23 rehearsal found 15 rows that had only
    ever been hand-created, so a fresh tenant failed VC-03 with no scripted
    remedy. A new ConfigRow in verify_cutover without a matching seeder literal
    RED-lights here (add it to a seeder in the same PR)."""
    vc_text = (REPO_ROOT / "scripts" / "verify_cutover.py").read_text(encoding="utf-8")
    settings = set(re.findall(r'ConfigRow\(\s*"([^"]+)"', vc_text))
    assert len(settings) > 30, "ConfigRow parse regressed"
    corpus = (REPO_ROOT / "scripts" / "seed_its_config.py").read_text(encoding="utf-8")
    for path in sorted((REPO_ROOT / "scripts" / "migrations").glob("*.py")):
        corpus += path.read_text(encoding="utf-8")
    unseeded = {s for s in settings if s not in corpus}
    # system.docs_index_sheet_id is SELF-RECORDED by build_docs_index_sheet.py and
    # the Box-root rows are auto-pasted by standup's box-roots stage — both appear
    # in the corpus as literals, so no static exemptions are needed today.
    assert not unseeded, (
        f"VC-03 config rows with NO seeder anywhere: {sorted(unseeded)}")


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


# ---- _rest_retry: transient-vs-permanent + the wipe-dump fail-closed fix ----
#
# The 2026-07-23 review finding: dump_workspace classified 429/5xx/timeouts and
# the sheet_dump_truncated RuntimeError as "unreadable — skip and delete anyway"
# (a fail-open data-loss path; the dump is the sole row-restore source). These
# tests prove the new polarity BITES: transient exhaustion ABORTS the wipe,
# while the genuinely-permanent signatures (404 / errorCode 1006/1115 — the
# four broken ITS_Errors shells) still classify unreadable-and-continue.


class _FakeResponse:
    def __init__(self, status: int, body: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self) -> dict[str, Any]:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _http_error(f"{self.status_code} Client Error", self)


def _http_error(message: str, response: _FakeResponse) -> requests.HTTPError:
    """Typed shim — types-requests pins HTTPError.response to Response | None."""
    return requests.HTTPError(message, response=cast(Any, response))


def _scripted_requests(monkeypatch: pytest.MonkeyPatch,
                       responses: list[Any]) -> list[float]:
    """Feed request_with_retry a scripted response sequence; capture sleeps.

    Each entry is a _FakeResponse (returned) or an Exception (raised).
    """
    sleeps: list[float] = []
    calls = iter(responses)

    def _fake_request(method: str, url: str, **kwargs: Any) -> Any:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(_rest_retry.requests, "request", _fake_request)
    monkeypatch.setattr(_rest_retry.time, "sleep", sleeps.append)
    return sleeps


def test_retry_429_then_success_honors_retry_after(
        monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _scripted_requests(monkeypatch, [
        _FakeResponse(429, headers={"Retry-After": "7"}),
        _FakeResponse(200, {"data": []}),
    ])
    r = _rest_retry.request_with_retry("get", "https://x/sheets/1")
    assert r.status_code == 200
    assert sleeps == [7.0]


def test_retry_persistent_429_raises_after_budget(
        monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _scripted_requests(monkeypatch, [_FakeResponse(429)] * 3)
    with pytest.raises(requests.HTTPError, match="429 transient"):
        _rest_retry.request_with_retry("get", "https://x/sheets/1", attempts=3)
    assert len(sleeps) == 2  # no sleep after the final attempt


def test_retry_permanent_404_raises_immediately_no_retries(
        monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _scripted_requests(monkeypatch, [_FakeResponse(404)])
    with pytest.raises(requests.HTTPError):
        _rest_retry.request_with_retry("get", "https://x/sheets/1")
    assert sleeps == []  # a permanent 4xx must never burn the retry budget


def test_retry_timeout_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps = _scripted_requests(monkeypatch, [
        requests.Timeout("read timed out"),
        _FakeResponse(200, {"ok": True}),
    ])
    r = _rest_retry.request_with_retry("get", "https://x/ws", backoff_seconds=1.5)
    assert r.json() == {"ok": True}
    assert sleeps == [1.5]


def test_retry_no_raise_mode_returns_permanent_but_raises_transient(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """raise_for_status=False hands a permanent 4xx back for caller inspection
    (the shares-POST already-shared WARN path) — but transient exhaustion STILL
    raises, so a 429 can never masquerade as a caller-visible non-200."""
    _scripted_requests(monkeypatch, [_FakeResponse(400, {"errorCode": 1025})])
    r = _rest_retry.request_with_retry(
        "post", "https://x/shares", raise_for_status=False)
    assert r.status_code == 400
    _scripted_requests(monkeypatch, [_FakeResponse(503)] * 2)
    with pytest.raises(requests.HTTPError, match="503 transient"):
        _rest_retry.request_with_retry(
            "post", "https://x/shares", attempts=2, raise_for_status=False)


def test_is_permanent_read_failure_classification() -> None:
    perm_404 = _http_error("404", _FakeResponse(404))
    perm_1115 = _http_error("400", _FakeResponse(400, {"errorCode": 1115}))
    perm_1006 = _http_error("400", _FakeResponse(400, {"errorCode": 1006}))
    transient_429 = _http_error("429", _FakeResponse(429))
    no_response = requests.ConnectionError("boom")
    assert _rest_retry.is_permanent_read_failure(perm_404)
    assert _rest_retry.is_permanent_read_failure(perm_1115)
    assert _rest_retry.is_permanent_read_failure(perm_1006)
    assert not _rest_retry.is_permanent_read_failure(transient_429)
    assert not _rest_retry.is_permanent_read_failure(no_response)


_WS_FIXTURE = {"id": 1, "name": "ITS — System"}


def _dumpable_workspace(monkeypatch: pytest.MonkeyPatch,
                        sheet_dump: Any) -> None:
    """Wire dump_workspace's collaborators so only _sheet_dump varies."""
    monkeypatch.setattr(wipe, "_workspace_tree", lambda ws_id: {
        "id": 1, "name": "ITS — System", "folders": [],
        "sheets": [{"id": 42, "name": "ITS_Config"}]})
    monkeypatch.setattr(wipe, "_workspace_shares", lambda ws_id: [])
    monkeypatch.setattr(wipe, "_sheet_dump", sheet_dump)


def test_dump_workspace_permanent_failure_classified_unreadable(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def _raise_404(sheet_id: int) -> dict[str, Any]:
        raise _http_error("404 Client Error", _FakeResponse(404))

    _dumpable_workspace(monkeypatch, _raise_404)
    dumped, unreadable = wipe.dump_workspace(_WS_FIXTURE, tmp_path)
    assert dumped == 0
    assert len(unreadable) == 1 and "id=42" in unreadable[0]


def test_dump_workspace_transient_exhaustion_propagates(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An exhausted 429 must ABORT (guard 4), never classify-and-proceed."""
    def _raise_429(sheet_id: int) -> dict[str, Any]:
        raise _http_error("429 transient error", _FakeResponse(429))

    _dumpable_workspace(monkeypatch, _raise_429)
    with pytest.raises(requests.HTTPError, match="429"):
        wipe.dump_workspace(_WS_FIXTURE, tmp_path)


def test_dump_workspace_truncation_propagates(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """sheet_dump_truncated is a transient pagination artifact — it was
    misclassified as 'unreadable' before 2026-07-23; now it aborts."""
    def _raise_truncated(sheet_id: int) -> dict[str, Any]:
        raise RuntimeError("sheet_dump_truncated: sheet 42 totalRowCount=10 "
                           "but 7 rows fetched — refusing a lossy dump")

    _dumpable_workspace(monkeypatch, _raise_truncated)
    with pytest.raises(RuntimeError, match="sheet_dump_truncated"):
        wipe.dump_workspace(_WS_FIXTURE, tmp_path)


def test_wipe_main_aborts_on_transient_dump_failure(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End-to-end polarity proof: a transient dump failure aborts the WHOLE
    wipe with nothing deleted (exit 1, no delete call reachable)."""
    monkeypatch.setattr(wipe, "require_daemons_down", lambda: None)
    monkeypatch.setattr(wipe, "_list_workspaces",
                        lambda: [dict(zip(("name", "id"),
                                          wipe.SMARTSHEET_WORKSPACE_ALLOWLIST[0],
                                          strict=True))])
    monkeypatch.setattr(wipe, "_box_root_items", lambda: [])
    monkeypatch.setattr(wipe, "_confirm_phrase", lambda: True)
    monkeypatch.setattr(wipe, "DUMP_ROOT", tmp_path)
    # past the phrase gate main() writes the stand-up ACT-fence marker; wipe is
    # a bare scripts/ module the conftest live-state sweep cannot see, so
    # redirect explicitly or the guard refuses the live ~/its/state write.
    monkeypatch.setattr(wipe, "STANDUP_MARKER_PATH", tmp_path / "marker.json")

    def _transient_dump(ws: dict[str, Any], dump_dir: Path) -> tuple[int, list[str]]:
        raise _http_error("503 transient error", _FakeResponse(503))

    monkeypatch.setattr(wipe, "dump_workspace", _transient_dump)

    def _boom(*a: Any, **k: Any) -> None:
        raise AssertionError("delete reached despite a failed dump")

    monkeypatch.setattr(wipe, "_delete_workspace", _boom)
    monkeypatch.setattr(wipe, "_delete_box_folder", _boom)
    monkeypatch.setattr(sys, "argv", ["wipe_tenant.py", "--commit"])
    assert wipe.main() == 1


# ---- standup: non-interactive contract + streamed output + run-state -------
#
# 2026-07-23 review items 2/3/4: the blind `input="y\n"*8` feed would silently
# confirm ANY prompt a builder grows later (including a destructive one); child
# output block-buffered illegibly (the mid-run PYTHONUNBUFFERED fix lived only
# in the operator's shell); and 5 resume points meant 5 hand-typed --start-at
# values. These tests prove the replacement contract BITES.


def _child_script(tmp_path: Path, name: str, body: str) -> str:
    (tmp_path / name).write_text(body, encoding="utf-8")
    return name


def test_run_script_streams_prefixed_output(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(standup, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(standup, "_current_stage", "tstage")
    rel = _child_script(tmp_path, "child.py", "print('hello')\nprint('world')\n")
    standup._run_script(rel)
    out = capsys.readouterr().out
    assert "[tstage/child] hello" in out
    assert "[tstage/child] world" in out


def test_run_script_sets_contract_env(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(standup, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(standup, "_current_stage", "env")
    rel = _child_script(
        tmp_path, "env_child.py",
        "import os\nprint(os.environ.get('STANDUP_NONINTERACTIVE'),"
        " os.environ.get('PYTHONUNBUFFERED'))\n")
    standup._run_script(rel)
    assert "[env/env_child] 1 1" in capsys.readouterr().out


def test_run_script_unexpected_prompt_fails_loudly(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """THE contract's teeth: a brand-new prompt in a child (here, a synthetic
    destructive one) hits the CLOSED stdin, raises EOFError, and FAILS the
    stage — under the old blind y-feed it would have been silently confirmed."""
    monkeypatch.setattr(standup, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(standup, "_current_stage", "prompt")
    rel = _child_script(
        tmp_path, "prompt_child.py",
        "answer = input('Delete conflicting sheet? [y/N] ')\n"
        "print('CONFIRMED', answer)\n")
    with pytest.raises(standup.StageFailedError, match="exited"):
        standup._run_script(rel)
    assert "CONFIRMED" not in capsys.readouterr().out


def test_run_script_tees_to_run_log(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(standup, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(standup, "_current_stage", "tee")
    monkeypatch.setattr(standup, "_run_log_path", tmp_path / "run.log")
    rel = _child_script(tmp_path, "tee_child.py", "print('logged-line')\n")
    standup._run_script(rel)
    assert "logged-line" in (tmp_path / "run.log").read_text(encoding="utf-8")


def test_confirm_seams_auto_approve_only_under_env(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Each of the six builder gates auto-approves ONLY under
    STANDUP_NONINTERACTIVE=1 and NEVER touches stdin while doing so (input is
    booby-trapped); without the env var the prompt remains the control."""
    import build_box_roots as bbr
    import build_safety_portal_workspace as bspw
    import build_system_sheets as bss
    import build_system_workspace as bsw
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import seed_its_config as sic

    def _boom(*a: Any, **k: Any) -> str:
        raise AssertionError("stdin touched under STANDUP_NONINTERACTIVE")

    monkeypatch.setenv("STANDUP_NONINTERACTIVE", "1")
    monkeypatch.setattr("builtins.input", _boom)
    assert bsw._confirm("x") is True
    assert bss._confirm("x") is True
    assert legacy._confirm("x") is True
    assert sic._confirm("x") is True
    assert bbr._confirm_live_writes(["A"], "its@example.com") is True
    gate = bspw.LiveWriteGate(dry_run=False)
    assert gate.allow("create X") is True
    # dry-run stays dry even under the env var — auto-approve must never
    # convert a dry run into live writes.
    dry_gate = bspw.LiveWriteGate(dry_run=True)
    assert dry_gate.allow("create X") is False

    # Without the env var the prompt is the control again.
    monkeypatch.delenv("STANDUP_NONINTERACTIVE")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    assert bsw._confirm("x") is True
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    assert bsw._confirm("x") is False
    assert sic._confirm("x") is False


def test_resume_derives_first_incomplete_stage(tmp_path: Path) -> None:
    names = ["a", "b", "c", "d"]
    standup._write_state({
        "run_id": "r1", "status": "failed",
        "flags": {"no_restore": False, "skip_shares": False,
                  "dump_dir": str(tmp_path)},
        "completed": ["a", "b"], "stage_names": names, "stages": {},
    }, tmp_path)
    assert standup._resume_start_stage(
        tmp_path, no_restore=False, skip_shares=False, stage_names=names) == "c"


def test_resume_refusals(tmp_path: Path) -> None:
    names = ["a", "b"]
    # no state file
    with pytest.raises(standup.StageFailedError, match="no run state"):
        standup._resume_start_stage(
            tmp_path, no_restore=False, skip_shares=False, stage_names=names)
    # completed run
    standup._write_state({
        "status": "complete",
        "flags": {"no_restore": False, "skip_shares": False},
        "completed": names,
    }, tmp_path)
    with pytest.raises(standup.StageFailedError, match="COMPLETE"):
        standup._resume_start_stage(
            tmp_path, no_restore=False, skip_shares=False, stage_names=names)
    # conflicting flags (recorded skip_shares=False, supplied True)
    standup._write_state({
        "status": "failed",
        "flags": {"no_restore": False, "skip_shares": False},
        "completed": ["a"],
    }, tmp_path)
    with pytest.raises(standup.StageFailedError, match="conflict"):
        standup._resume_start_stage(
            tmp_path, no_restore=False, skip_shares=True, stage_names=names)


def test_write_state_is_best_effort(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """A state-write failure WARNs and continues — bookkeeping must never kill
    an otherwise-healthy attended run."""
    def _oser(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(standup.state_io, "atomic_write_json", _oser)
    standup._write_state({"status": "running"}, tmp_path)  # must not raise
    assert "run_state_write_failed" in capsys.readouterr().out
