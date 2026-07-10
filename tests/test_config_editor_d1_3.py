"""D1-3 sensitive tier — Class-B weighted edits + Class-C secret rotation.

Prove-it-bites: a Class-B edit is refused on the Class-A route and requires the
elevated ceremony; a secret is never read back (source-level), rotation is
registry-bound, the Box refresh token is guided-only, a rotated value NEVER
leaks to the outcome/audit/argv; the Worker path puts the value on stdin and
dual-writes the mirror; a send-poller activation needs the attestation; Class-E
rows render read-only; the outcome is escaped.
"""
from __future__ import annotations

import inspect
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from operator_dashboard.act import config_write, secret_rotate
from operator_dashboard.act.config_write import apply_edit, apply_elevated_edit
from operator_dashboard.act.secret_rotate import rotate_secret
from operator_dashboard.act.validators import (
    ConfigValidationError,
    v_reviewer_chain,
    v_sender_list,
    v_state,
)
from operator_dashboard.app import create_app
from operator_dashboard.auth import PinError, verify_elevated


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from operator_dashboard import auth

    monkeypatch.setattr(auth, "_FAIL_SLEEP_SECONDS", 0.0)
    auth.reset_pin_throttle()
    yield
    auth.reset_pin_throttle()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import shared.error_log as el
    import shared.keychain as kc
    import shared.smartsheet_client as ss

    st: dict[str, Any] = {"rows": {}, "updates": [], "kc_writes": [], "audits": [], "pin": "1234"}

    def get_rows(sheet_id: int, *, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if filters:
            row = st["rows"].get((filters.get("Setting"), filters.get("Workstream")))
            return [row] if row else []
        return list(st["rows"].values())

    def update_rows(sheet_id: int, updates: list[dict[str, Any]]) -> None:
        st["updates"].extend(updates)

    def kc_get(service: str, account: str | None = None) -> str:
        return st["pin"]  # the ONLY get_secret in play is the PIN read

    def kc_set(service: str, value: str, account: str | None = None) -> None:
        st["kc_writes"].append((service, value))

    def log(sev: Any, script: str, msg: str, **kw: Any) -> None:
        st["audits"].append((str(sev), kw.get("error_code"), msg))

    monkeypatch.setattr(ss, "get_rows", get_rows)
    monkeypatch.setattr(ss, "update_rows", update_rows)
    monkeypatch.setattr(kc, "get_secret", kc_get)
    monkeypatch.setattr(kc, "set_secret", kc_set)
    monkeypatch.setattr(el, "log", log)
    return st


def _seed(st: dict[str, Any], setting: str, ws: str, value: str, row_id: int = 1) -> None:
    st["rows"][(setting, ws)] = {"_row_id": row_id, "Setting": setting, "Workstream": ws, "Value": value}


# ---------------------------------------------------------------- validators ----
def test_new_validators() -> None:
    assert v_state("active") == "ACTIVE"
    with pytest.raises(ConfigValidationError):
        v_state("HALT")
    assert v_sender_list('["a@b.com", "@b.com"]') == '["a@b.com", "@b.com"]'
    assert v_sender_list('["a@b.com\\n"]') == '["a@b.com"]'  # per-item strip
    with pytest.raises(ConfigValidationError):
        v_sender_list('["bad"]')
    chain = '{"primary":"a@b.com","secondary":"c@b.com","tertiary":"d@b.com","delay_to_secondary_hours":4,"delay_to_tertiary_hours":18}'
    assert '"primary"' in v_reviewer_chain(chain)
    with pytest.raises(ConfigValidationError):
        v_reviewer_chain('{"primary":"notanemail"}')


# ----------------------------------------------------------------- Class B ----
def test_class_b_refused_on_class_a_route(env: dict[str, Any]) -> None:
    _seed(env, "system.state", "global", "ACTIVE")
    out = apply_edit("system.state", "global", "PAUSED", "op")  # plain route
    assert out.kind == config_write.NOT_EDITABLE  # must use elevated
    assert env["updates"] == []


def test_class_b_applies_via_elevated(env: dict[str, Any]) -> None:
    _seed(env, "system.state", "global", "ACTIVE")
    out = apply_elevated_edit("system.state", "global", "PAUSED", "op")
    assert out.kind == config_write.APPLIED
    assert env["updates"] == [{"_row_id": 1, "Value": "PAUSED"}]


def test_tier_a_refused_on_elevated_route(env: dict[str, Any]) -> None:
    _seed(env, "circuit_breaker.enabled", "global", "true")
    out = apply_elevated_edit("circuit_breaker.enabled", "global", "false", "op")
    assert out.kind == config_write.NOT_EDITABLE
    assert env["updates"] == []


def test_verify_elevated_requires_pin_and_typed_confirm(env: dict[str, Any]) -> None:
    verify_elevated("1234", "system.state", expected="system.state")  # ok
    with pytest.raises(PinError):
        verify_elevated("1234", "wrong", expected="system.state")  # bad typed confirm
    with pytest.raises(PinError):
        verify_elevated("WRONGPIN", "system.state", expected="system.state")  # bad pin


def test_http_elevated_apply(env: dict[str, Any]) -> None:
    _seed(env, "safety_reports.weekly_send.from_mailbox", "safety_reports", "old@x.com")
    resp = TestClient(create_app()).post(
        "/act/config/elevated",
        data={
            "setting": "safety_reports.weekly_send.from_mailbox",
            "workstream": "safety_reports",
            "value": "new@x.com",
            "pin": "1234",
            "confirm": "safety_reports.weekly_send.from_mailbox",
        },
    )
    assert "outcome-applied" in resp.text
    assert env["updates"] == [{"_row_id": 1, "Value": "new@x.com"}]


def test_http_elevated_wrong_confirm_denied_no_write(env: dict[str, Any]) -> None:
    _seed(env, "system.state", "global", "ACTIVE")
    resp = TestClient(create_app()).post(
        "/act/config/elevated",
        data={"setting": "system.state", "workstream": "global", "value": "PAUSED", "pin": "1234", "confirm": "WRONG"},
    )
    assert "denied" in resp.text
    assert env["updates"] == []


def test_send_poller_activation_needs_attestation(env: dict[str, Any]) -> None:
    _seed(env, "po_materials.po_send.polling_enabled", "po_materials", "false", row_id=5)
    out = apply_elevated_edit("po_materials.po_send.polling_enabled", "po_materials", "true", "op", attested=False)
    assert out.kind == config_write.REJECTED  # no attest → refused
    assert env["updates"] == []
    out2 = apply_elevated_edit("po_materials.po_send.polling_enabled", "po_materials", "true", "op", attested=True)
    assert out2.kind == config_write.APPLIED
    assert env["updates"] == [{"_row_id": 5, "Value": "true"}]
    assert any(a[1] == "config_audit_elevated" for a in env["audits"])


def test_elevated_outcome_is_escaped(env: dict[str, Any]) -> None:
    _seed(env, "system.state", "global", "ACTIVE")
    resp = TestClient(create_app()).post(
        "/act/config/elevated",
        data={
            "setting": "system.state",
            "workstream": "global",
            "value": "<script>alert(1)</script>",
            "pin": "1234",
            "confirm": "system.state",
        },
    )
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text
    assert env["updates"] == []  # invalid state → rejected


# ----------------------------------------------------------------- Class C ----
def test_secret_rotate_module_never_reads_a_secret_back() -> None:
    # DoD: grep proves no code path READS a secret back (only set_secret writes).
    src = inspect.getsource(secret_rotate)
    assert ".get_secret" not in src and "get_secret(" not in src
    assert "set_secret" in src  # it does write-through


def test_rotate_unlisted_secret_refused(env: dict[str, Any]) -> None:
    out = rotate_secret("NOT_A_LISTED_SECRET", "x", "op")
    assert out.kind == "refused"
    assert env["kc_writes"] == []


def test_rotate_box_refresh_token_is_guided_never_written(env: dict[str, Any]) -> None:
    out = rotate_secret("ITS_BOX_REFRESH_TOKEN", "a-pasted-value", "op")
    assert out.kind == "guided"
    assert env["kc_writes"] == []  # never written from a pasted value
    assert "a-pasted-value" not in out.message


def test_rotate_keychain_write_through_no_value_leak(env: dict[str, Any]) -> None:
    out = rotate_secret("ITS_SMARTSHEET_TOKEN", "s3cr3t-value", "op")
    assert out.kind == "rotated"
    assert env["kc_writes"] == [("ITS_SMARTSHEET_TOKEN", "s3cr3t-value")]
    assert "s3cr3t-value" not in out.message  # never in the outcome
    audit = [a for a in env["audits"] if a[1] == "config_secret_rotated"]
    assert audit and all("s3cr3t-value" not in a[2] for a in audit)  # never in the audit


def test_rotate_worker_value_on_stdin_and_mirror(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    calls: dict[str, Any] = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv: Any, **kw: Any) -> Any:
        calls["argv"] = argv
        calls["input"] = kw.get("input")
        return _Proc()

    # hermetic: ~/its/safety_portal may not exist in CI — point at a real temp dir
    monkeypatch.setattr(secret_rotate, "_SAFETY_PORTAL", tmp_path)
    monkeypatch.setattr(secret_rotate.subprocess, "run", fake_run)
    out = rotate_secret("PORTAL_PO_API_TOKEN", "newtok", "op")
    assert out.kind == "rotated"
    assert calls["argv"][:4] == ["npx", "wrangler", "secret", "put"]
    assert calls["input"] == "newtok"  # value on STDIN
    assert "newtok" not in calls["argv"]  # value NEVER on argv
    assert ("ITS_PORTAL_PO_TOKEN", "newtok") in env["kc_writes"]  # byte-equal mirror written
    assert "newtok" not in out.message


def test_http_rotate_flow_no_value_echo(env: dict[str, Any]) -> None:
    resp = TestClient(create_app()).post(
        "/act/secret/rotate",
        data={"key": "ITS_RESEND_API_KEY", "value": "topsecret", "pin": "1234", "confirm": "ITS_RESEND_API_KEY"},
    )
    assert "outcome-rotated" in resp.text
    assert ("ITS_RESEND_API_KEY", "topsecret") in env["kc_writes"]
    assert "topsecret" not in resp.text  # value never echoed to the browser


def test_http_rotate_wrong_confirm_denied(env: dict[str, Any]) -> None:
    resp = TestClient(create_app()).post(
        "/act/secret/rotate",
        data={"key": "ITS_RESEND_API_KEY", "value": "topsecret", "pin": "1234", "confirm": "WRONG"},
    )
    assert "denied" in resp.text
    assert env["kc_writes"] == []


# ----------------------------------------------------------------- Class E ----
def test_class_e_rows_render_read_only_no_edit_control(env: dict[str, Any]) -> None:
    _seed(env, "safety_reports.external_send_gate", "safety_reports", "MANUAL")
    resp = TestClient(create_app()).get("/config")
    assert resp.status_code == 200
    assert "External Send Gate" in resp.text
    # NO edit form targets external_send_gate (no hidden setting input for it)
    assert 'value="safety_reports.external_send_gate"' not in resp.text


# ---------------------------------------------------- review-fix regressions ----
def test_config_actuator_activation_requires_attestation(env: dict[str, Any]) -> None:
    # review fix: the code-committing/deploying daemon must attest go-live to
    # activate — same bar as a send-poller, not lower.
    _seed(env, "po_materials.config_actuator.polling_enabled", "po_materials", "false", row_id=8)
    out = apply_elevated_edit("po_materials.config_actuator.polling_enabled", "po_materials", "true", "op", attested=False)
    assert out.kind == config_write.REJECTED
    assert env["updates"] == []
    out2 = apply_elevated_edit("po_materials.config_actuator.polling_enabled", "po_materials", "true", "op", attested=True)
    assert out2.kind == config_write.APPLIED
    assert env["updates"] == [{"_row_id": 8, "Value": "true"}]


def test_reviewer_chain_canonicalizes_and_drops_extras() -> None:
    out = v_reviewer_chain(
        '{"primary":"  a@b.com  ","secondary":"c@b.com","tertiary":"d@b.com",'
        '"delay_to_secondary_hours":4,"delay_to_tertiary_hours":18,"EXTRA":"x"}'
    )
    assert '"a@b.com"' in out and "  a@b.com  " not in out  # stripped
    assert "EXTRA" not in out  # extra top-level key dropped


def test_worker_mirror_desync_is_audited(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    import shared.keychain as kc

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(secret_rotate, "_SAFETY_PORTAL", tmp_path)  # hermetic (CI has no ~/its/safety_portal)
    monkeypatch.setattr(secret_rotate.subprocess, "run", lambda argv, **kw: _Proc())

    def failing_set(service: str, value: str, account: str | None = None) -> None:
        if service == "ITS_PORTAL_PO_TOKEN":  # the mirror write fails
            raise RuntimeError("keychain locked")

    monkeypatch.setattr(kc, "set_secret", failing_set)
    out = rotate_secret("PORTAL_PO_API_TOKEN", "newtok", "op")
    assert out.kind == "error"
    # the desync is durably audited (distinct code), and never leaks the value
    desync = [a for a in env["audits"] if a[1] == "config_secret_mirror_desync"]
    assert desync and all("newtok" not in a[2] for a in desync)
