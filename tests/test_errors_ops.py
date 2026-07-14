"""Tests for the dashboard clear-error-log verb (operator_dashboard/act/errors_ops.py)
and its shared terminality predicate (shared/errors_rotation.py).

prove-the-control-bites (HOUSE_REFLEXES §2): the load-bearing invariant is "an OPEN
CRITICAL is NEVER deleted". Every clear test injects a synthetic open CRITICAL among
terminal rows and asserts it SURVIVES — if the predicate were bypassed these RED-light.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import shared.error_log as el
import shared.errors_rotation as er
import shared.sheet_ids as sid  # noqa: F401 — imported for parity with runtime lookups
import shared.smartsheet_client as ss
from operator_dashboard.act import errors_ops
from operator_dashboard.act import router as router_mod

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import watchdog  # noqa: E402 — after sys.path insertion

# ---- shared terminality predicate ----------------------------------------

def test_terminal_predicate_open_critical_is_never_terminal() -> None:
    assert er.errors_row_is_terminal({"Severity": "WARN"}) is True
    assert er.errors_row_is_terminal({"Severity": "INFO"}) is True
    assert er.errors_row_is_terminal({"Severity": "ERROR"}) is True
    # open CRITICAL (blank / missing Resolved At) — NOT terminal
    assert er.errors_row_is_terminal({"Severity": "CRITICAL"}) is False
    assert er.errors_row_is_terminal({"Severity": "CRITICAL", "Resolved At": ""}) is False
    assert er.errors_row_is_terminal({"Severity": "CRITICAL", "Resolved At": None}) is False
    # resolved CRITICAL — terminal
    assert er.errors_row_is_terminal({"Severity": "CRITICAL", "Resolved At": "2026-07-01"}) is True


def test_row_age_date_parsing() -> None:
    assert er.row_age_date({"Timestamp": "2026-07-01T12:00:00"}, "Timestamp") == date(2026, 7, 1)
    assert er.row_age_date({"Timestamp": "2026-07-01"}, "Timestamp") == date(2026, 7, 1)
    assert er.row_age_date({"Timestamp": ""}, "Timestamp") is None
    assert er.row_age_date({"Timestamp": None}, "Timestamp") is None
    assert er.row_age_date({"Timestamp": "not-a-date"}, "Timestamp") is None


def test_watchdog_reuses_the_shared_predicate() -> None:
    # single source of truth: watchdog's private aliases ARE the shared functions
    assert watchdog._errors_row_is_terminal is er.errors_row_is_terminal
    assert watchdog._row_age_date is er.row_age_date


# ---- clear_error_log worker ----------------------------------------------

def _rows() -> list[dict[str, Any]]:
    return [
        {"_row_id": 1, "Severity": "WARN", "Error": "config_row_missing", "Timestamp": "2026-05-01"},
        {"_row_id": 2, "Severity": "ERROR", "Error": "po_fetch_failed", "Timestamp": "2026-07-13"},
        # OPEN CRITICAL — must survive
        {"_row_id": 3, "Severity": "CRITICAL", "Error": "portal_creds_missing", "Resolved At": "", "Timestamp": "2026-07-10"},
        # resolved CRITICAL — terminal
        {"_row_id": 4, "Severity": "CRITICAL", "Error": "old_fixed", "Resolved At": "2026-06-01", "Timestamp": "2026-06-01"},
        # preserved audit-trail codes — must survive
        {"_row_id": 5, "Severity": "WARN", "Error": "errors_log_cleared", "Timestamp": "2026-05-01"},
        {"_row_id": 6, "Severity": "WARN", "Error": "row_cap_rotation", "Timestamp": "2026-05-01"},
    ]


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[dict[str, Any]]]:
    deleted: list[int] = []
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(_rows()))
    monkeypatch.setattr(ss, "delete_rows", lambda sheet_id, ids: deleted.extend(ids))

    def _log(severity: Any, script: str, message: str, *, error_code: Any = None, alert: bool = True, **kw: Any) -> None:
        audits.append({"error_code": error_code, "script": script, "alert": alert})

    monkeypatch.setattr(el, "log", _log)
    return deleted, audits


def test_clear_deletes_terminal_never_open_critical_never_audit_trail(
    wired: tuple[list[int], list[dict[str, Any]]],
) -> None:
    deleted, audits = wired
    out = errors_ops.clear_error_log("seth")
    assert out.kind == "ok"
    # rows 1 (WARN), 2 (ERROR), 4 (resolved CRITICAL) are terminal + not preserved
    assert sorted(deleted) == [1, 2, 4]
    # the load-bearing invariant: the OPEN CRITICAL (3) SURVIVES
    assert 3 not in deleted
    # preserved audit trail (5 errors_log_cleared, 6 row_cap_rotation) SURVIVES
    assert 5 not in deleted and 6 not in deleted
    # exactly one audit row, error_code=errors_log_cleared, non-paging
    assert len(audits) == 1
    assert audits[0]["error_code"] == "errors_log_cleared"
    assert audits[0]["alert"] is False


def test_clear_noop_when_nothing_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: [
        {"_row_id": 3, "Severity": "CRITICAL", "Resolved At": "", "Error": "x", "Timestamp": "2026-07-10"},
    ])
    monkeypatch.setattr(ss, "delete_rows", lambda sheet_id, ids: calls.append(ids))
    monkeypatch.setattr(el, "log", lambda *a, **k: None)
    out = errors_ops.clear_error_log("seth")
    assert out.kind == "noop"
    assert calls == []  # never called delete on an open-CRITICAL-only sheet


def test_clear_older_than_days_keeps_newer(wired: tuple[list[int], list[dict[str, Any]]]) -> None:
    deleted, _ = wired
    # a huge threshold => cutoff far in the past => no row is old enough => noop
    out = errors_ops.clear_error_log("seth", older_than_days=100_000)
    assert out.kind == "noop"
    assert deleted == []


def test_clear_dry_run_deletes_nothing(wired: tuple[list[int], list[dict[str, Any]]]) -> None:
    deleted, audits = wired
    out = errors_ops.clear_error_log("seth", dry_run=True)
    assert out.kind == "ok" and "DRY RUN" in out.message
    assert deleted == [] and audits == []


def test_clear_partial_failure_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    audits: list[Any] = []
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(_rows()))

    def _boom(sheet_id: int, ids: list[int]) -> None:
        raise ss.SmartsheetError("500 boom")

    monkeypatch.setattr(ss, "delete_rows", _boom)
    monkeypatch.setattr(el, "log", lambda *a, **k: audits.append(k.get("error_code")))
    out = errors_ops.clear_error_log("seth")
    assert out.kind == "error" and "run again to continue" in out.message
    assert audits == ["errors_log_cleared"]  # partial-clear still audited


def test_clear_respects_per_run_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.defaults as defaults

    # shrink the cap to 4 (batch 2 x 2 batches) so a 6-eligible sheet exceeds it
    monkeypatch.setattr(defaults, "SHEET_ROW_ROTATION_DELETE_BATCH", 2)
    monkeypatch.setattr(defaults, "SHEET_ROW_ROTATION_MAX_BATCHES_PER_RUN", 2)
    rows = [{"_row_id": i, "Severity": "WARN", "Error": "x", "Timestamp": "2026-05-01"} for i in range(1, 7)]
    chunks: list[list[int]] = []
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(rows))
    monkeypatch.setattr(ss, "delete_rows", lambda sheet_id, ids: chunks.append(list(ids)))
    monkeypatch.setattr(el, "log", lambda *a, **k: None)
    out = errors_ops.clear_error_log("seth")
    assert out.kind == "ok" and "run again to continue" in out.message
    assert [len(c) for c in chunks] == [2, 2]  # capped at 4 (2 batches of 2)
    assert sum(len(c) for c in chunks) == 4  # 2 of the 6 remain for the next run


# ---- router gating --------------------------------------------------------

def test_route_fail_closed_without_auth_touches_no_smartsheet(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from operator_dashboard.app import create_app

    touched: list[str] = []

    def _mark_get(*a: Any, **k: Any) -> list[dict[str, Any]]:
        touched.append("get")
        return []

    monkeypatch.setattr(ss, "get_rows", _mark_get)
    monkeypatch.setattr(ss, "delete_rows", lambda *a, **k: touched.append("delete"))
    client = TestClient(create_app())
    resp = client.post("/act/errors/clear", data={"pin": "x", "confirm": "wrong"})
    assert resp.status_code == 200  # uniform outcome partial, no status-code oracle
    assert touched == []  # gate short-circuits BEFORE any Smartsheet read/delete


def test_route_happy_path_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from operator_dashboard.app import create_app

    deleted: list[int] = []
    monkeypatch.setattr(router_mod, "check_origin", lambda *a, **k: None)
    monkeypatch.setattr(router_mod, "verify_elevated", lambda *a, **k: None)
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(_rows()))
    monkeypatch.setattr(ss, "delete_rows", lambda sheet_id, ids: deleted.extend(ids))
    monkeypatch.setattr(el, "log", lambda *a, **k: None)
    client = TestClient(create_app())
    resp = client.post("/act/errors/clear", data={"pin": "x", "confirm": "clear-error-log"})
    assert resp.status_code == 200
    assert sorted(deleted) == [1, 2, 4] and 3 not in deleted  # open CRITICAL survives via the route too


# ---- mark_errors_resolved (the "solve it" half) ---------------------------
# prove-the-control-bites (HOUSE_REFLEXES §2): two load-bearing invariants — (1) an
# unfiltered mass-resolve is REFUSED (it would empty the "am I on fire" surface), and
# (2) only OPEN CRITICALs are stamped. Each test injects violations and asserts they hold.


def _crit_rows() -> list[dict[str, Any]]:
    return [
        # open CRITICALs — the markable set
        {"_row_id": 10, "Severity": "CRITICAL", "Script": "intake_poll", "Error": "graph_fail", "Resolved At": ""},
        {"_row_id": 11, "Severity": "CRITICAL", "Script": "intake_poll", "Error": "smoke", "Resolved At": None},
        {"_row_id": 12, "Severity": "CRITICAL", "Script": "po_poll", "Error": "graph_fail", "Resolved At": ""},
        # already-terminal — NEVER marked
        {"_row_id": 13, "Severity": "CRITICAL", "Script": "intake_poll", "Error": "graph_fail", "Resolved At": "2026-06-01"},
        {"_row_id": 14, "Severity": "WARN", "Script": "intake_poll", "Error": "graph_fail"},
    ]


@pytest.fixture
def wired_mark(monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    updates: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(_crit_rows()))
    monkeypatch.setattr(ss, "update_rows", lambda sheet_id, ups: updates.extend(ups))

    def _log(severity: Any, script: str, message: str, *, error_code: Any = None, alert: bool = True, **kw: Any) -> None:
        audits.append({"error_code": error_code, "script": script, "alert": alert})

    monkeypatch.setattr(el, "log", _log)
    return updates, audits


def test_mark_by_script_stamps_only_matching_open_criticals(
    wired_mark: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    updates, audits = wired_mark
    out = errors_ops.mark_errors_resolved("seth", script="intake_poll")
    assert out.kind == "ok"
    ids = sorted(u["_row_id"] for u in updates)
    # only OPEN CRITICALs with Script=intake_poll (10, 11); 12 wrong script, 13 terminal, 14 WARN
    assert ids == [10, 11]
    # every update stamps a non-blank "Resolved At" (=> errors_row_is_terminal flips True)
    assert all(str(u["Resolved At"]).strip() for u in updates)
    for u in updates:
        assert er.errors_row_is_terminal({"Severity": "CRITICAL", **u}) is True
    # exactly one non-paging audit row
    assert len(audits) == 1
    assert audits[0]["error_code"] == "errors_resolved_marked" and audits[0]["alert"] is False


def test_mark_by_error_code_filter(wired_mark: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
    updates, _ = wired_mark
    out = errors_ops.mark_errors_resolved("seth", error_code="graph_fail")
    assert out.kind == "ok"
    # open CRITICALs with Error=graph_fail: 10, 12 (13 is terminal, 14 is WARN)
    assert sorted(u["_row_id"] for u in updates) == [10, 12]


def test_mark_requires_a_filter_and_touches_no_smartsheet(monkeypatch: pytest.MonkeyPatch) -> None:
    touched: list[str] = []
    def _get(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        touched.append("get")
        return []

    monkeypatch.setattr(ss, "get_rows", _get)
    monkeypatch.setattr(ss, "update_rows", lambda *a, **k: touched.append("update"))
    monkeypatch.setattr(el, "log", lambda *a, **k: None)
    out = errors_ops.mark_errors_resolved("seth")  # no filter
    assert out.kind == "error" and "filter is required" in out.message
    assert touched == []  # refused BEFORE any Smartsheet read/write


def test_mark_dry_run_stamps_nothing(wired_mark: tuple[list[dict[str, Any]], list[dict[str, Any]]]) -> None:
    updates, audits = wired_mark
    out = errors_ops.mark_errors_resolved("seth", script="intake_poll", dry_run=True)
    assert out.kind == "ok" and "DRY RUN" in out.message
    assert updates == [] and audits == []


def test_mark_noop_when_no_open_critical_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(_crit_rows()))
    monkeypatch.setattr(ss, "update_rows", lambda sheet_id, ups: calls.append(ups))
    monkeypatch.setattr(el, "log", lambda *a, **k: None)
    out = errors_ops.mark_errors_resolved("seth", script="nonexistent_daemon")
    assert out.kind == "noop"
    assert calls == []  # never stamps when nothing matches


def test_mark_partial_failure_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    audits: list[Any] = []
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(_crit_rows()))

    def _boom(sheet_id: int, ups: list[dict[str, Any]]) -> None:
        raise ss.SmartsheetError("500 boom")

    monkeypatch.setattr(ss, "update_rows", _boom)
    monkeypatch.setattr(el, "log", lambda *a, **k: audits.append(k.get("error_code")))
    out = errors_ops.mark_errors_resolved("seth", script="intake_poll")
    assert out.kind == "error" and "run again to continue" in out.message
    assert audits == ["errors_resolved_marked"]  # partial-mark still audited


def test_mark_read_failure_is_error_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "get_rows", lambda *a, **k: (_ for _ in ()).throw(ss.SmartsheetError("breaker open")))
    monkeypatch.setattr(ss, "update_rows", lambda *a, **k: None)
    monkeypatch.setattr(el, "log", lambda *a, **k: None)
    out = errors_ops.mark_errors_resolved("seth", script="intake_poll")
    assert out.kind == "error" and "could not read ITS_Errors" in out.message


def test_mark_then_clear_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Integration of the two halves: a row a MARK makes terminal is exactly a row a CLEAR deletes.
    state = list(_crit_rows())

    def _get(sheet_id: int, **kw: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in state]

    def _update(sheet_id: int, ups: list[dict[str, Any]]) -> None:
        for u in ups:
            for r in state:
                if r["_row_id"] == u["_row_id"]:
                    r["Resolved At"] = u["Resolved At"]

    deleted: list[int] = []
    monkeypatch.setattr(ss, "get_rows", _get)
    monkeypatch.setattr(ss, "update_rows", _update)
    monkeypatch.setattr(ss, "delete_rows", lambda sheet_id, ids: deleted.extend(ids))
    monkeypatch.setattr(el, "log", lambda *a, **k: None)

    # before marking, row 10 is an OPEN CRITICAL — clear must NOT delete it
    errors_ops.clear_error_log("seth")
    assert 10 not in deleted
    deleted.clear()
    # mark it resolved, then clear — now it IS deletable
    errors_ops.mark_errors_resolved("seth", script="intake_poll")
    errors_ops.clear_error_log("seth")
    assert 10 in deleted and 11 in deleted  # both intake_poll open CRITICALs now swept
    assert 12 not in deleted  # untouched open CRITICAL still survives


# ---- router gating (mark) -------------------------------------------------

def test_route_resolve_fail_closed_without_auth_touches_no_smartsheet(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from operator_dashboard.app import create_app

    touched: list[str] = []
    def _get(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
        touched.append("get")
        return []

    monkeypatch.setattr(ss, "get_rows", _get)
    monkeypatch.setattr(ss, "update_rows", lambda *a, **k: touched.append("update"))
    client = TestClient(create_app())
    resp = client.post("/act/errors/resolve", data={"pin": "x", "confirm": "wrong", "script": "intake_poll"})
    assert resp.status_code == 200
    assert touched == []  # gate short-circuits BEFORE any Smartsheet read/write


def test_route_resolve_happy_path_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from operator_dashboard.app import create_app

    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(router_mod, "check_origin", lambda *a, **k: None)
    monkeypatch.setattr(router_mod, "verify_elevated", lambda *a, **k: None)
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(_crit_rows()))
    monkeypatch.setattr(ss, "update_rows", lambda sheet_id, ups: updates.extend(ups))
    monkeypatch.setattr(el, "log", lambda *a, **k: None)
    client = TestClient(create_app())
    resp = client.post("/act/errors/resolve", data={"pin": "x", "confirm": "mark-resolved", "script": "intake_poll"})
    assert resp.status_code == 200
    assert sorted(u["_row_id"] for u in updates) == [10, 11]


def test_route_resolve_preview_is_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # §3.1 safety: mode=preview must count without writing to the forensic surface.
    from fastapi.testclient import TestClient

    from operator_dashboard.app import create_app

    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(router_mod, "check_origin", lambda *a, **k: None)
    monkeypatch.setattr(router_mod, "verify_elevated", lambda *a, **k: None)
    monkeypatch.setattr(ss, "get_rows", lambda sheet_id, **kw: list(_crit_rows()))
    monkeypatch.setattr(ss, "update_rows", lambda sheet_id, ups: updates.extend(ups))
    monkeypatch.setattr(el, "log", lambda *a, **k: None)
    client = TestClient(create_app())
    resp = client.post(
        "/act/errors/resolve",
        data={"pin": "x", "confirm": "mark-resolved", "script": "intake_poll", "mode": "preview"},
    )
    assert resp.status_code == 200
    assert updates == []  # preview stamps NOTHING (dry run)
