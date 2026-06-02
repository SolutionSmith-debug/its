"""Locks the verbatim-duplication invariant for the daemon heartbeat helpers.

`safety_reports/intake_poll.py` and `safety_reports/weekly_send_poll.py` each
carry their own copy of the seven ITS_Daemon_Health heartbeat helpers
(`_load_heartbeat_row_state`, `_persist_heartbeat_row_state`,
`_invalidate_heartbeat_row_state`, `_create_heartbeat_row`,
`_resolve_heartbeat_row_id`, `_write_heartbeat_row`, `_log_heartbeat_failure`).
CLAUDE.md records them as "replicated VERBATIM (preservation-over-refactor;
shared/heartbeat.py extraction is tracked tech-debt)". A1's self-provision
change relies on that invariant: the create-fail / race-adopt / write-then-update
paths are exhaustively unit-tested on the intake_poll side only, and
weekly_send_poll's coverage rides on the bodies being logic-identical.

This test fails loudly if a future one-sided edit drifts the two copies, so the
shared coverage assumption stays honest until the helpers are extracted to
`shared/heartbeat.py` (at which point this test is deleted with the duplication).

Per-daemon differences are intentionally hoisted OUT of the helper bodies into
module constants (`DAEMON_NAME`, `_REGISTRATION_INTERVAL_SECONDS`,
`_REGISTRATION_SOURCE_ID`, `SCRIPT_NAME`, …), so the bodies themselves must be
executable-code identical. Docstrings are allowed to differ (and comments are
not part of the AST), so both are excluded from the comparison.

Run with: pytest -q tests/test_heartbeat_helper_parity.py
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DAEMONS = (
    _REPO_ROOT / "safety_reports" / "intake_poll.py",
    _REPO_ROOT / "safety_reports" / "weekly_send_poll.py",
)
_HELPERS = (
    "_load_heartbeat_row_state",
    "_persist_heartbeat_row_state",
    "_invalidate_heartbeat_row_state",
    "_create_heartbeat_row",
    "_resolve_heartbeat_row_id",
    "_write_heartbeat_row",
    "_log_heartbeat_failure",
)


def _dump_without_docstring(fn: ast.FunctionDef) -> str:
    """AST dump of a function with its leading docstring stripped."""
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    stripped = ast.FunctionDef(
        name=fn.name,
        args=fn.args,
        body=body,
        decorator_list=fn.decorator_list,
        returns=fn.returns,
        type_params=getattr(fn, "type_params", []),
    )
    return ast.dump(stripped)


def _helpers_from(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text())
    return {
        node.name: _dump_without_docstring(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in _HELPERS
    }


@pytest.mark.parametrize("helper", _HELPERS)
def test_heartbeat_helper_is_logic_identical_across_daemons(helper: str) -> None:
    intake, weekly_send = (_helpers_from(p) for p in _DAEMONS)
    assert helper in intake, f"{helper} missing from intake_poll.py"
    assert helper in weekly_send, f"{helper} missing from weekly_send_poll.py"
    assert intake[helper] == weekly_send[helper], (
        f"{helper} has drifted between intake_poll.py and weekly_send_poll.py "
        "(docstrings stripped). The verbatim-duplication invariant requires the "
        "helper BODIES to stay logic-identical — apply the change to BOTH, or "
        "extract to shared/heartbeat.py and delete this test."
    )
