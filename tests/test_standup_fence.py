"""The stand-up ACT fence + the daemon-down dashboard exemption.

The dashboard stays UP through a tenant wipe/rebuild for observability
(exempt from the tools' daemon-down guards), so its Class-A/B/C ACT verbs must
be unreachable while the tenant is half-provisioned. Prove-the-control-bites:
a FRESH marker refuses both auth ceremonies before any PIN handling; a STALE
or corrupt marker fails OPEN (a crashed stand-up must never brick the
dashboard); the guards pass with only the dashboard loaded and still refuse
on any writing daemon.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

from operator_dashboard import auth

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import standup  # noqa: E402
import wipe_tenant as wipe  # noqa: E402


def _marker(tmp_path: Path, *, age_hours: float, body: str | None = None) -> Path:
    p = tmp_path / "standup_in_progress.json"
    if body is not None:
        p.write_text(body, encoding="utf-8")
        return p
    started = dt.datetime.now(dt.UTC) - dt.timedelta(hours=age_hours)
    p.write_text(json.dumps({"started_at_utc": started.isoformat(),
                             "tool": "wipe_tenant"}), encoding="utf-8")
    return p


# ---- the fence bites -------------------------------------------------------


def test_fresh_marker_fences_verify_pin_before_any_pin_handling(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "STANDUP_MARKER_PATH", _marker(tmp_path, age_hours=0.1))

    def _boom(*a: object, **k: object) -> None:
        raise AssertionError("fence must refuse BEFORE the throttle/PIN path")

    monkeypatch.setattr(auth, "_verify_pin_throttled", _boom)
    with pytest.raises(auth.StandupFenceError, match="stand-up in progress"):
        auth.verify_pin("whatever")


def test_fresh_marker_fences_verify_elevated(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "STANDUP_MARKER_PATH", _marker(tmp_path, age_hours=0.1))
    with pytest.raises(auth.StandupFenceError):
        auth.verify_elevated("pin", "confirm-me", expected="confirm-me")


def test_fence_error_is_a_pin_error_so_routers_render_it() -> None:
    assert issubclass(auth.StandupFenceError, auth.PinError)


# ---- fail-open paths -------------------------------------------------------


def test_stale_marker_fails_open(tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth, "STANDUP_MARKER_PATH",
        _marker(tmp_path, age_hours=auth.STANDUP_MARKER_MAX_AGE_HOURS + 1))
    assert auth._standup_block_reason() is None


def test_corrupt_and_absent_markers_fail_open(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "STANDUP_MARKER_PATH",
                        _marker(tmp_path, age_hours=0, body="{not json"))
    assert auth._standup_block_reason() is None
    monkeypatch.setattr(auth, "STANDUP_MARKER_PATH", tmp_path / "missing.json")
    assert auth._standup_block_reason() is None


# ---- daemon-down guards: dashboard exempt, writers still refused -----------


def test_wipe_guard_passes_with_only_dashboard_loaded(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wipe, "_loaded_its_daemons",
                        lambda: ["org.solutionsmith.its.dashboard"])
    wipe.require_daemons_down()  # no raise


def test_wipe_guard_still_refuses_a_writing_daemon(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wipe, "_loaded_its_daemons",
                        lambda: ["org.solutionsmith.its.dashboard",
                                 "org.solutionsmith.its.portal-poll"])
    with pytest.raises(wipe.WipeRefusedError):
        wipe.require_daemons_down()


def test_standup_guard_mirrors_the_exemption(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def fake_run(*a: object, **k: object) -> object:
        class R:
            stdout = ("123\t0\torg.solutionsmith.its.dashboard\n")
        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert standup._require_daemons_down() is True


# ---- marker lifecycle ------------------------------------------------------


def test_standup_marker_write_and_clear(tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "standup_in_progress.json"
    monkeypatch.setattr(standup, "STANDUP_MARKER_PATH", marker)
    standup._write_standup_marker()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["tool"] == "standup" and "started_at_utc" in data
    standup._clear_standup_marker()
    assert not marker.exists()
    standup._clear_standup_marker()  # idempotent on absence


def test_marker_paths_agree_across_all_three_surfaces() -> None:
    """auth (reader), wipe (setter), standup (setter+clearer) must point at the
    SAME file or the fence silently never engages.

    conftest's autouse live-state sweep redirects auth's constant (first-party
    package attr under ~/its/state/) to tmp while preserving RELATIVE topology,
    and the bare scripts/migrations modules are outside the sweep — so compare
    topology (state/<name> tail), plus exact equality between the two unswept
    setters."""
    assert wipe.STANDUP_MARKER_PATH == standup.STANDUP_MARKER_PATH
    for path in (auth.STANDUP_MARKER_PATH, wipe.STANDUP_MARKER_PATH,
                 standup.STANDUP_MARKER_PATH):
        assert path.parts[-2:] == ("state", "standup_in_progress.json"), path


def test_wipe_commit_sets_marker_and_standup_completion_clears_it() -> None:
    """Source-level wiring check: the write sits in wipe's --commit path and the
    clear sits after the stage loop (success only — an abort stays fenced)."""
    wipe_src = (Path(wipe.__file__)).read_text(encoding="utf-8")
    assert "atomic_write_json(STANDUP_MARKER_PATH" in wipe_src
    standup_src = (Path(standup.__file__)).read_text(encoding="utf-8")
    assert "_write_standup_marker()" in standup_src
    assert "_clear_standup_marker()" in standup_src
