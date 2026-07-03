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
        # PR-2: publish_once now sweeps stale rows (calls get_publish_stuck). Default to none so
        # the existing tests' sweep is a no-op; the sweep tests below set a return value.
        "stuck": mocker.patch.object(pd.portal_client, "get_publish_stuck", return_value=[]),
        "reset": mocker.patch.object(pd, "_reset_to_main"),
        "unstrand": mocker.patch.object(pd, "_unstrand_if_needed"),
        "apply_wt": mocker.patch.object(pd, "_apply_to_worktree"),
        "commit": mocker.patch.object(pd, "_commit_test_merge"),
        "deploy": mocker.patch.object(pd, "_deploy_land_health"),
        "archive": mocker.patch.object(pd, "_regenerate_archive"),
        # PR-1: the daemon now passes required_content to apply_publish. These tests target the
        # state machine / stamping / error-handling, NOT the legal floor (tested in
        # test_publish_manifest + test_form_definitions), so stub the floor to an empty manifest
        # (no requirements → any definition passes the C3 re-check) to keep them decoupled.
        "req_content": mocker.patch.object(pd, "_load_required_content", return_value={}),
        # Slice 1 (R3-F1): publish_once now gates each cycle-with-work on remote D1 migration
        # state (wrangler shell-out). Default to "none pending" so existing tests proceed; the
        # deploy-gate tests below set a pending list / a failure.
        "migrations": mocker.patch.object(pd, "_pending_migrations", return_value=[]),
        # R4-F1: the daemon now writes an ITS_Daemon_Health heartbeat per cycle. Mock the
        # two thin-delegator seams so no test touches live state / Smartsheet.
        "hb": mocker.patch.object(pd, "_write_heartbeat"),
        "hb_row": mocker.patch.object(pd, "_write_heartbeat_row"),
        "circuit": mocker.patch.object(pd.circuit_breaker, "is_open", return_value=False),
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
    mocker.patch.object(pd.tempfile, "mkdtemp", return_value="/tmp/its_form_archive_test")
    rmtree = mocker.patch.object(pd.shutil, "rmtree")
    pd._regenerate_archive()
    cmd = run.call_args.args[0]
    assert cmd[0] == pd.sys.executable
    assert cmd[0] != "python"  # the exact bug
    # renders into a throwaway tempdir (--out-dir), NOT ~/its/form_archive_out, and cleans up
    assert cmd[1:] == ["-m", "scripts.generate_form_archive", "--upload", "--out-dir", "/tmp/its_form_archive_test"]
    rmtree.assert_called_once_with("/tmp/its_form_archive_test", ignore_errors=True)


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


# ── D1 pending-migrations deploy gate (Slice 1, R3-F1 — forensic class #2, publish #434) ──


def test_pending_migrations_refuse_the_cycle_before_claiming(stub):
    """Unapplied remote migrations REFUSE the whole cycle pre-claim: no lease burned, no row
    stamped (they stay `pending` on the Worker for the next cycle), and the refusal is LOUD —
    a CRITICAL under the distinct category naming the pending files."""
    stub["pending"].return_value = [{"id": 7}]
    stub["migrations"].return_value = ["0030_job_daily_requirements.sql", "0031_job_expected_materials.sql"]
    out = pd.publish_once()
    assert out.halted == "pending_migrations"
    assert out.polled == 1 and out.actuated == 0 and out.failed == 0
    stub["claim"].assert_not_called()
    stub["commit"].assert_not_called()
    stub["deploy"].assert_not_called()
    stub["stamp"].assert_not_called()  # nothing terminal-failed — the request survives
    crit = [
        c for c in stub["log"].call_args_list
        if c.args and c.args[0] == pd.Severity.CRITICAL
        and c.kwargs.get("error_code") == pd.ERR_PENDING_MIGRATIONS
    ]
    assert len(crit) == 1
    assert "0030_job_daily_requirements.sql" in crit[0].args[2]  # the pending list is named


def test_operator_apply_unblocks_the_next_cycle_automatically(stub):
    """The retry semantics the pre-claim placement buys: cycle 1 refuses (pending), the
    operator applies (no re-publish, no daemon poke), cycle 2 actuates the SAME queued row."""
    stub["pending"].return_value = [{"id": 8}]
    stub["claim"].return_value = _row("create", "incident", "incident", definition=_create_def(), rid=8)
    stub["migrations"].return_value = ["0032_job_daily_requirements_kinds.sql"]
    assert pd.publish_once().halted == "pending_migrations"
    stub["migrations"].return_value = []  # the operator ran `wrangler d1 migrations apply`
    out = pd.publish_once()
    assert out.halted is None and out.actuated == 1
    assert _statuses(stub) == ["validated", "tested", "live", "archived"]


def test_migration_check_failure_halts_fail_closed(stub):
    """Cannot verify ⇒ must not deploy: a wrangler-list failure halts the cycle (fail-closed)
    with a PAGING CRITICAL under its own category (a sustained failure blocks every publish —
    ERROR would be a silent stall; ops review), and nothing is claimed."""
    stub["pending"].return_value = [{"id": 9}]
    stub["migrations"].side_effect = subprocess.CalledProcessError(1, ["npx"], stderr="net down")
    out = pd.publish_once()
    assert out.halted == "migration_check_failed"
    stub["claim"].assert_not_called()
    assert any(
        c.args and c.args[0] == pd.Severity.CRITICAL
        and c.kwargs.get("error_code") == pd.ERR_MIGRATION_CHECK
        for c in stub["log"].call_args_list
    )


def test_idle_cycle_never_shells_out_to_wrangler(stub):
    """No rows → no deploy possible → the gate is skipped (a 60s-cadence daemon must not
    hit wrangler/network every idle cycle)."""
    stub["pending"].return_value = []
    out = pd.publish_once()
    assert out.halted is None
    stub["migrations"].assert_not_called()


# ── ITS_Daemon_Health heartbeat (R4-F1) ──────────────────────────────────────────


def test_cycle_writes_ok_heartbeat(stub):
    stub["pending"].return_value = [{"id": 1}]
    stub["claim"].return_value = _row("create", "incident", "incident", definition=_create_def())
    pd.publish_once()
    stub["hb"].assert_called_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "OK"
    assert stub["hb_row"].call_args.kwargs["items_processed"] == 1
    assert stub["hb_row"].call_args.kwargs["error_summary"] is None


def test_failed_actuation_writes_degraded_heartbeat(stub):
    stub["pending"].return_value = [{"id": 5}]
    stub["claim"].return_value = _row("create", "incident", "incident", definition=_create_def(), rid=5)
    stub["deploy"].side_effect = RuntimeError("wrangler boom")
    pd.publish_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "DEGRADED"
    assert "failed=1" in stub["hb_row"].call_args.kwargs["error_summary"]


def test_disabled_cycle_skips_heartbeat(stub):
    stub["enabled"].return_value = False
    pd.publish_once()
    stub["hb"].assert_not_called()
    stub["hb_row"].assert_not_called()


def test_unresolved_creds_write_error_heartbeat(stub):
    stub["creds"].return_value = None
    pd.publish_once()
    stub["hb"].assert_called_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "ERROR"


def test_pending_migrations_write_warn_heartbeat(stub):
    # A deliberate, bounded refusal (rows stay queued; operator apply unblocks) → WARN,
    # not ERROR — mirrors portal_poll's halted_transient precedent.
    stub["pending"].return_value = [{"id": 7}]
    stub["migrations"].return_value = ["0033_x.sql"]
    pd.publish_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "WARN"
    assert "deploy blocked" in stub["hb_row"].call_args.kwargs["error_summary"]


def test_open_circuit_writes_circuit_open_heartbeat(stub):
    stub["pending"].return_value = []
    stub["circuit"].return_value = True
    pd.publish_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "CIRCUIT_OPEN"


def test_heartbeat_row_failure_never_blocks_the_cycle(stub):
    # Heartbeat-never-blocks: the outer-catch fence holds even if the delegator raises.
    stub["pending"].return_value = [{"id": 1}]
    stub["claim"].return_value = _row("create", "incident", "incident", definition=_create_def())
    stub["hb_row"].side_effect = RuntimeError("sheet down")
    out = pd.publish_once()
    assert out.actuated == 1  # primary work unharmed
    assert any(
        c.kwargs.get("error_code") == "daemon_health_write_failed"
        for c in stub["log"].call_args_list
    )


def test_reporter_registration_metadata_is_self_provisioning_config(stub):
    # A1 self-provision rides constructor config — pin the registration identity so the
    # ITS_Daemon_Health row this daemon creates is stable (shared row-state file, ARCH-2).
    r = pd._heartbeat_reporter
    assert r.daemon_name == "safety_reports.publish_daemon"
    assert r.workstream == "safety_reports"
    assert r.interval_seconds == 120
    assert r.row_state_path.name == "heartbeat_row_ids.json"


def test_pending_migrations_diffs_disk_against_remote_list(mocker, tmp_path):
    """_pending_migrations = on-disk *.sql names cross-checked against the wrangler
    `d1 migrations list --remote` output (which prints ONLY unapplied migrations), invoked
    exactly like the deploy stage (same cwd, local wrangler via npx)."""
    mocker.patch.object(pd, "_MIGRATIONS_DIR", tmp_path)
    for name in ("0030_a.sql", "0031_b.sql", "0032_c.sql"):
        (tmp_path / name).write_text("-- migration")
    (tmp_path / "notes.txt").write_text("not a migration")  # non-.sql ignored
    run = mocker.patch.object(pd.subprocess, "run")
    run.return_value.stdout = (
        "Migrations to be applied:\n"
        "┌──────────────┐\n│ 0031_b.sql │\n│ 0032_c.sql │\n└──────────────┘\n"
    )
    assert pd._pending_migrations() == ["0031_b.sql", "0032_c.sql"]
    cmd = run.call_args.args[0]
    assert cmd == ["npx", "wrangler", "d1", "migrations", "list", pd.D1_DATABASE_NAME, "--remote"]
    assert run.call_args.kwargs["cwd"] == pd._ROOT / "safety_portal"
    assert run.call_args.kwargs["check"] is True  # a wrangler failure raises (fail-closed)


def test_pending_migrations_empty_when_remote_is_current(mocker, tmp_path):
    mocker.patch.object(pd, "_MIGRATIONS_DIR", tmp_path)
    (tmp_path / "0030_a.sql").write_text("-- migration")
    run = mocker.patch.object(pd.subprocess, "run")
    run.return_value.stdout = "✅ No migrations to apply!\n"
    assert pd._pending_migrations() == []


def test_deploy_land_health_refuses_ahead_of_pending_migrations(mocker):
    """The authoritative in-stage gate: AFTER the pull, BEFORE `npm run deploy` — pending
    migrations raise PendingMigrationsError (the stage-3 fence stamps failed('live')) and the
    deploy subprocess is NEVER invoked. Fires the distinct CRITICAL naming the files."""
    mocker.patch.object(pd, "_git")
    mocker.patch.object(pd, "_pending_migrations", return_value=["0030_a.sql"])
    run = mocker.patch.object(pd.subprocess, "run")
    log = mocker.patch.object(pd.error_log, "log")
    ping = mocker.patch.object(pd.portal_client, "get_publish_pending")
    with pytest.raises(pd.PendingMigrationsError, match="0030_a.sql"):
        pd._deploy_land_health(pd._Creds("https://portal.test", "tok"), "incident-v1")
    run.assert_not_called()   # the deploy never ran
    ping.assert_not_called()  # nor the liveness ping
    assert any(
        c.args and c.args[0] == pd.Severity.CRITICAL
        and c.kwargs.get("error_code") == pd.ERR_PENDING_MIGRATIONS
        for c in log.call_args_list
    )


def test_deploy_land_health_deploys_when_remote_is_current(mocker):
    mocker.patch.object(pd, "_git")
    mocker.patch.object(pd, "_pending_migrations", return_value=[])
    run = mocker.patch.object(pd.subprocess, "run")
    mocker.patch.object(pd.portal_client, "get_publish_pending")
    pd._deploy_land_health(pd._Creds("https://portal.test", "tok"), "incident-v1")
    assert run.call_args.args[0] == ["npm", "run", "deploy"]


# ── stale-row sweep (PR-2: reclaim a crashed/stalled publish before it wedges a parent) ──


def test_sweep_reclaims_a_stale_row_and_fires_critical(stub):
    stub["pending"].return_value = []
    stub["stuck"].return_value = [
        {"id": 9, "status": "tested", "lease_owner": "deadmac:123", "parent_form_code": "jha"},
    ]
    out = pd.publish_once()
    assert out.reclaimed == 1
    failed = [c for c in stub["stamp"].call_args_list if c.kwargs.get("status") == "failed"]
    assert any(
        c.kwargs.get("request_id") == 9 and c.kwargs.get("failed_stage") == "stale_reclaimed"
        for c in failed
    )
    assert _critical_fired(stub)


def test_sweep_is_a_noop_when_nothing_is_stuck(stub):
    stub["pending"].return_value = []
    stub["stuck"].return_value = []
    out = pd.publish_once()
    assert out.reclaimed == 0
    assert not _critical_fired(stub)


def test_sweep_fetch_failure_is_logged_not_fatal(stub):
    # A sweep fetch failure must NOT wedge the cycle — log ERROR and let the pull proceed.
    stub["stuck"].side_effect = pd.portal_client.PortalTransportError("boom")
    stub["pending"].return_value = []
    out = pd.publish_once()
    assert out.reclaimed == 0
    stub["pending"].assert_called_once()  # the cycle continued past the sweep to the pull
    assert any(c.args and c.args[0] == pd.Severity.ERROR for c in stub["log"].call_args_list)


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
