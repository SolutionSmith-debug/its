"""Tests for shared.required_config — #336 observable ITS_Config resolution.

The EVIDENCE test that binds the §52 narrated_controls ledger entry
``required_config_observable_resolution`` (status: enforced): every branch of
``resolve_and_log`` is asserted RED-lightable — a resolved key logs INFO with its
source, a MISSING row WARNs distinctly (``config_row_missing``), a blank row and a
transient error are distinguishable, and the whole pass is fail-open (never
raises). Modelled on tests/test_kill_switch.py (patch the get_setting + log seams).
"""
from __future__ import annotations

import pytest

from shared.error_log import Severity
from shared.required_config import ConfigKey, resolve_and_log
from shared.smartsheet_client import SmartsheetError, SmartsheetNotFoundError


@pytest.fixture
def mock_log(mocker):
    return mocker.patch("shared.required_config.log")


@pytest.fixture
def mock_get_setting(mocker):
    return mocker.patch("shared.required_config.smartsheet_client.get_setting")


# ---- ConfigKey validation -------------------------------------------------


def test_configkey_rejects_invalid_kind():
    with pytest.raises(ValueError, match="kind must be one of"):
        ConfigKey("x.y", "global", 0, "integer")  # not a valid coercion kind


# ---- resolved from ITS_Config → INFO naming the source -------------------


def test_resolved_string_logs_info_source_its_config(mock_get_setting, mock_log):
    mock_get_setting.return_value = "hello"
    out = resolve_and_log("test.script", [ConfigKey("a.b", "global", "fallback", "str")])
    assert out == {"a.b": "hello"}
    severity, script, message = mock_log.call_args.args
    assert severity is Severity.INFO
    assert script == "test.script"
    assert "a.b" in message and "global" in message
    assert "source: ITS_Config" in message


@pytest.mark.parametrize(
    "raw,kind,expected",
    [
        ("true", "bool", True),
        ("no", "bool", False),
        ("on", "bool", True),
        ("42", "int", 42),
        ("3.5", "float", 3.5),
        ("  7 ", "int", 7),  # whitespace tolerated
    ],
)
def test_coercion(mock_get_setting, mock_log, raw, kind, expected):
    mock_get_setting.return_value = raw
    out = resolve_and_log("t", [ConfigKey("k", "global", None, kind)])
    assert out["k"] == expected


def test_malformed_int_falls_back_to_default(mock_get_setting, mock_log):
    mock_get_setting.return_value = "not-a-number"
    out = resolve_and_log("t", [ConfigKey("k", "global", 99, "int")])
    assert out["k"] == 99


# ---- MISSING row → the #336 distinct WARN --------------------------------


def test_missing_row_warns_config_row_missing(mock_get_setting, mock_log):
    mock_get_setting.side_effect = SmartsheetNotFoundError("no row")
    out = resolve_and_log(
        "test.script", [ConfigKey("gate.enabled", "field_ops", False, "bool")]
    )
    assert out == {"gate.enabled": False}  # → the declared default
    mock_log.assert_called_once()
    severity, script, message = mock_log.call_args.args
    assert severity is Severity.WARN  # ← distinct from a resolved key's INFO
    assert mock_log.call_args.kwargs["error_code"] == "config_row_missing"
    assert "gate.enabled" in message and "field_ops" in message
    assert "NO ROW" in message


# ---- blank row → INFO, source default ------------------------------------


def test_blank_row_logs_info_source_default(mock_get_setting, mock_log):
    mock_get_setting.return_value = None  # row present, Value cell blank
    out = resolve_and_log("t", [ConfigKey("k", "global", "dflt", "str")])
    assert out == {"k": "dflt"}
    severity, _, message = mock_log.call_args.args
    assert severity is Severity.INFO
    assert "source: default" in message and "blank" in message


# ---- transient / unexpected error → WARN config_read_error, fail-open -----


def test_transient_error_warns_config_read_error(mock_get_setting, mock_log):
    mock_get_setting.side_effect = SmartsheetError("circuit open")
    out = resolve_and_log("t", [ConfigKey("k", "global", "dflt", "str")])
    assert out == {"k": "dflt"}
    severity, _, _ = mock_log.call_args.args
    assert severity is Severity.WARN
    assert mock_log.call_args.kwargs["error_code"] == "config_read_error"


def test_unexpected_error_never_raises(mock_get_setting, mock_log):
    mock_get_setting.side_effect = RuntimeError("boom")  # not a SmartsheetError
    out = resolve_and_log("t", [ConfigKey("k", "global", 5, "int")])  # must NOT propagate
    assert out == {"k": 5}
    assert mock_log.call_args.args[0] is Severity.WARN


def test_one_key_failing_does_not_block_the_others(mock_get_setting, mock_log):
    def _side(setting, *, workstream):
        if setting == "bad":
            raise SmartsheetNotFoundError("no")
        return "ok"

    mock_get_setting.side_effect = _side
    out = resolve_and_log(
        "t",
        [
            ConfigKey("good1", "global", "d1", "str"),
            ConfigKey("bad", "global", "d2", "str"),
            ConfigKey("good2", "global", "d3", "str"),
        ],
    )
    assert out == {"good1": "ok", "bad": "d2", "good2": "ok"}


# ---- roster: real daemons wire it (the completeness the review demanded) --


def test_a_daemon_declares_a_valid_required_config():
    from safety_reports import weekly_send

    rc = weekly_send.REQUIRED_CONFIG
    assert isinstance(rc, list) and len(rc) >= 1
    assert all(isinstance(k, ConfigKey) for k in rc)
    assert all(
        isinstance(k.setting, str) and k.setting and isinstance(k.workstream, str) and k.workstream
        for k in rc
    )


def test_send_poll_daemon_declares_from_mailbox_on_the_production_path():
    # #336-fix: from_mailbox is read by send_one_row on EVERY automated dispatch, so the POLL
    # daemon (the production driver) must declare it — not only weekly_send.main (the debug path).
    from safety_reports import weekly_send, weekly_send_poll

    settings = {k.setting for k in weekly_send_poll.REQUIRED_CONFIG}
    assert weekly_send.CONFIG.from_mailbox_cfg_key in settings
