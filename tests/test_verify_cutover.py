"""Unit tests for scripts/verify_cutover.py — pass/fail plumbing + check units.

NO live calls: every Smartsheet / Keychain / subprocess touchpoint is
monkeypatched. The live gate run is operator-executed at cutover (§53); these
tests lock the harness contract (exit codes, --only/--skip, failure isolation)
and the per-check decision logic.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# scripts/ is not a Python package; use the same sys.path-insert idiom as
# tests/test_check_doctrine_drift.py so the module imports as the top-level
# `verify_cutover` (a `from scripts import …` would make mypy see the file
# under two module names — "found twice").
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_cutover as vc  # noqa: E402  — sys.path-driven import

OPTS = vc.Options()


def _spec(check_id: str, slug: str, outcome: vc.CheckOutcome) -> vc.CheckSpec:
    return vc.CheckSpec(check_id, slug, f"fake {slug}", lambda opts: outcome)


PASS = vc.CheckOutcome(passed=True, summary="ok")
FAIL = vc.CheckOutcome(passed=False, summary="bad", details="why it failed")


# ---- harness: exit codes, selection, isolation ---------------------------


def test_all_pass_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(
        vc, "CHECKS", (_spec("VC-01", "alpha", PASS), _spec("VC-02", "beta", PASS))
    )
    assert vc.main([]) == 0
    out = capsys.readouterr().out
    assert "[PASS] VC-01 alpha" in out
    assert "2 passed, 0 failed, 0 skipped" in out


def test_one_failure_exits_one_and_prints_details(monkeypatch, capsys):
    monkeypatch.setattr(
        vc, "CHECKS", (_spec("VC-01", "alpha", PASS), _spec("VC-02", "beta", FAIL))
    )
    assert vc.main([]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] VC-02 beta" in out
    assert "why it failed" in out
    assert "1 passed, 1 failed" in out


def test_only_selects_by_slug_and_flags_partial(monkeypatch, capsys):
    monkeypatch.setattr(
        vc, "CHECKS", (_spec("VC-01", "alpha", PASS), _spec("VC-02", "beta", FAIL))
    )
    assert vc.main(["--only", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "PARTIAL RUN" in out
    assert "beta" not in out.split("PARTIAL RUN")[1].split("verify_cutover:")[0]
    assert "1 skipped" in out


def test_skip_selects_by_check_id(monkeypatch, capsys):
    monkeypatch.setattr(
        vc, "CHECKS", (_spec("VC-01", "alpha", FAIL), _spec("VC-02", "beta", PASS))
    )
    assert vc.main(["--skip", "VC-01"]) == 0
    assert "1 skipped" in capsys.readouterr().out


def test_unknown_handle_exits_two(monkeypatch, capsys):
    monkeypatch.setattr(vc, "CHECKS", (_spec("VC-01", "alpha", PASS),))
    assert vc.main(["--only", "nope"]) == 2
    assert "unknown check" in capsys.readouterr().err


def test_check_exception_is_isolated_as_fail(monkeypatch, capsys):
    def boom(opts: vc.Options) -> vc.CheckOutcome:
        raise RuntimeError("sheet unreachable")

    monkeypatch.setattr(
        vc,
        "CHECKS",
        (vc.CheckSpec("VC-01", "alpha", "boom", boom), _spec("VC-02", "beta", PASS)),
    )
    assert vc.main([]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] VC-01 alpha" in out
    assert "RuntimeError" in out
    assert "[PASS] VC-02 beta" in out  # later checks still ran


def test_list_mode(capsys):
    assert vc.main(["--list"]) == 0
    out = capsys.readouterr().out
    for spec in vc.CHECKS:
        assert spec.check_id in out
        assert spec.slug in out


# ---- VC-01 keychain -------------------------------------------------------


def test_keychain_all_present(monkeypatch):
    monkeypatch.setattr(vc.keychain, "get_secret", lambda name: "x" * 12)
    outcome = vc._check_keychain(OPTS)
    assert outcome.passed
    assert f"{len(vc.REQUIRED_SECRETS)}/{len(vc.REQUIRED_SECRETS)}" in outcome.summary


def test_keychain_missing_named_but_value_never_leaked(monkeypatch):
    def fake(name: str) -> str:
        if name == "ITS_PORTAL_PO_TOKEN":
            raise vc.keychain.KeychainError("not found")
        return "s3cret-value"

    monkeypatch.setattr(vc.keychain, "get_secret", fake)
    outcome = vc._check_keychain(OPTS)
    assert not outcome.passed
    assert "ITS_PORTAL_PO_TOKEN" in outcome.details
    assert "s3cret-value" not in outcome.summary + outcome.details  # §54


# ---- VC-02 launchd --------------------------------------------------------


def _fake_launchctl(labels: set[str]) -> str:
    return "\n".join(f"123\t0\t{label}" for label in sorted(labels))


def test_launchd_exact_match_passes(monkeypatch):
    expected = vc._expected_labels()
    assert expected, "repo should ship org.solutionsmith.its.*.plist files"
    monkeypatch.setattr(vc, "_launchctl_list", lambda: _fake_launchctl(expected))
    assert vc._check_launchd(OPTS).passed


def test_launchd_missing_and_orphan_fail(monkeypatch):
    expected = sorted(vc._expected_labels())
    loaded = set(expected[1:]) | {"org.solutionsmith.its.ghost"}
    monkeypatch.setattr(vc, "_launchctl_list", lambda: _fake_launchctl(loaded))
    outcome = vc._check_launchd(OPTS)
    assert not outcome.passed
    assert expected[0] in outcome.details
    assert "ghost" in outcome.details


# ---- VC-03 config ---------------------------------------------------------


def _config_values(overrides: dict[str, str | None]) -> object:
    def fake(key: str, *, workstream: str) -> str | None:
        if key in overrides:
            value = overrides[key]
            if value == "MISSING":
                raise vc.SmartsheetNotFoundError(key)
            return value
        row = next(r for r in vc.CONFIG_ROWS if r.key == key)
        return "true" if row.requirement == "true" else "https://portal.example.com"

    return fake


def test_config_all_good_passes(monkeypatch):
    monkeypatch.setattr(vc.smartsheet_client, "get_setting", _config_values({}))
    assert vc._check_config(OPTS).passed


def test_config_missing_row_and_false_gate_fail(monkeypatch):
    monkeypatch.setattr(
        vc.smartsheet_client,
        "get_setting",
        _config_values(
            {
                "safety_reports.weekly_send.from_mailbox": "MISSING",
                "field_ops.fieldops_sync.sync_enabled": "false",
            }
        ),
    )
    outcome = vc._check_config(OPTS)
    assert not outcome.passed
    assert "row MISSING" in outcome.details
    assert "field_ops.fieldops_sync.sync_enabled" in outcome.details


def test_config_sandbox_value_fails_unless_allowed(monkeypatch):
    monkeypatch.setattr(
        vc.smartsheet_client,
        "get_setting",
        _config_values(
            {"safety_reports.portal.worker_base_url": "https://safety.evergreenmirror.com"}
        ),
    )
    assert not vc._check_config(OPTS).passed
    assert vc._check_config(vc.Options(allow_sandbox=True)).passed


# ---- VC-04 daemon-health --------------------------------------------------


def _health_row(name: str, *, enabled: bool, age_seconds: float | None, interval: float):
    heartbeat = (
        (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
        if age_seconds is not None
        else None
    )
    return {
        "Daemon Name": name,
        "Enabled": enabled,
        "Interval Seconds": interval,
        "Last Heartbeat": heartbeat,
    }


def test_daemon_health_fresh_rows_pass(monkeypatch):
    rows = [
        _health_row("safety_reports.portal_poll", enabled=True, age_seconds=30, interval=60),
        _health_row("scripts.watchdog", enabled=True, age_seconds=3600, interval=86400),
        _health_row("dark.daemon", enabled=False, age_seconds=10**7, interval=60),  # ignored
    ]
    monkeypatch.setattr(vc.smartsheet_client, "get_rows", lambda sheet_id: rows)
    outcome = vc._check_daemon_health(OPTS)
    assert outcome.passed
    assert "2 enabled" in outcome.summary


def test_daemon_health_stale_and_heartbeatless_fail(monkeypatch):
    rows = [
        _health_row("stale.daemon", enabled=True, age_seconds=500, interval=60),
        _health_row("silent.daemon", enabled=True, age_seconds=None, interval=60),
    ]
    monkeypatch.setattr(vc.smartsheet_client, "get_rows", lambda sheet_id: rows)
    outcome = vc._check_daemon_health(OPTS)
    assert not outcome.passed
    assert "stale.daemon" in outcome.details
    assert "silent.daemon" in outcome.details


def test_daemon_health_zero_enabled_rows_fail(monkeypatch):
    monkeypatch.setattr(vc.smartsheet_client, "get_rows", lambda sheet_id: [])
    assert not vc._check_daemon_health(OPTS).passed


# ---- VC-08 d1-migrations --------------------------------------------------


def _completed(rc: int, stdout: str, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["npx"], returncode=rc, stdout=stdout, stderr=stderr)


def test_d1_no_pending_passes(monkeypatch):
    monkeypatch.setattr(
        vc,
        "_run_wrangler_migrations_list",
        lambda: _completed(0, "✅ No migrations to apply!"),
    )
    assert vc._check_d1_migrations(OPTS).passed


def test_d1_pending_fails(monkeypatch):
    monkeypatch.setattr(
        vc,
        "_run_wrangler_migrations_list",
        lambda: _completed(0, "Migrations to be applied:\n0042_po_vendors.sql"),
    )
    outcome = vc._check_d1_migrations(OPTS)
    assert not outcome.passed
    assert "0042_po_vendors.sql" in outcome.details


def test_d1_transient_7403_retries_once_then_passes(monkeypatch):
    calls: list[int] = []

    def fake() -> subprocess.CompletedProcess[str]:
        calls.append(1)
        if len(calls) == 1:
            return _completed(1, "", "A request to the Cloudflare API failed. [code: 7403]")
        return _completed(0, "No migrations to apply")

    monkeypatch.setattr(vc, "_run_wrangler_migrations_list", fake)
    outcome = vc._check_d1_migrations(OPTS)
    assert outcome.passed
    assert len(calls) == 2


def test_d1_persistent_7403_fails_after_one_retry(monkeypatch):
    calls: list[int] = []

    def fake() -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return _completed(1, "", "[code: 7403]")

    monkeypatch.setattr(vc, "_run_wrangler_migrations_list", fake)
    assert not vc._check_d1_migrations(OPTS).passed
    assert len(calls) == 2  # exactly one retry, not an infinite loop


# ---- VC-09 heartbeat-url --------------------------------------------------


def test_heartbeat_url_https_passes(monkeypatch):
    monkeypatch.setattr(
        vc.smartsheet_client,
        "get_setting",
        lambda key, *, workstream: "https://heartbeat.uptimerobot.com/abc",
    )
    assert vc._check_heartbeat_url(OPTS).passed


@pytest.mark.parametrize("value", ["", "http://insecure.example.com"])
def test_heartbeat_url_blank_or_http_fails(monkeypatch, value):
    monkeypatch.setattr(
        vc.smartsheet_client, "get_setting", lambda key, *, workstream: value
    )
    assert not vc._check_heartbeat_url(OPTS).passed


def test_heartbeat_url_missing_row_fails(monkeypatch):
    def fake(key: str, *, workstream: str) -> str:
        raise vc.SmartsheetNotFoundError(key)

    monkeypatch.setattr(vc.smartsheet_client, "get_setting", fake)
    outcome = vc._check_heartbeat_url(OPTS)
    assert not outcome.passed
    assert "MISSING" in outcome.summary


# ---- registry sanity ------------------------------------------------------


def test_check_ids_unique_and_sequential():
    ids = [spec.check_id for spec in vc.CHECKS]
    assert len(ids) == len(set(ids))
    assert ids == [f"VC-{i:02d}" for i in range(1, len(ids) + 1)]


def test_required_secrets_cover_program_list():
    # The 11 non-Box + Box triplet + PO token (docs/2026-07-09_aug7_delivery_program.md WS4).
    assert len(vc.NON_BOX_SECRETS) == 11
    assert set(vc.BOX_SECRETS) == {
        "ITS_BOX_CLIENT_ID",
        "ITS_BOX_CLIENT_SECRET",
        "ITS_BOX_REFRESH_TOKEN",
    }
    assert "ITS_PORTAL_PO_TOKEN" in vc.REQUIRED_SECRETS
    assert len(vc.REQUIRED_SECRETS) == 15
