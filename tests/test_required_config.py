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
    # ONE summary INFO line for the pass (not a per-key line). Retargeted to the
    # single-summary format `setting[workstream]=value(SOURCE)` — the section-5
    # content (key + workstream + value + source) must still be named in it.
    mock_log.assert_called_once()
    severity, script, message = mock_log.call_args.args
    assert severity is Severity.INFO
    assert script == "test.script"
    assert "a.b" in message and "global" in message  # key + workstream named
    assert "hello" in message  # resolved value named
    assert "ITS_Config" in message  # source named (distinct from the blank/default branch)
    assert "config resolved 1 key(s)" in message


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
    # Blank row is folded into the same one-per-pass summary INFO line, source "default".
    mock_log.assert_called_once()
    severity, _, message = mock_log.call_args.args
    assert severity is Severity.INFO
    assert "k" in message and "global" in message  # key + workstream named
    assert "dflt" in message  # the default value named
    assert "default" in message  # source named
    assert "ITS_Config" not in message  # a blank row is NOT sourced from ITS_Config


# ---- mixed pass → exactly ONE summary line naming every key + source -----


def test_mixed_pass_emits_one_summary_naming_each_key_and_source(mock_get_setting, mock_log):
    # k1 resolves from ITS_Config, k2 is a present-but-blank row (→ default), in ONE pass.
    # The whole pass emits EXACTLY ONE INFO summary line, and that line NAMES every resolved
    # key with its value AND source. This single test red-lights on BOTH S1-a regressions:
    #   - reverting to per-key lines  → call_count becomes 2 (not 1)
    #   - collapsing to a bare count  → the key names / values / sources vanish from the line
    def _side(setting, *, workstream):
        return "cfgval" if setting == "k1" else None  # k2 → blank → default

    mock_get_setting.side_effect = _side
    out = resolve_and_log(
        "d.poll",
        [
            ConfigKey("k1", "field_ops", "d1", "str"),
            ConfigKey("k2", "field_ops", "d2", "str"),
        ],
    )
    # Returned dict is populated identically to the per-key era: config value for k1,
    # declared default for the blank k2.
    assert out == {"k1": "cfgval", "k2": "d2"}
    # Exactly ONE log call for the whole pass, and it is the INFO summary.
    assert mock_log.call_count == 1, "expected exactly ONE summary INFO line for the pass"
    severity, script, message = mock_log.call_args.args
    assert severity is Severity.INFO
    assert script == "d.poll"
    assert "config resolved 2 key(s)" in message
    # section-5: the ONE line names EACH key with its value + source.
    assert "k1" in message and "cfgval" in message and "ITS_Config" in message
    assert "k2" in message and "d2" in message and "default" in message


def test_missing_row_excluded_from_the_resolved_summary(mock_get_setting, mock_log):
    # A MISSING row keeps its own per-key config_row_missing WARN and is NOT folded into the
    # resolved-keys INFO summary (which names only successfully-resolved keys).
    def _side(setting, *, workstream):
        if setting == "absent":
            raise SmartsheetNotFoundError("no row")
        return "ok"

    mock_get_setting.side_effect = _side
    resolve_and_log(
        "t",
        [
            ConfigKey("present", "global", "d1", "str"),
            ConfigKey("absent", "global", "d2", "str"),
        ],
    )
    infos = [c for c in mock_log.call_args_list if c.args[0] is Severity.INFO]
    warns = [c for c in mock_log.call_args_list if c.args[0] is Severity.WARN]
    assert len(infos) == 1  # one summary INFO for the resolved key
    assert len(warns) == 1  # one per-key WARN for the missing row
    summary_msg = infos[0].args[2]
    assert "present" in summary_msg  # the resolved key IS named
    assert "absent" not in summary_msg  # the missing key is NOT folded into the summary
    assert warns[0].kwargs["error_code"] == "config_row_missing"
    assert "absent" in warns[0].args[2]


def test_empty_keys_no_crash_and_no_zero_key_summary(mock_get_setting, mock_log):
    # An empty keys sequence must not crash and must NOT emit a confusing
    # "config resolved 0 key(s)" line (the summary is emitted only when ≥1 key resolved).
    out = resolve_and_log("t", [])
    assert out == {}
    mock_get_setting.assert_not_called()
    mock_log.assert_not_called()  # nothing at all — no summary, no WARN


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


def test_transient_failures_summarized_to_one_warn(mock_get_setting, mock_log):
    # alert-hygiene: N keys all failing transiently (breaker-open window) must emit exactly
    # ONE summarized WARN, not one per key — the former per-key flood is the fix's target.
    mock_get_setting.side_effect = SmartsheetError("circuit open")
    keys = [ConfigKey(f"k{i}", "po_materials", False, "bool") for i in range(5)]
    out = resolve_and_log("po.poll", keys)
    assert out == {f"k{i}": False for i in range(5)}  # fail-open preserved for every key
    assert mock_log.call_count == 1, "expected ONE summarized WARN, not one per key"
    severity, script, message = mock_log.call_args.args
    assert severity is Severity.WARN
    assert script == "po.poll"
    assert mock_log.call_args.kwargs["error_code"] == "config_read_error"
    assert "5 of 5 key(s)" in message
    assert "SmartsheetError" in message
    assert "k0" in message and "k4" in message  # names the failed keys


def test_missing_row_stays_per_key_but_transient_is_summarized(mock_get_setting, mock_log):
    # A MISSING row is individually actionable → keeps its own WARN; transient reads collapse
    # into the single summary. So: 1 per-key config_row_missing + 1 summary = 2 WARNs total.
    def _side(setting, *, workstream):
        if setting == "missing":
            raise SmartsheetNotFoundError("no row")
        raise SmartsheetError("circuit open")

    mock_get_setting.side_effect = _side
    out = resolve_and_log(
        "t",
        [
            ConfigKey("missing", "global", "d0", "str"),
            ConfigKey("t1", "global", "d1", "str"),
            ConfigKey("t2", "global", "d2", "str"),
        ],
    )
    assert out == {"missing": "d0", "t1": "d1", "t2": "d2"}  # all fail-open to defaults
    codes = [c.kwargs.get("error_code") for c in mock_log.call_args_list]
    assert codes.count("config_row_missing") == 1  # the missing row keeps its per-key WARN
    assert codes.count("config_read_error") == 1  # the two transient reads → ONE summary
    assert mock_log.call_count == 2


def test_no_failures_emits_no_warn(mock_get_setting, mock_log):
    mock_get_setting.return_value = "v"
    resolve_and_log("t", [ConfigKey("a", "global", "d", "str"), ConfigKey("b", "global", "d", "str")])
    warns = [c for c in mock_log.call_args_list if c.args[0] is Severity.WARN]
    assert warns == []  # all-clean pass emits no WARN (only env-gated INFO)


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
