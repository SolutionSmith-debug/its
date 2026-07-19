"""Review-queue resolve ACT verb (DASH-13) — the errors_ops twin for
ITS_Review_Queue.

Prove-it-bites: the filter is REQUIRED (an unfiltered mass-resolve is refused
before any Smartsheet read), only PENDING rows are touched (idempotent), the
resolution value is validated, writes are batched under the per-run cap with
honest partial-failure reporting, dry-run writes nothing, the audit row is
durable, and the HTTP route requires the elevated ceremony. All Smartsheet
calls are MOCKED.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from operator_dashboard.act import review_ops
from operator_dashboard.app import create_app

_PIN = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _reset_pin_throttle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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

    state: dict[str, Any] = {"rows": [], "updates": [], "audits": [], "fail_after": None}

    def get_rows(sheet_id: int, **kw: Any) -> list[dict[str, Any]]:
        return list(state["rows"])

    def update_rows(sheet_id: int, updates: list[dict[str, Any]]) -> None:
        if state["fail_after"] is not None and len(state["updates"]) >= state["fail_after"]:
            raise RuntimeError("smartsheet down")
        state["updates"].extend(updates)

    def log(sev: Any, script: str, msg: str, **kw: Any) -> None:
        state["audits"].append((kw.get("error_code"), msg))

    monkeypatch.setattr(ss, "get_rows", get_rows)
    monkeypatch.setattr(ss, "update_rows", update_rows)
    monkeypatch.setattr(el, "log", log)
    monkeypatch.setattr(kc, "get_secret", lambda name, account=None: _PIN)
    return state


def _row(rid: int, status: str = "PENDING", ws: str = "safety_reports",
         summary: str = "weekly compile failed for job JOB-000013") -> dict[str, Any]:
    return {"_row_id": rid, "Status": status, "Workstream": ws, "Summary": summary}


def test_filter_required(env: dict[str, Any]) -> None:
    out = review_ops.resolve_review_rows("op")
    assert out.kind == "rejected"
    assert env["updates"] == []


def test_bad_resolution_rejected(env: dict[str, Any]) -> None:
    out = review_ops.resolve_review_rows("op", workstream="safety_reports", resolution="DELETED")
    assert out.kind == "rejected"
    assert env["updates"] == []


def test_only_pending_matching_rows_marked(env: dict[str, Any]) -> None:
    env["rows"] = [
        _row(1),
        _row(2, status="REJECTED"),                      # already terminal — untouched
        _row(3, ws="progress_reports"),                  # other workstream — untouched
        _row(4, summary="has no reports contact (TO)"),  # other class — untouched
    ]
    out = review_ops.resolve_review_rows(
        "op", workstream="safety_reports", summary_prefix="weekly compile failed",
        resolution="REJECTED", note="stale sandbox backlog",
    )
    assert out.kind == "ok"
    assert [u["_row_id"] for u in env["updates"]] == [1]
    update = env["updates"][0]
    assert update["Status"] == "REJECTED"
    assert update["Resolved At"]
    assert "resolved via dashboard by op" in update["Resolution Notes"]
    assert "stale sandbox backlog" in update["Resolution Notes"]
    assert env["audits"] and env["audits"][0][0] == "review_rows_resolved"


def test_rerun_is_idempotent(env: dict[str, Any]) -> None:
    env["rows"] = [_row(1, status="REJECTED"), _row(2, status="APPROVED")]
    out = review_ops.resolve_review_rows("op", workstream="safety_reports")
    assert out.kind == "noop"
    assert env["updates"] == []


def test_dry_run_writes_nothing(env: dict[str, Any]) -> None:
    env["rows"] = [_row(1), _row(2)]
    out = review_ops.resolve_review_rows("op", workstream="safety_reports", dry_run=True)
    assert out.kind == "ok" and "DRY RUN" in out.message and "2" in out.message
    assert env["updates"] == []
    assert env["audits"] == []  # a preview is not an action


def test_partial_failure_is_honest(env: dict[str, Any]) -> None:
    import shared.defaults as defaults

    batch = defaults.SHEET_ROW_ROTATION_DELETE_BATCH
    env["rows"] = [_row(i) for i in range(1, batch + 2)]  # two batches
    env["fail_after"] = batch  # first batch lands, second raises
    out = review_ops.resolve_review_rows("op", workstream="safety_reports")
    assert out.kind == "error"
    assert f"marked {batch} of {batch + 1}" in out.message
    assert any("PARTIAL" in msg for _, msg in env["audits"])


def test_read_failure_is_error_outcome(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.smartsheet_client as ss

    def boom(*a: Any, **k: Any) -> list[dict[str, Any]]:
        raise RuntimeError("breaker open")

    monkeypatch.setattr(ss, "get_rows", boom)
    out = review_ops.resolve_review_rows("op", workstream="safety_reports")
    assert out.kind == "error" and "could not read" in out.message


def test_http_route_requires_elevated_ceremony(env: dict[str, Any]) -> None:
    env["rows"] = [_row(1)]
    client = TestClient(create_app())
    r = client.post(
        "/act/review/resolve",
        data={"pin": _PIN, "confirm": "wrong", "workstream": "safety_reports"},
        headers={"origin": "http://127.0.0.1:8484"},
    )
    assert "denied" in r.text
    assert env["updates"] == []
    r = client.post(
        "/act/review/resolve",
        data={
            "pin": _PIN, "confirm": "resolve-review",
            "workstream": "safety_reports", "mode": "preview",
        },
        headers={"origin": "http://127.0.0.1:8484"},
    )
    assert "DRY RUN" in r.text
    assert env["updates"] == []
    r = client.post(
        "/act/review/resolve",
        data={
            "pin": _PIN, "confirm": "resolve-review",
            "workstream": "safety_reports", "mode": "commit",
        },
        headers={"origin": "http://127.0.0.1:8484"},
    )
    assert "marked 1 of 1" in r.text
    assert len(env["updates"]) == 1
