"""Orchestration tests for the Mac publish daemon (slice 3b) — the privileged git/deploy
ops + portal_client HTTP are mocked; apply_publish runs against the REAL catalog. Verifies
the stage sequence + stamping + the fail+CRITICAL detect-and-alert (C12 mandate)."""
from __future__ import annotations

import json
import subprocess

import pytest

import shared.kill_switch as ks
from safety_reports import publish_daemon as pd


def _create_def() -> dict:
    return {
        "form_code": "incident-v1", "parent_form_code": "incident", "form_name": "Incident",
        "variant_label": None, "version": 1, "archetype": "rows_signatures",
        "source_pdf": "x.pdf", "sections": [{"type": "static_text", "text": "x"}],
    }


def _row(op: str, identity: str, parent: str, *, definition: dict | None = None,
         target: str | None = None, rid: int = 1) -> dict:
    return {
        "id": rid, "op": op, "identity": identity, "parent_form_code": parent,
        "target_form_code": target,
        "definition_json": json.dumps(definition) if definition is not None else None,
        "status": "queued",
    }


@pytest.fixture
def stub(mocker):
    mocker.patch.object(ks, "check_system_state", return_value=ks.SystemState.ACTIVE)
    return {
        "enabled": mocker.patch.object(pd, "_polling_enabled", return_value=True),
        "creds": mocker.patch.object(pd, "_resolve_creds",
                                     return_value=pd._Creds("https://portal.test", "tok")),
        "pending": mocker.patch.object(pd.portal_client, "get_publish_pending"),
        "claim": mocker.patch.object(pd.portal_client, "claim_publish"),
        "stamp": mocker.patch.object(pd.portal_client, "stamp_publish", return_value=True),
        "reset": mocker.patch.object(pd, "_reset_to_main"),
        "unstrand": mocker.patch.object(pd, "_unstrand_if_needed"),
        "apply_wt": mocker.patch.object(pd, "_apply_to_worktree"),
        "commit": mocker.patch.object(pd, "_commit_test_merge"),
        "deploy": mocker.patch.object(pd, "_deploy_land_health"),
        "archive": mocker.patch.object(pd, "_regenerate_archive"),
        "log": mocker.patch.object(pd.error_log, "log"),
    }


def _statuses(stub) -> list[str]:
    return [c.kwargs["status"] for c in stub["stamp"].call_args_list]


def _critical_fired(stub) -> bool:
    return any(c.args and c.args[0] == pd.Severity.CRITICAL for c in stub["log"].call_args_list)


# ── happy paths ───────────────────────────────────────────────────────────────


def test_create_actuates_through_the_full_state_machine(stub):
    stub["pending"].return_value = [{"id": 1}]
    stub["claim"].return_value = _row("create", "incident", "incident", definition=_create_def())
    out = pd.publish_once()
    assert out.actuated == 1 and out.failed == 0
    assert _statuses(stub) == ["validated", "tested", "live", "archived"]
    stub["apply_wt"].assert_called_once()
    stub["commit"].assert_called_once()
    stub["deploy"].assert_called_once()
    stub["archive"].assert_called_once()
    assert not _critical_fired(stub)


def test_delete_actuates_without_a_definition(stub):
    stub["pending"].return_value = [{"id": 2}]
    stub["claim"].return_value = _row("delete", "jha", "jha", rid=2)  # jha exists → retire
    out = pd.publish_once()
    assert out.actuated == 1
    assert _statuses(stub) == ["validated", "tested", "live", "archived"]


def test_regenerate_archive_uses_venv_interpreter_not_bare_python(mocker):
    """Regression: the `archived` stage shells out with sys.executable, NOT a bare "python".
    Under launchd (minimal PATH; macOS ships only `python3`, and the interpreter is really
    ~/its/.venv/bin/python) a bare "python" raised FileNotFoundError, failing every publish
    at `archived` AFTER the form had already gone live."""
    run = mocker.patch.object(pd.subprocess, "run")
    pd._regenerate_archive()
    cmd = run.call_args.args[0]
    assert cmd[0] == pd.sys.executable
    assert cmd[0] != "python"  # the exact bug
    assert cmd[1:] == ["-m", "scripts.generate_form_archive", "--upload"]


# ── failures stamp failed(stage) + fire the operator CRITICAL ────────────────────


def test_validation_failure_stamps_failed_and_fires_critical(stub):
    stub["pending"].return_value = [{"id": 3}]
    # op=create with identity 'jha' (already exists) → apply_publish raises at stage 1.
    stub["claim"].return_value = _row("create", "jha", "jha", definition=_create_def(), rid=3)
    out = pd.publish_once()
    assert out.failed == 1 and out.actuated == 0
    assert _statuses(stub) == ["failed"]
    assert stub["stamp"].call_args.kwargs["failed_stage"] == "validated"
    assert _critical_fired(stub)
    stub["commit"].assert_not_called()  # never reached actuation


def test_commit_failure_stamps_failed_tested_and_fires_critical(stub):
    stub["pending"].return_value = [{"id": 4}]
    stub["claim"].return_value = _row("create", "incident", "incident", definition=_create_def(), rid=4)
    stub["commit"].side_effect = subprocess.CalledProcessError(1, ["gh"], stderr="CI red")
    out = pd.publish_once()
    assert out.failed == 1
    assert _statuses(stub) == ["validated", "failed"]
    assert stub["stamp"].call_args.kwargs["failed_stage"] == "tested"
    assert _critical_fired(stub)
    stub["deploy"].assert_not_called()


def test_deploy_failure_stamps_failed_live(stub):
    stub["pending"].return_value = [{"id": 5}]
    stub["claim"].return_value = _row("create", "incident", "incident", definition=_create_def(), rid=5)
    stub["deploy"].side_effect = RuntimeError("wrangler boom")
    out = pd.publish_once()
    assert out.failed == 1
    assert _statuses(stub) == ["validated", "tested", "failed"]
    assert stub["stamp"].call_args.kwargs["failed_stage"] == "live"
    assert _critical_fired(stub)
    stub["archive"].assert_not_called()


# ── gating / fail-closed / lease ─────────────────────────────────────────────────


def test_polling_disabled_halts_without_polling(stub):
    stub["enabled"].return_value = False
    out = pd.publish_once()
    assert out.halted == "polling_disabled"
    stub["pending"].assert_not_called()


def test_unresolved_creds_halts_loud(stub):
    stub["creds"].return_value = None
    out = pd.publish_once()
    assert out.halted == "creds_unresolved"
    assert any(c.args and c.args[0] == pd.Severity.ERROR for c in stub["log"].call_args_list)
    stub["pending"].assert_not_called()


def test_already_leased_row_is_skipped(stub):
    stub["pending"].return_value = [{"id": 6}]
    stub["claim"].return_value = None  # a concurrent run already leased it
    out = pd.publish_once()
    assert out.skipped_unclaimed == 1 and out.actuated == 0
    stub["commit"].assert_not_called()


# ── _unstrand_if_needed (idle self-heal: recover a stranded tree at the top of a cycle) ──


def test_unstrand_recovers_a_stray_branch(mocker):
    """On a leftover publish/req-* branch (idle-stranded), recover via _reset_to_main."""
    mocker.patch.object(pd, "_git", return_value="publish/req-7-incident\n")
    reset = mocker.patch.object(pd, "_reset_to_main")
    pd._unstrand_if_needed()
    reset.assert_called_once()


def test_unstrand_is_a_noop_on_main(mocker):
    """The common idle case: already on main → no reset, no network pull (cheap rev-parse)."""
    mocker.patch.object(pd, "_git", return_value="main\n")
    reset = mocker.patch.object(pd, "_reset_to_main")
    pd._unstrand_if_needed()
    reset.assert_not_called()


def test_publish_once_unstrands_before_actuating(stub, mocker):
    """publish_once calls the idle self-heal at the top of every cycle (even with no rows)."""
    stub["pending"].return_value = []
    pd.publish_once()
    stub["unstrand"].assert_called_once()


def test_publish_once_halts_loud_when_unstrand_fails(stub):
    """A recovery failure halts the cycle + logs ERROR — never silently actuates from a
    stranded tree."""
    stub["unstrand"].side_effect = RuntimeError("git checkout main failed")
    out = pd.publish_once()
    assert out.halted == "unstrand_failed"
    stub["pending"].assert_not_called()
    assert any(c.args and c.args[0] == pd.Severity.ERROR for c in stub["log"].call_args_list)


# ── _wait_for_ci (the synchronous CI gate that replaced `gh pr merge --auto`) ────


def test_wait_for_ci_returns_when_clean(mocker):
    mocker.patch.object(pd, "_gh", return_value=json.dumps({"mergeStateStatus": "CLEAN", "statusCheckRollup": []}))
    pd._wait_for_ci("publish/req-1-jha")  # returns without raising


def test_wait_for_ci_raises_on_a_failed_check(mocker):
    mocker.patch.object(pd, "_gh", return_value=json.dumps({
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [{"name": "portal", "conclusion": "FAILURE"}],
    }))
    with pytest.raises(RuntimeError, match="CI failed"):
        pd._wait_for_ci("publish/req-1-jha")


def test_wait_for_ci_dedupes_and_surfaces_detail(mocker):
    """D2: a single failing job double-fires (push + pull_request) → the reason de-dupes by
    NAME (no 'portal, portal'), and each failing check carries its real log excerpt rather
    than a bare job name."""
    rollup = {
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [
            {"name": "test", "conclusion": "FAILURE", "detailsUrl": "https://x/actions/runs/1/job/111"},
            {"name": "test", "conclusion": "FAILURE", "detailsUrl": "https://x/actions/runs/2/job/222"},
            {"name": "portal", "conclusion": "FAILURE", "detailsUrl": "https://x/actions/runs/1/job/333"},
            {"name": "portal", "conclusion": "FAILURE", "detailsUrl": "https://x/actions/runs/2/job/444"},
        ],
    }
    fail_log = "test\tTests\t2026-06-09T05:15:55Z AssertionError: expected 11 to be 10\n"

    def fake_gh(*a):
        if a[:2] == ("pr", "view"):
            return json.dumps(rollup)
        if a[:2] == ("run", "view"):
            return fail_log
        return ""

    mocker.patch.object(pd, "_gh", side_effect=fake_gh)
    with pytest.raises(RuntimeError) as exc:
        pd._wait_for_ci("publish/req-1-jha")
    msg = str(exc.value)
    assert msg.count("test:") == 1 and msg.count("portal:") == 1  # de-duped by name
    assert "expected 11 to be 10" in msg  # the real reason, not a bare job name


def test_wait_for_ci_updates_a_behind_branch_then_merges(mocker):
    views = [
        json.dumps({"mergeStateStatus": "BEHIND", "statusCheckRollup": []}),
        json.dumps({"mergeStateStatus": "CLEAN", "statusCheckRollup": []}),
    ]
    gh = mocker.patch.object(pd, "_gh", side_effect=lambda *a: views.pop(0) if a[:2] == ("pr", "view") else "")
    mocker.patch.object(pd.time, "sleep")
    pd._wait_for_ci("publish/req-1-jha")
    assert any(c.args[:2] == ("pr", "update-branch") for c in gh.call_args_list)
