"""Sustained pending-fetch escalation — the ERROR→CRITICAL primitive + all four wirings.

THE forensic this locks (2026-07-20): estimate_poll's pending fetch failed every cycle
for ~21h (629 ERROR rows) and was invisible — every fire surface (Open-CRITICALs panel,
/system badges, the triple-fire push) keys on CRITICAL. Each test here is
prove-the-control-bites: below the threshold the per-cycle ERROR stays an ERROR; AT the
threshold the SAME failure logs the lane's `*_pending_fetch_sustained` CRITICAL.

Run with: pytest -q tests/test_sustained_fetch_escalation.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

import pytest

from shared import portal_client, sustained_failure
from shared.error_log import Severity

# ---- the shared counter itself -----------------------------------------------------


def test_counter_increments_persists_and_resets(tmp_path):
    c = sustained_failure.SustainedFailureCounter(
        tmp_path / "c.json", "test.script", "test_counter_failed"
    )
    assert c.record() == 1
    assert c.record() == 2
    assert json.loads((tmp_path / "c.json").read_text()) == {"count": 2}
    c.reset()
    assert c.record() == 1  # reset zeroed the persisted count


def test_counter_recovers_from_a_corrupted_state_file(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    c = sustained_failure.SustainedFailureCounter(path, "test.script", "test_counter_failed")
    assert c.record() == 1  # corrupt → treated as 0, then incremented — never a throw


def test_counter_state_error_degrades_to_one_with_warn(tmp_path, mocker):
    log = mocker.patch("shared.sustained_failure.error_log.log")
    mocker.patch(
        "shared.sustained_failure.state_io.with_path_lock",
        side_effect=RuntimeError("lock boom"),
    )
    c = sustained_failure.SustainedFailureCounter(
        tmp_path / "c.json", "test.script", "test_counter_failed"
    )
    assert c.record() == 1  # never page off a state glitch
    assert log.call_args.kwargs["error_code"] == "test_counter_failed"


# ---- the four daemon wirings (parametrized) ----------------------------------------

CREDS = SimpleNamespace(base_url="https://portal.example", bearer="tok", secret="s")

CASES = [
    # (module path, pass fn, fetch fn name, sustained code, transient code)
    ("po_materials.estimate_poll", "_estimates_pass", "get_estimates_pending",
     "estimate_pending_fetch_sustained", "estimate_pending_fetch_failed"),
    ("po_materials.rfq_poll", "_rfq_pass", "get_rfqs_pending",
     "rfq_pending_fetch_sustained", "rfq_pending_fetch_failed"),
    ("po_materials.po_poll", "_drafts_pass", "get_pending_pos",
     "po_pending_fetch_sustained", "po_pending_fetch_failed"),
    ("subcontracts.subcontract_poll", "_drafts_pass", "get_pending_subcontracts",
     "subcontract_pending_fetch_sustained", "subcontract_pending_fetch_failed"),
]


def _codes(log_mock) -> list[tuple[Any, str]]:
    return [(c.args[0], c.kwargs.get("error_code")) for c in log_mock.call_args_list]


@pytest.mark.parametrize("mod_path,pass_fn,fetch_fn,sustained_code,transient_code", CASES)
def test_below_threshold_stays_error(mocker, mod_path, pass_fn, fetch_fn, sustained_code, transient_code):
    import importlib

    mod = importlib.import_module(mod_path)
    log = mocker.patch(f"{mod_path}.error_log.log")
    mocker.patch(
        f"{mod_path}.portal_client.{fetch_fn}",
        side_effect=portal_client.PortalTransportError("blip"),
    )
    mocker.patch.object(mod._FETCH_FAILS, "record", return_value=1)

    getattr(mod, pass_fn)(CREDS, defaultdict(int))

    codes = _codes(log)
    assert (Severity.ERROR, transient_code) in codes
    assert all(code != sustained_code for _, code in codes)


@pytest.mark.parametrize("mod_path,pass_fn,fetch_fn,sustained_code,transient_code", CASES)
def test_at_threshold_escalates_to_critical(mocker, mod_path, pass_fn, fetch_fn, sustained_code, transient_code):
    import importlib

    mod = importlib.import_module(mod_path)
    log = mocker.patch(f"{mod_path}.error_log.log")
    mocker.patch(
        f"{mod_path}.portal_client.{fetch_fn}",
        side_effect=portal_client.PortalTransportError("blip"),
    )
    mocker.patch.object(
        mod._FETCH_FAILS, "record",
        return_value=sustained_failure.DEFAULT_CRITICAL_THRESHOLD,
    )

    getattr(mod, pass_fn)(CREDS, defaultdict(int))

    assert (Severity.CRITICAL, sustained_code) in _codes(log)


@pytest.mark.parametrize("mod_path,pass_fn,fetch_fn,sustained_code,transient_code", CASES)
def test_successful_fetch_resets_the_counter(mocker, mod_path, pass_fn, fetch_fn, sustained_code, transient_code):
    import importlib

    mod = importlib.import_module(mod_path)
    mocker.patch(f"{mod_path}.error_log.log")
    mocker.patch(f"{mod_path}.portal_client.{fetch_fn}", return_value=[])
    reset = mocker.patch.object(mod._FETCH_FAILS, "reset")

    getattr(mod, pass_fn)(CREDS, defaultdict(int))

    reset.assert_called_once()
