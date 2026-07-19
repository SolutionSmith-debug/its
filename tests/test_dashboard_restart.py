"""Restart-dashboard ACT verb (DASH-12) — the pre-authorized self-restart.

Prove-it-bites: the spawn is DETACHED (start_new_session=True + closed stdio —
the child must survive this process's own SIGTERM), the audit row is written
BEFORE the spawn (the process dies moments later, so a post-spawn audit could
be lost), the command is restart-only (kickstart -k on the dashboard's own
fixed label — never a pull/deploy, never another label), a spawn failure is an
honest error outcome, and the HTTP route requires the full elevated ceremony.
subprocess.Popen is MOCKED — nothing is ever really restarted in tests.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from operator_dashboard.act import dashboard_ops
from operator_dashboard.app import create_app

_PIN = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _reset_pin_throttle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from operator_dashboard import auth

    monkeypatch.setattr(auth, "_FAIL_SLEEP_SECONDS", 0.0)
    auth.reset_pin_throttle()
    yield
    auth.reset_pin_throttle()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record Popen calls + error_log audits in arrival order (the `timeline`
    list proves audit-before-spawn)."""
    import shared.error_log as el
    import shared.keychain as kc

    state: dict[str, Any] = {"popen": [], "audits": [], "timeline": [], "raise": False}

    class _Proc:
        pid = 4242

    def fake_popen(argv: list[str], **kw: Any) -> _Proc:
        if state["raise"]:
            raise OSError("spawn refused")
        state["popen"].append((argv, kw))
        state["timeline"].append("popen")
        return _Proc()

    def log(sev: Any, script: str, msg: str, **kw: Any) -> None:
        state["audits"].append((str(sev), kw.get("error_code"), msg))
        state["timeline"].append("audit")

    monkeypatch.setattr(dashboard_ops.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(el, "log", log)
    monkeypatch.setattr(kc, "get_secret", lambda name, account=None: _PIN)
    return state


def test_restart_spawns_detached_kickstart(env: dict[str, Any]) -> None:
    out = dashboard_ops.restart_dashboard("op")
    assert out.kind == "ok"
    (argv, kw), = env["popen"]
    assert argv[0] == "/bin/sh" and argv[1] == "-c"
    cmd = argv[2]
    assert "launchctl kickstart -k" in cmd
    assert dashboard_ops.DASHBOARD_LABEL in cmd
    assert cmd.startswith("sleep ")  # the response must flush before the kill lands
    # Detachment is the whole point — a same-session child dies with its parent.
    assert kw["start_new_session"] is True
    assert kw["stdin"] == dashboard_ops.subprocess.DEVNULL
    assert kw["stdout"] == dashboard_ops.subprocess.DEVNULL
    assert kw["stderr"] == dashboard_ops.subprocess.DEVNULL


def test_restart_is_restart_only(env: dict[str, Any]) -> None:
    dashboard_ops.restart_dashboard("op")
    (argv, _), = env["popen"]
    cmd = argv[2]
    for forbidden in ("git", "pull", "deploy", "wrangler", "install.sh"):
        assert forbidden not in cmd


def test_audit_written_before_spawn(env: dict[str, Any]) -> None:
    dashboard_ops.restart_dashboard("op")
    assert env["timeline"] == ["audit", "popen"]
    sev, code, msg = env["audits"][0]
    assert code == "dashboard_restart_requested"
    assert "op" in msg


def test_spawn_failure_is_honest_error(env: dict[str, Any]) -> None:
    env["raise"] = True
    out = dashboard_ops.restart_dashboard("op")
    assert out.kind == "error"
    assert "OSError" in out.message


def test_http_route_requires_elevated_ceremony(env: dict[str, Any]) -> None:
    client = TestClient(create_app())
    # Wrong confirmation phrase → denied, and NOTHING spawned.
    r = client.post(
        "/act/dashboard/restart",
        data={"pin": _PIN, "confirm": "restart"},
        headers={"origin": "http://127.0.0.1:8484"},
    )
    assert r.status_code == 200 and "denied" in r.text
    assert env["popen"] == []
    # Correct ceremony → spawned.
    r = client.post(
        "/act/dashboard/restart",
        data={"pin": _PIN, "confirm": "restart-dashboard"},
        headers={"origin": "http://127.0.0.1:8484"},
    )
    assert r.status_code == 200 and "restart scheduled" in r.text
    assert len(env["popen"]) == 1


def test_http_route_refuses_bad_origin(env: dict[str, Any]) -> None:
    client = TestClient(create_app())
    r = client.post(
        "/act/dashboard/restart",
        data={"pin": _PIN, "confirm": "restart-dashboard"},
        headers={"origin": "https://evil.example"},
    )
    assert r.status_code == 200 and "refused" in r.text
    assert env["popen"] == []


def test_daemon_control_allowlist_still_excludes_dashboard() -> None:
    # The new verb must NOT have widened the general control surface: the
    # dashboard label stays excluded from controllable_labels() (the deliberate
    # exception lives ONLY in the dedicated restart verb).
    from operator_dashboard.act import daemon_ops

    assert dashboard_ops.DASHBOARD_LABEL == daemon_ops.DASHBOARD_LABEL
    assert daemon_ops.DASHBOARD_LABEL not in daemon_ops.controllable_labels()
