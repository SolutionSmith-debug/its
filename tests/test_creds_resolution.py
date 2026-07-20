"""Transient-vs-absent classification of the fail-closed credential read.

The bug this locks down (live, 2026-07-20 04:42Z): a single Smartsheet GET blip on
`po_poll`'s Worker-base-URL read fell back to `""`, which was indistinguishable
from an unset row, so the daemon fired a CRITICAL saying PO **credentials** were
missing. Both Keychain entries were fine; the daemon self-healed 90s later. The
page was false AND it aimed the §43 repair at re-provisioning secrets — a
high-capability-class action — for a condition needing none.

So the contract under test is precisely: a read FAILURE must never be reported as
an absent credential, while a genuinely absent row still must page.
"""
from __future__ import annotations

import pytest

from shared import creds_resolution, smartsheet_client

_SETTING = "safety_reports.portal.worker_base_url"
_WS = "safety_reports"


def _patch(monkeypatch: pytest.MonkeyPatch, outcome: object) -> None:
    def fake_get_setting(setting: str, workstream: str | None = None) -> object:
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(smartsheet_client, "get_setting", fake_get_setting)


def test_readable_row_returns_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, "https://safety.evergreenmirror.com")
    assert creds_resolution.read_base_url(_SETTING, _WS) == "https://safety.evergreenmirror.com"


@pytest.mark.parametrize(
    "exc",
    [
        smartsheet_client.SmartsheetCircuitOpenError("breaker open"),
        # The pre-trip case that actually bit: the breaker needs N CONSECUTIVE
        # failures, so a one-cycle blip raises the RAW error class, not circuit-open.
        smartsheet_client.SmartsheetError("(<PreparedRequest [GET]>, None)"),
    ],
)
def test_read_failure_is_transient_never_absent(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    _patch(monkeypatch, exc)
    got = creds_resolution.read_base_url(_SETTING, _WS)
    assert isinstance(got, creds_resolution.TransientUnavailable), (
        "a FAILED read must classify as transient — returning None here is exactly "
        "the bug: it makes the caller page 'credentials missing' on a network blip"
    )
    assert got.reason, "the transient reason must be populated for the WARN / heartbeat"
    assert got is not None and not isinstance(got, str)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_absent_or_blank_row_is_a_misconfig(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    # A genuinely unset row must STILL resolve to None so the caller pages — the fix
    # must not silence the real misconfig it was hiding behind.
    _patch(monkeypatch, value.strip() if isinstance(value, str) else value)
    assert creds_resolution.read_base_url(_SETTING, _WS) is None


def test_missing_row_is_a_misconfig_not_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, smartsheet_client.SmartsheetNotFoundError("no such row"))
    assert creds_resolution.read_base_url(_SETTING, _WS) is None


def test_every_puller_actually_adopts_the_shared_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PARITY TOOTH: each puller's real `_resolve_credentials` must classify a
    failed read as transient.

    The per-daemon tests all MOCK `_resolve_credentials` to hand their caller a
    sentinel, so they verify the caller branch but would happily pass for a daemon
    that never adopted the shared classifier at all — the exact drift that left
    five pullers paging falsely after portal_poll had already fixed it. This drives
    the REAL resolver with a failing `get_setting`, so a puller that reverts to
    swallowing the error into "" fails HERE.

    Keychain is never reached: every resolver returns at the transient early-out
    before touching it.
    """
    import importlib

    modules = [
        "safety_reports.portal_poll",
        "po_materials.po_poll",
        "po_materials.rfq_poll",
        "po_materials.estimate_poll",
        "subcontracts.subcontract_poll",
        "field_ops.fieldops_sync",
    ]

    def boom(setting: str, workstream: str | None = None) -> object:
        raise smartsheet_client.SmartsheetError("(<PreparedRequest [GET]>, None)")

    monkeypatch.setattr(smartsheet_client, "get_setting", boom)
    offenders: list[str] = []
    for name in modules:
        mod = importlib.import_module(name)
        got = mod._resolve_credentials()
        if not isinstance(got, creds_resolution.TransientUnavailable):
            offenders.append(f"{name} -> {got!r}")
    assert not offenders, (
        "these pullers do NOT classify a failed config read as transient, so a "
        f"Smartsheet blip will make them page 'credentials missing': {offenders} — "
        "route the base-URL read through shared.creds_resolution.read_base_url"
    )


@pytest.mark.parametrize(
    "exc",
    [
        smartsheet_client.SmartsheetAuthError("revoked token"),
        smartsheet_client.SmartsheetPermissionError("lost share"),
    ],
)
def test_auth_and_permission_errors_propagate_and_page(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    # Deterministic misconfig — a revoked token will NOT self-heal, so swallowing it
    # as "transient" would silence a real outage forever. It must escape to
    # @its_error_log as a CRITICAL.
    _patch(monkeypatch, exc)
    with pytest.raises(type(exc)):
        creds_resolution.read_base_url(_SETTING, _WS)
