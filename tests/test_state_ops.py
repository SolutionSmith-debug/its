"""Circuit-breaker clear ACT verb (WS2 Block 3).

Behavioral prove-it-bites: clearing an OPEN breaker makes `circuit_breaker.is_open()`
read False (the real goal), a clear is audited, and an already-CLOSED breaker
reports noop. State file is a tmp path (hermetic). The lock-clear is intentionally
NOT built (state_io flock model has no stale artifact) — nothing to test there.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from operator_dashboard.act import state_ops


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from operator_dashboard import auth

    monkeypatch.setattr(auth, "_FAIL_SLEEP_SECONDS", 0.0)
    auth.reset_pin_throttle()
    yield
    auth.reset_pin_throttle()


@pytest.fixture
def brk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Point circuit_breaker.STATE_FILE at a tmp file + capture audit codes."""
    import shared.circuit_breaker as cb
    import shared.error_log as el

    state_file = tmp_path / "circuit_breaker.json"
    monkeypatch.setattr(cb, "STATE_FILE", state_file)
    audits: list[str | None] = []
    monkeypatch.setattr(el, "log", lambda sev, script, msg, **kw: audits.append(kw.get("error_code")))
    return {"cb": cb, "state_file": state_file, "audits": audits}


def _seed_open(cb: Any, path: Path) -> None:
    now = datetime.now(UTC).isoformat()
    path.write_text(
        json.dumps(
            {"state": cb.OPEN, "consecutive_failures": 9, "opened_at": now, "first_opened_at": now}
        )
    )


def test_clear_resets_open_breaker_to_closed(brk: dict[str, Any]) -> None:
    cb, path = brk["cb"], brk["state_file"]
    _seed_open(cb, path)
    assert json.loads(path.read_text())["state"] == cb.OPEN  # precondition (file-level)
    out = state_ops.clear_circuit_breaker("op")
    assert out.kind == "ok"
    assert json.loads(path.read_text())["state"] == cb.CLOSED  # behavioral proof: reset to CLOSED
    assert "config_breaker_cleared" in brk["audits"]


def test_clear_is_noop_when_already_closed(brk: dict[str, Any]) -> None:
    # no state file → prior None → noop, but the reset is still written + audited
    out = state_ops.clear_circuit_breaker("op")
    assert out.kind == "noop"
    assert json.loads(brk["state_file"].read_text())["state"] == brk["cb"].CLOSED
    assert "config_breaker_cleared" in brk["audits"]


def test_clear_written_state_matches_blank_state_shape(brk: dict[str, Any]) -> None:
    # drift-guard: the persisted state is exactly the breaker's own blank state, so
    # a future schema change to _blank_state is respected (no duplicated shape here).
    cb, path = brk["cb"], brk["state_file"]
    _seed_open(cb, path)
    state_ops.clear_circuit_breaker("op")
    assert json.loads(path.read_text()) == cb._blank_state()
