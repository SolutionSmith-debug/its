"""Orchestration tests for the Mac config actuator (§50 config editor, slice 2) — the
privileged git/deploy ops + portal_client HTTP + the Stage-1 config_apply write are mocked.
Verifies the state-machine stamp sequence + per-stage fencing + the fail+CRITICAL detect-and-
alert, BOTH D1 migration-gate sites, the stale sweep, the idle self-heal, and the heartbeat
status. Mirrors tests/test_publish_daemon.py's stub seam."""
from __future__ import annotations

import json
import subprocess

import pytest

import shared.kill_switch as ks
from po_materials import config_actuator as ca


def _tax_payload() -> dict:
    return {"rates_bp": {"IL": 900}, "state_names": {"IL": "Illinois"}}


def _row(artifact: str = "tax", op: str = "edit", *, target: str | None = None,
         payload: dict | None = None, rid: int = 1) -> dict:
    return {
        "id": rid, "workstream": "po_materials", "artifact_key": artifact, "op": op,
        "target_version": target,
        "payload": json.dumps(payload if payload is not None else _tax_payload()),
        "status": "queued",
    }


@pytest.fixture
def stub(mocker):
    mocker.patch.object(ks, "check_system_state", return_value=ks.SystemState.ACTIVE)
    return {
        "enabled": mocker.patch.object(ca, "_polling_enabled", return_value=True),
        "creds": mocker.patch.object(ca, "_resolve_creds",
                                     return_value=ca._Creds("https://portal.test", "tok")),
        "pending": mocker.patch.object(ca.portal_client, "get_config_pending"),
        "claim": mocker.patch.object(ca.portal_client, "claim_config"),
        "stamp": mocker.patch.object(ca.portal_client, "stamp_config", return_value=True),
        "stuck": mocker.patch.object(ca.portal_client, "get_config_stuck", return_value=[]),
        "reset": mocker.patch.object(ca, "_reset_to_main"),
        "unstrand": mocker.patch.object(ca, "_unstrand_if_needed"),
        # Stage-1 domain write is mocked so orchestration tests never touch the live tree
        # (the REAL apply_config is exercised in test_config_apply.py against a tmp root).
        "apply": mocker.patch.object(ca, "_apply_config", return_value="tax: 1 state rate(s) -> config_version 2"),
        "commit": mocker.patch.object(ca, "_commit_test_merge"),
        "deploy": mocker.patch.object(ca, "_deploy_land_health"),
        "migrations": mocker.patch.object(ca, "_pending_migrations", return_value=[]),
        "hb": mocker.patch.object(ca, "_write_heartbeat"),
        "hb_row": mocker.patch.object(ca, "_write_heartbeat_row"),
        "circuit": mocker.patch.object(ca.circuit_breaker, "is_open", return_value=False),
        "log": mocker.patch.object(ca.error_log, "log"),
    }


def _statuses(stub) -> list[str]:
    return [c.kwargs["status"] for c in stub["stamp"].call_args_list]


def _critical_fired(stub) -> bool:
    return any(c.args and c.args[0] == ca.Severity.CRITICAL for c in stub["log"].call_args_list)


# ── happy paths ───────────────────────────────────────────────────────────────


def test_actuates_through_the_full_state_machine(stub):
    stub["pending"].return_value = [{"id": 1}]
    stub["claim"].return_value = _row()
    out = ca.config_once()
    assert out.actuated == 1 and out.failed == 0
    assert _statuses(stub) == ["validated", "tested", "live", "archived"]
    stub["apply"].assert_called_once()
    stub["commit"].assert_called_once()
    stub["deploy"].assert_called_once()
    assert not _critical_fired(stub)


def test_terms_add_version_actuates(stub):
    stub["pending"].return_value = [{"id": 2}]
    stub["claim"].return_value = _row(
        "terms", "add_version", target="standard_17_v2",
        payload={"profile_id": "standard_17", "text": "x"}, rid=2,
    )
    out = ca.config_once()
    assert out.actuated == 1
    assert _statuses(stub) == ["validated", "tested", "live", "archived"]


# ── failures stamp failed(stage) + fire the operator CRITICAL ────────────────────


def test_validation_failure_stamps_failed_and_fires_critical(stub):
    stub["pending"].return_value = [{"id": 3}]
    stub["claim"].return_value = _row(rid=3)
    stub["apply"].side_effect = ca.config_apply.ConfigApplyError("bad rate")
    out = ca.config_once()
    assert out.failed == 1 and out.actuated == 0
    assert _statuses(stub) == ["failed"]
    assert stub["stamp"].call_args.kwargs["failed_stage"] == "validated"
    assert _critical_fired(stub)
    stub["commit"].assert_not_called()  # never reached actuation


def test_commit_failure_stamps_failed_tested_and_fires_critical(stub):
    stub["pending"].return_value = [{"id": 4}]
    stub["claim"].return_value = _row(rid=4)
    stub["commit"].side_effect = subprocess.CalledProcessError(1, ["gh"], stderr="CI red")
    out = ca.config_once()
    assert out.failed == 1
    assert _statuses(stub) == ["validated", "failed"]
    assert stub["stamp"].call_args.kwargs["failed_stage"] == "tested"
    assert _critical_fired(stub)
    stub["deploy"].assert_not_called()


def test_deploy_failure_stamps_failed_live(stub):
    stub["pending"].return_value = [{"id": 5}]
    stub["claim"].return_value = _row(rid=5)
    stub["deploy"].side_effect = RuntimeError("wrangler boom")
    out = ca.config_once()
    assert out.failed == 1
    assert _statuses(stub) == ["validated", "tested", "failed"]
    assert stub["stamp"].call_args.kwargs["failed_stage"] == "live"
    assert _critical_fired(stub)


# ── gating / fail-closed / lease ─────────────────────────────────────────────────


def test_polling_disabled_halts_without_polling(stub):
    stub["enabled"].return_value = False
    out = ca.config_once()
    assert out.halted == "polling_disabled"
    stub["pending"].assert_not_called()


def test_unresolved_creds_halts_loud(stub):
    stub["creds"].return_value = None
    out = ca.config_once()
    assert out.halted == "creds_unresolved"
    assert any(c.args and c.args[0] == ca.Severity.ERROR for c in stub["log"].call_args_list)
    stub["pending"].assert_not_called()


def test_already_leased_row_is_skipped(stub):
    stub["pending"].return_value = [{"id": 6}]
    stub["claim"].return_value = None  # a concurrent run already leased it
    out = ca.config_once()
    assert out.skipped_unclaimed == 1 and out.actuated == 0
    stub["commit"].assert_not_called()


# ── D1 pending-migrations deploy gate (forensic class #2) — BOTH sites ────────────


def test_pending_migrations_refuse_the_cycle_before_claiming(stub):
    """Unapplied remote migrations REFUSE the whole cycle pre-claim: no lease burned, no row
    stamped (they stay `queued` on the Worker), and the refusal is LOUD (a CRITICAL naming
    the pending files)."""
    stub["pending"].return_value = [{"id": 7}]
    stub["migrations"].return_value = ["0046_x.sql", "0047_y.sql"]
    out = ca.config_once()
    assert out.halted == "pending_migrations"
    assert out.polled == 1 and out.actuated == 0 and out.failed == 0
    stub["claim"].assert_not_called()
    stub["commit"].assert_not_called()
    stub["deploy"].assert_not_called()
    stub["stamp"].assert_not_called()  # nothing terminal-failed — the request survives
    crit = [
        c for c in stub["log"].call_args_list
        if c.args and c.args[0] == ca.Severity.CRITICAL
        and c.kwargs.get("error_code") == ca.ERR_PENDING_MIGRATIONS
    ]
    assert len(crit) == 1
    assert "0046_x.sql" in crit[0].args[2]  # the pending list is named


def test_operator_apply_unblocks_the_next_cycle_automatically(stub):
    stub["pending"].return_value = [{"id": 8}]
    stub["claim"].return_value = _row(rid=8)
    stub["migrations"].return_value = ["0046_x.sql"]
    assert ca.config_once().halted == "pending_migrations"
    stub["migrations"].return_value = []  # the operator ran `wrangler d1 migrations apply`
    out = ca.config_once()
    assert out.halted is None and out.actuated == 1
    assert _statuses(stub) == ["validated", "tested", "live", "archived"]


def test_migration_check_failure_halts_fail_closed(stub):
    """Cannot verify ⇒ must not deploy: a wrangler-list failure halts the cycle (fail-closed)
    with a PAGING CRITICAL under its own category, and nothing is claimed."""
    stub["pending"].return_value = [{"id": 9}]
    stub["migrations"].side_effect = subprocess.CalledProcessError(1, ["npx"], stderr="net down")
    out = ca.config_once()
    assert out.halted == "migration_check_failed"
    stub["claim"].assert_not_called()
    assert any(
        c.args and c.args[0] == ca.Severity.CRITICAL
        and c.kwargs.get("error_code") == ca.ERR_MIGRATION_CHECK
        for c in stub["log"].call_args_list
    )


def test_idle_cycle_never_shells_out_to_wrangler(stub):
    """No rows → no deploy possible → the gate is skipped (a 120s-cadence daemon must not hit
    wrangler/network every idle cycle)."""
    stub["pending"].return_value = []
    out = ca.config_once()
    assert out.halted is None
    stub["migrations"].assert_not_called()


def test_pending_migrations_diffs_disk_against_remote_list(mocker, tmp_path):
    """_pending_migrations = on-disk *.sql names cross-checked against the wrangler
    `d1 migrations list --remote` output (which prints ONLY unapplied migrations), invoked
    exactly like the deploy stage (same cwd, local wrangler via npx)."""
    mocker.patch.object(ca, "_MIGRATIONS_DIR", tmp_path)
    for name in ("0046_a.sql", "0047_b.sql"):
        (tmp_path / name).write_text("-- migration")
    (tmp_path / "notes.txt").write_text("not a migration")
    run = mocker.patch.object(ca.subprocess, "run")
    run.return_value.stdout = (
        "Migrations to be applied:\n┌──────────────┐\n│ 0047_b.sql │\n└──────────────┘\n"
    )
    assert ca._pending_migrations() == ["0047_b.sql"]
    cmd = run.call_args.args[0]
    assert cmd == ["npx", "wrangler", "d1", "migrations", "list", ca.D1_DATABASE_NAME, "--remote"]
    assert run.call_args.kwargs["cwd"] == ca._ROOT / "safety_portal"
    assert run.call_args.kwargs["check"] is True  # a wrangler failure raises (fail-closed)


def test_deploy_land_health_refuses_ahead_of_pending_migrations(mocker):
    """The authoritative in-stage gate (AFTER the pull, BEFORE `npm run deploy`): pending
    migrations raise PendingMigrationsError and the deploy subprocess is NEVER invoked. Fires
    the distinct CRITICAL naming the files — the row NOT stamped live."""
    mocker.patch.object(ca, "_git")
    mocker.patch.object(ca, "_pending_migrations", return_value=["0046_a.sql"])
    run = mocker.patch.object(ca.subprocess, "run")
    log = mocker.patch.object(ca.error_log, "log")
    ping = mocker.patch.object(ca.portal_client, "get_config_pending")
    with pytest.raises(ca.PendingMigrationsError, match="0046_a.sql"):
        ca._deploy_land_health(ca._Creds("https://portal.test", "tok"))
    run.assert_not_called()   # the deploy never ran
    ping.assert_not_called()  # nor the liveness ping
    assert any(
        c.args and c.args[0] == ca.Severity.CRITICAL
        and c.kwargs.get("error_code") == ca.ERR_PENDING_MIGRATIONS
        for c in log.call_args_list
    )


def test_deploy_land_health_deploys_when_remote_is_current(mocker):
    mocker.patch.object(ca, "_git")
    mocker.patch.object(ca, "_pending_migrations", return_value=[])
    run = mocker.patch.object(ca.subprocess, "run")
    mocker.patch.object(ca.portal_client, "get_config_pending")
    ca._deploy_land_health(ca._Creds("https://portal.test", "tok"))
    assert run.call_args.args[0] == ["npm", "run", "deploy"]


# ── ITS_Daemon_Health heartbeat ──────────────────────────────────────────────────


def test_cycle_writes_ok_heartbeat(stub):
    stub["pending"].return_value = [{"id": 1}]
    stub["claim"].return_value = _row()
    ca.config_once()
    stub["hb"].assert_called_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "OK"
    assert stub["hb_row"].call_args.kwargs["items_processed"] == 1
    assert stub["hb_row"].call_args.kwargs["error_summary"] is None


def test_failed_actuation_writes_degraded_heartbeat(stub):
    stub["pending"].return_value = [{"id": 5}]
    stub["claim"].return_value = _row(rid=5)
    stub["deploy"].side_effect = RuntimeError("wrangler boom")
    ca.config_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "DEGRADED"
    assert "failed=1" in stub["hb_row"].call_args.kwargs["error_summary"]


def test_disabled_cycle_skips_heartbeat(stub):
    stub["enabled"].return_value = False
    ca.config_once()
    stub["hb"].assert_not_called()
    stub["hb_row"].assert_not_called()


def test_unresolved_creds_write_error_heartbeat(stub):
    stub["creds"].return_value = None
    ca.config_once()
    stub["hb"].assert_called_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "ERROR"


def test_pending_migrations_write_warn_heartbeat(stub):
    stub["pending"].return_value = [{"id": 7}]
    stub["migrations"].return_value = ["0046_x.sql"]
    ca.config_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "WARN"
    assert "deploy blocked" in stub["hb_row"].call_args.kwargs["error_summary"]


def test_open_circuit_writes_circuit_open_heartbeat(stub):
    stub["pending"].return_value = []
    stub["circuit"].return_value = True
    ca.config_once()
    assert stub["hb_row"].call_args.kwargs["status"] == "CIRCUIT_OPEN"


def test_heartbeat_row_failure_never_blocks_the_cycle(stub):
    stub["pending"].return_value = [{"id": 1}]
    stub["claim"].return_value = _row()
    stub["hb_row"].side_effect = RuntimeError("sheet down")
    out = ca.config_once()
    assert out.actuated == 1  # primary work unharmed
    assert any(
        c.kwargs.get("error_code") == "daemon_health_write_failed"
        for c in stub["log"].call_args_list
    )


def test_reporter_registration_metadata_is_self_provisioning_config(stub):
    r = ca._heartbeat_reporter
    assert r.daemon_name == "po_materials.config_actuator"
    assert r.workstream == "po_materials"
    assert r.interval_seconds == 120
    assert r.row_state_path.name == "heartbeat_row_ids.json"


def test_stale_reclaim_window_exceeds_ci_plus_worker_lease():
    """STALE_RECLAIM_S must be strictly greater than CI_TIMEOUT_S + the Worker's LEASE_TTL_S
    (1800) so a legitimately in-progress config publish is never reclaimed."""
    assert ca.STALE_RECLAIM_S > ca.CI_TIMEOUT_S + 1800


# ── stale-row sweep ───────────────────────────────────────────────────────────────


def test_sweep_reclaims_a_stale_row_and_fires_critical(stub):
    stub["pending"].return_value = []
    stub["stuck"].return_value = [
        {"id": 9, "status": "tested", "lease_owner": "deadmac:123", "artifact_key": "tax"},
    ]
    out = ca.config_once()
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
    out = ca.config_once()
    assert out.reclaimed == 0
    assert not _critical_fired(stub)


def test_sweep_fetch_failure_is_logged_not_fatal(stub):
    stub["stuck"].side_effect = ca.portal_client.PortalTransportError("boom")
    stub["pending"].return_value = []
    out = ca.config_once()
    assert out.reclaimed == 0
    stub["pending"].assert_called_once()  # the cycle continued past the sweep to the pull
    assert any(c.args and c.args[0] == ca.Severity.ERROR for c in stub["log"].call_args_list)


# ── _unstrand_if_needed (idle self-heal) ──────────────────────────────────────────


def test_unstrand_recovers_a_stray_branch(mocker):
    mocker.patch.object(ca, "_git", return_value="config/req-7-po_materials-tax\n")
    reset = mocker.patch.object(ca, "_reset_to_main")
    ca._unstrand_if_needed()
    reset.assert_called_once()


def test_unstrand_is_a_noop_on_main(mocker):
    mocker.patch.object(ca, "_git", return_value="main\n")
    reset = mocker.patch.object(ca, "_reset_to_main")
    ca._unstrand_if_needed()
    reset.assert_not_called()


def test_config_once_unstrands_before_actuating(stub):
    stub["pending"].return_value = []
    ca.config_once()
    stub["unstrand"].assert_called_once()


def test_config_once_halts_loud_when_unstrand_fails(stub):
    stub["unstrand"].side_effect = RuntimeError("git checkout main failed")
    out = ca.config_once()
    assert out.halted == "unstrand_failed"
    stub["pending"].assert_not_called()
    assert any(c.args and c.args[0] == ca.Severity.ERROR for c in stub["log"].call_args_list)


# ── _commit_test_merge branch naming + empty-diff backstop ────────────────────────


def test_commit_test_merge_branch_name_and_empty_diff_backstop(mocker):
    """Branch is config/req-{id}-{workstream}-{artifact}; a no-op apply (empty staged diff)
    raises a clean reason rather than a confusing `git commit` exit-1."""
    git = mocker.patch.object(ca, "_git")
    mocker.patch.object(ca, "_gh")
    # bare subprocess.run: branch -D / push --delete (no-op) then `diff --cached --quiet` → 0 (no diff)
    run = mocker.patch.object(ca.subprocess, "run")
    run.return_value.returncode = 0
    with pytest.raises(RuntimeError, match="no config change"):
        ca._commit_test_merge(7, "po_materials", "tax", "tax: ...")
    checkout = [c for c in git.call_args_list if c.args[:1] == ("checkout",)]
    assert any("config/req-7-po_materials-tax" in c.args for c in checkout)


# ── _wait_for_ci (the synchronous CI gate) ────────────────────────────────────────


def test_wait_for_ci_returns_when_clean(mocker):
    mocker.patch.object(ca, "_gh", return_value=json.dumps({"mergeStateStatus": "CLEAN", "statusCheckRollup": []}))
    ca._wait_for_ci("config/req-1-po_materials-tax")


def test_wait_for_ci_raises_on_a_failed_check(mocker):
    mocker.patch.object(ca, "_gh", return_value=json.dumps({
        "mergeStateStatus": "BLOCKED",
        "statusCheckRollup": [{"name": "test", "conclusion": "FAILURE"}],
    }))
    with pytest.raises(RuntimeError, match="CI failed"):
        ca._wait_for_ci("config/req-1-po_materials-tax")
