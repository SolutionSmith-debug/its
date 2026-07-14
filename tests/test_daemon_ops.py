"""Interval-daemon ACT verb (WS2 D1-3b) — the launchctl-reinstall edit.

Prove-it-bites: the label allowlist refuses non-interval / non-ITS labels, the
interval is bounds-enforced, a missing ITS_Config row is refused, ITS_Config +
the plist reinstall happen together, a reinstall failure is audited as a desync
(not silent), and the HTTP route requires the elevated ceremony. subprocess +
install.sh are MOCKED (install.sh is a real tmp file so is_file() is True — the
CI-hermetic pattern, since ~/its may be absent on the runner). Nothing loaded live.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from operator_dashboard.act import daemon_ops
from operator_dashboard.app import create_app

_PO_POLL = "org.solutionsmith.its.po-poll"
_PO_POLL_KEY = "po_materials.po_poll.poll_interval_seconds"


@pytest.fixture(autouse=True)
def _reset_pin_throttle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from operator_dashboard import auth

    monkeypatch.setattr(auth, "_FAIL_SLEEP_SECONDS", 0.0)
    auth.reset_pin_throttle()
    yield
    auth.reset_pin_throttle()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Mock ITS_Config read/write + error_log audit + the install.sh subprocess.
    `rows` maps (Setting, Workstream)->row; `updates` records writes; `audits`
    records error_log codes; `install_calls` records install.sh argv; `rc` is the
    install.sh exit code the fake returns. install.sh is a real tmp file so
    is_file() is True (hermetic — ~/its may be absent on the CI runner)."""
    import shared.error_log as el
    import shared.smartsheet_client as ss

    state: dict[str, Any] = {"rows": {}, "updates": [], "audits": [], "install_calls": [], "rc": 0}

    def get_rows(sheet_id: int, *, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if filters:
            row = state["rows"].get((filters.get("Setting"), filters.get("Workstream")))
            return [row] if row else []
        return list(state["rows"].values())

    def update_rows(sheet_id: int, updates: list[dict[str, Any]]) -> None:
        state["updates"].extend(updates)

    def log(sev: Any, script: str, msg: str, **kw: Any) -> None:
        state["audits"].append((str(sev), kw.get("error_code")))

    class _Proc:
        def __init__(self, rc: int) -> None:
            self.returncode = rc
            self.stderr = "" if rc == 0 else "boom"

    def fake_run(argv: list[str], **kw: Any) -> _Proc:
        state["install_calls"].append(argv)
        return _Proc(state["rc"])

    fake_sh = tmp_path / "install.sh"
    fake_sh.write_text("#!/bin/sh\n")
    monkeypatch.setattr(ss, "get_rows", get_rows)
    monkeypatch.setattr(ss, "update_rows", update_rows)
    monkeypatch.setattr(el, "log", log)
    monkeypatch.setattr(daemon_ops, "_INSTALL_SH", fake_sh)
    monkeypatch.setattr(daemon_ops.subprocess, "run", fake_run)
    return state


def _seed(state: dict[str, Any], setting: str, ws: str, value: str, row_id: int = 1) -> None:
    state["rows"][(setting, ws)] = {"_row_id": row_id, "Setting": setting, "Workstream": ws, "Value": value}


# ---------------------------------------------------------------- allowlist ----
def test_label_allowlist_refuses_non_interval_and_non_its(env: dict[str, Any]) -> None:
    # the dashboard itself, a calendar daemon, a non-ITS label, and a bare name
    for bad in ("org.solutionsmith.its.dashboard", "org.solutionsmith.its.watchdog", "com.evil.daemon", "po-poll"):
        out = daemon_ops.edit_interval(bad, "120", "op")
        assert out.kind == "not_editable", bad
    assert env["updates"] == [] and env["install_calls"] == []


def test_all_eight_interval_daemons_registered() -> None:
    assert len(daemon_ops.INTERVAL_DAEMONS) == 8
    assert daemon_ops.is_interval_daemon(_PO_POLL)
    assert not daemon_ops.is_interval_daemon("org.solutionsmith.its.dashboard")


# ------------------------------------------------------------------- bounds ----
@pytest.mark.parametrize(
    "bad", ["5", "0", "-30", "1.5", "abc", "", "９０", str(daemon_ops.MAX_INTERVAL + 1), "9" * 8]
)
def test_interval_bounds_rejected_no_write(env: dict[str, Any], bad: str) -> None:
    _seed(env, _PO_POLL_KEY, "po_materials", "90", row_id=5)
    out = daemon_ops.edit_interval(_PO_POLL, bad, "op")
    assert out.kind == "rejected"
    assert env["updates"] == [] and env["install_calls"] == []


# -------------------------------------------------------------------- apply ----
def test_edit_applies_config_then_reinstall(env: dict[str, Any]) -> None:
    _seed(env, _PO_POLL_KEY, "po_materials", "90", row_id=5)
    out = daemon_ops.edit_interval(_PO_POLL, "120", "op")
    assert out.kind == "applied"
    assert env["updates"] == [{"_row_id": 5, "Value": "120"}]
    # install.sh invoked with the EXPLICIT interval on argv (load <label> <interval>)
    assert env["install_calls"] and env["install_calls"][0][1:] == ["load", _PO_POLL, "120"]
    assert any(code == "config_interval_edited" for _, code in env["audits"])


def test_missing_row_refused_no_write(env: dict[str, Any]) -> None:
    out = daemon_ops.edit_interval(_PO_POLL, "120", "op")  # no seeded row
    assert out.kind == "not_editable"
    assert env["updates"] == [] and env["install_calls"] == []


def test_noop_when_unchanged(env: dict[str, Any]) -> None:
    _seed(env, _PO_POLL_KEY, "po_materials", "120", row_id=5)
    out = daemon_ops.edit_interval(_PO_POLL, "120", "op")
    assert out.kind == "noop"
    assert env["updates"] == [] and env["install_calls"] == []


def test_reinstall_failure_audits_desync(env: dict[str, Any]) -> None:
    _seed(env, _PO_POLL_KEY, "po_materials", "90", row_id=5)
    env["rc"] = 3  # install.sh fails AFTER the ITS_Config write
    out = daemon_ops.edit_interval(_PO_POLL, "120", "op")
    assert out.kind == "error"
    assert env["updates"] == [{"_row_id": 5, "Value": "120"}]  # config already written
    assert any(code == "config_interval_reinstall_desync" for _, code in env["audits"])


def test_read_interval_state(env: dict[str, Any]) -> None:
    _seed(env, _PO_POLL_KEY, "po_materials", "90", row_id=5)
    state = {d["label"]: d for d in daemon_ops.read_interval_state()}
    assert len(state) == 8
    assert state[_PO_POLL]["value"] == "90" and state[_PO_POLL]["present"]
    assert state[_PO_POLL]["slug"] == "org-solutionsmith-its-po-poll"  # CSS-safe htmx id
    assert not state["org.solutionsmith.its.weekly-send"]["present"]  # unseeded → False


# ------------------------------------------------------------ HTTP integration ----
def _client(monkeypatch: pytest.MonkeyPatch, pin: str = "1234") -> TestClient:
    import shared.keychain as kc

    monkeypatch.setattr(kc, "get_secret", lambda name, account=None: pin)
    return TestClient(create_app())


def test_http_interval_requires_elevated_confirm(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(env, _PO_POLL_KEY, "po_materials", "90", row_id=5)
    client = _client(monkeypatch)
    # missing typed confirmation → denied, NO write / NO reinstall
    resp = client.post("/act/daemon/interval", data={"label": _PO_POLL, "interval": "120", "pin": "1234"})
    assert "outcome-rejected" in resp.text
    assert env["updates"] == [] and env["install_calls"] == []
    # correct elevated ceremony (re-PIN + typed label) → applied
    resp2 = client.post(
        "/act/daemon/interval",
        data={"label": _PO_POLL, "interval": "120", "pin": "1234", "confirm": _PO_POLL},
    )
    assert "outcome-applied" in resp2.text
    assert env["updates"] == [{"_row_id": 5, "Value": "120"}]


def test_http_interval_label_allowlist_refused(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    resp = client.post(
        "/act/daemon/interval",
        data={
            "label": "org.solutionsmith.its.dashboard",
            "interval": "120",
            "pin": "1234",
            "confirm": "org.solutionsmith.its.dashboard",
        },
    )
    assert "outcome-not_editable" in resp.text
    assert env["updates"] == [] and env["install_calls"] == []
