"""Change-operator-PIN verb (WS2) — the ACT-gate credential change.

Prove-it-bites: the new PIN is entered twice (typo guard), strength-floored,
write-only (never read back), the lockout throttle resets on success, a wrong
current PIN is denied with NO write, and the audit records no value. A LOST PIN
is NOT recoverable here (terminal-only). Keychain is mocked — no live PIN touched.
"""
from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from operator_dashboard.act import pin_change
from operator_dashboard.app import create_app

_CUR = "current-strong-pin"
_NEW = "brand-new-strong-pin"


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from operator_dashboard import auth

    monkeypatch.setattr(auth, "_FAIL_SLEEP_SECONDS", 0.0)
    auth.reset_pin_throttle()
    yield
    auth.reset_pin_throttle()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Mock keychain get (the current PIN) + set (capture) + error_log (audit)."""
    import shared.error_log as el
    import shared.keychain as kc

    state: dict[str, Any] = {"writes": [], "audits": []}
    monkeypatch.setattr(kc, "get_secret", lambda name, account=None: _CUR)
    monkeypatch.setattr(
        kc, "set_secret", lambda service, value, account=None: state["writes"].append((service, value))
    )
    monkeypatch.setattr(el, "log", lambda sev, script, msg, **kw: state["audits"].append((kw.get("error_code"), msg)))
    return state


# ------------------------------------------------------------------ validation ----
@pytest.mark.parametrize(
    ("new", "conf"),
    [
        ("longenough1", "different"),  # mismatch
        ("short", "short"),  # under MIN_PIN_LEN
        ("12345678", "12345678"),  # all-digits
        ("        ", "        "),  # all-whitespace (8 spaces): passes len + not-isdigit, still weak
        ("aaaaaaaa", "aaaaaaaa"),  # one character repeated: passes len, still weak
    ],
)
def test_new_pin_rejected_no_write(env: dict[str, Any], new: str, conf: str) -> None:
    out = pin_change.change_pin(new, conf, "op")
    assert out.kind == "rejected"
    assert env["writes"] == []  # nothing written on a rejected PIN


def test_change_writes_and_audits_no_value(env: dict[str, Any]) -> None:
    out = pin_change.change_pin(_NEW, _NEW, "op")
    assert out.kind == "changed"
    assert env["writes"] == [("ITS_OPERATOR_PIN", _NEW)]
    assert "config_pin_changed" in [c for c, _ in env["audits"]]
    assert all(_NEW not in msg for _, msg in env["audits"])  # the audit records NO value


def test_keychain_write_failure_is_error(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.keychain as kc

    def boom(service: str, value: str, account: str | None = None) -> None:
        raise RuntimeError("kc down")

    monkeypatch.setattr(kc, "set_secret", boom)
    out = pin_change.change_pin(_NEW, _NEW, "op")
    assert out.kind == "error"
    assert "kc down" not in out.message and _NEW not in out.message  # type only, never internals/value


def test_change_resets_lockout_throttle(env: dict[str, Any]) -> None:
    from operator_dashboard import auth

    for _ in range(auth._MAX_PIN_FAILS):
        auth._pin_throttle.record_failure()  # exhaust → locked out
    pin_change.change_pin(_NEW, _NEW, "op")
    auth._pin_throttle.check()  # raises if still locked; a successful change must clear it


def test_pin_change_is_write_only_never_reads_a_pin() -> None:
    # source-level: the module must NEVER read a PIN back (no get_secret)
    assert "get_secret" not in inspect.getsource(pin_change)


# ------------------------------------------------------------ HTTP integration ----
def test_http_wrong_current_pin_denied_no_write(env: dict[str, Any]) -> None:
    resp = TestClient(create_app()).post(
        "/act/pin/change",
        data={"pin": "WRONG", "confirm": "change-pin", "new_pin": _NEW, "confirm_pin": _NEW},
    )
    assert "outcome-rejected" in resp.text and "denied" in resp.text
    assert env["writes"] == []


def test_http_missing_change_pin_phrase_denied(env: dict[str, Any]) -> None:
    resp = TestClient(create_app()).post(
        "/act/pin/change",
        data={"pin": _CUR, "confirm": "", "new_pin": _NEW, "confirm_pin": _NEW},
    )
    assert "outcome-rejected" in resp.text
    assert env["writes"] == []


def test_http_new_pin_mismatch_rejected(env: dict[str, Any]) -> None:
    resp = TestClient(create_app()).post(
        "/act/pin/change",
        data={"pin": _CUR, "confirm": "change-pin", "new_pin": _NEW, "confirm_pin": "typo-different-pin"},
    )
    assert "outcome-rejected" in resp.text
    assert env["writes"] == []


def test_http_full_change_flow(env: dict[str, Any]) -> None:
    resp = TestClient(create_app()).post(
        "/act/pin/change",
        data={"pin": _CUR, "confirm": "change-pin", "new_pin": _NEW, "confirm_pin": _NEW},
    )
    assert "outcome-changed" in resp.text
    assert env["writes"] == [("ITS_OPERATOR_PIN", _NEW)]
