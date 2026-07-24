"""Tests for shared/sentry_client.py.

All `sentry_sdk` and Keychain interactions are mocked — these tests never
hit the network and never read the real Keychain. The module-level
`_initialized` flag is reset between tests via the autouse fixture.

Run with: pytest -q tests/test_sentry_client.py
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared import sentry_client
from shared.sentry_client import SentryCaptureError, SentryError, SentryInitError

# ---- Fixtures + helpers --------------------------------------------------


@pytest.fixture(autouse=True)
def reset_sentry_state(mocker):
    """Reset the module's init flag and stub keychain reads for every test."""
    mocker.patch.object(sentry_client, "_initialized", False)
    mocker.patch(
        "shared.sentry_client.keychain.get_secret",
        side_effect=lambda key, *a, **kw: f"https://fake-{key}@sentry.test/1",
    )


# ---- get_client lazy init ------------------------------------------------


def test_get_client_initializes_sdk_with_dsn_from_keychain(mocker):
    init = mocker.patch("shared.sentry_client.sentry_sdk.init")

    sentry_client.get_client()

    init.assert_called_once()
    kwargs = init.call_args.kwargs
    assert kwargs["dsn"] == "https://fake-ITS_SENTRY_DSN@sentry.test/1"
    assert kwargs["environment"] == "production"
    # Performance monitoring off — this client is for CRITICAL capture only.
    assert kwargs["traces_sample_rate"] == 0.0
    assert kwargs["send_default_pii"] is False


def test_get_client_initializes_only_once(mocker):
    init = mocker.patch("shared.sentry_client.sentry_sdk.init")

    sentry_client.get_client()
    sentry_client.get_client()
    sentry_client.get_client()

    init.assert_called_once()


def test_get_client_init_failure_raises_typed_exception(mocker):
    mocker.patch(
        "shared.sentry_client.sentry_sdk.init",
        side_effect=RuntimeError("bad DSN"),
    )

    with pytest.raises(SentryInitError, match="bad DSN"):
        sentry_client.get_client()


def test_sentry_init_error_is_subclass_of_sentry_error():
    # The error hierarchy is public — tests of the catch-broader pattern
    # in error_log._alert_critical depend on it.
    assert issubclass(SentryInitError, SentryError)
    assert issubclass(SentryCaptureError, SentryError)


# ---- capture_exception payload + behavior --------------------------------


def _mock_scope_context(mocker):
    """Patch sentry_sdk.push_scope() to return a context manager whose
    __enter__ yields a MagicMock scope. Returns (push_scope_patch, scope_mock).
    """
    scope = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=scope)
    ctx.__exit__ = MagicMock(return_value=False)
    push = mocker.patch("shared.sentry_client.sentry_sdk.push_scope", return_value=ctx)
    return push, scope


def test_capture_exception_initializes_sdk_lazily(mocker):
    init = mocker.patch("shared.sentry_client.sentry_sdk.init")
    _mock_scope_context(mocker)
    mocker.patch("shared.sentry_client.capture_message")

    sentry_client.capture_exception("test.script", "boom", "Traceback...")

    init.assert_called_once()


def test_capture_exception_sets_expected_tags_and_extra(mocker):
    _, scope = _mock_scope_context(mocker)
    mocker.patch("shared.sentry_client.sentry_sdk.init")
    capture = mocker.patch("shared.sentry_client.capture_message")

    sentry_client.capture_exception(
        "safety_reports.intake",
        "unhandled: ValueError specific-bug",
        "Traceback (most recent call last):\n  ...",
    )

    # Tags
    set_tag_calls = {c.args[0]: c.args[1] for c in scope.set_tag.call_args_list}
    assert set_tag_calls == {
        "script": "safety_reports.intake",
        "severity": "CRITICAL",
        "source": "its-error-log",
    }
    # Extra
    scope.set_extra.assert_called_once()
    assert scope.set_extra.call_args.args[0] == "traceback"
    assert "Traceback" in scope.set_extra.call_args.args[1]
    # Message + level
    capture.assert_called_once_with(
        "unhandled: ValueError specific-bug",
        level="fatal",
    )


def test_capture_exception_blank_traceback_falls_back_to_none(mocker):
    _, scope = _mock_scope_context(mocker)
    mocker.patch("shared.sentry_client.sentry_sdk.init")
    mocker.patch("shared.sentry_client.capture_message")

    sentry_client.capture_exception("s", "m", "")  # blank exc_info

    # The "(none)" sentinel ensures the Sentry "extra" never has an empty
    # string that the SDK might silently drop.
    assert scope.set_extra.call_args.args[1] == "(none)"


def test_capture_exception_sdk_failure_translates_to_typed_error(mocker):
    _mock_scope_context(mocker)
    mocker.patch("shared.sentry_client.sentry_sdk.init")
    mocker.patch(
        "shared.sentry_client.capture_message",
        side_effect=RuntimeError("transport closed"),
    )

    with pytest.raises(SentryCaptureError, match="transport closed"):
        sentry_client.capture_exception("s", "m", "tb")


def test_capture_exception_init_error_propagates_as_sentry_init_error(mocker):
    # If init fails on the lazy first call, the typed init error
    # surfaces — NOT a SentryCaptureError.
    mocker.patch(
        "shared.sentry_client.sentry_sdk.init",
        side_effect=RuntimeError("bad DSN"),
    )
    _mock_scope_context(mocker)

    with pytest.raises(SentryInitError, match="bad DSN"):
        sentry_client.capture_exception("s", "m", "tb")
