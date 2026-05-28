"""Intake-specific capability gating tests.

`tests/test_capability_gating.py` enforces the cross-workstream External
Send Gate (Foundation Mission v8 Invariant 1) via the GATED_SCRIPTS list
that intake.py and intake_poll.py are enrolled in. This file pins the
intake-specific nuances surfaced in the R3 session 1 brief + PR #59
polling-daemon brief that aren't part of the generic GATED_SCRIPTS
contract:

  1. No import from any `*_send*` module (e.g. the future `weekly_send`).
  2. No import path with `send` as a segment, even if the parent module
     might be otherwise legitimate.
  3. The capability surface of intake-pair scripts specifically — no
     plain `requests` import (Smartsheet/Box/Graph wrapping is via
     `shared/*`).

These pins exist so a future maintainer adding e.g. a "send_followup"
helper module to safety_reports doesn't accidentally enable a back-door
send capability for either intake.py or the polling daemon.

PR #59 expanded coverage from intake.py only to BOTH intake.py and
intake_poll.py: the polling daemon is the live mail-touching process
now, and the same capability surface applies to it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

INTAKE_PATHS = [
    REPO_ROOT / "safety_reports" / "intake.py",
    REPO_ROOT / "safety_reports" / "intake_poll.py",
]


def _imports_in(path: Path) -> set[str]:
    """Return the set of imported module names + from-import attribute paths."""
    tree = ast.parse(path.read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
                for alias in node.names:
                    imports.add(f"{node.module}.{alias.name}")
    return imports


@pytest.mark.parametrize(
    "intake_path", INTAKE_PATHS, ids=lambda p: p.name
)
def test_intake_does_not_import_any_send_named_module(intake_path: Path):
    """No import segment may contain 'send'. Covers weekly_send + future helpers."""
    imports = _imports_in(intake_path)
    offenders = [imp for imp in imports if "send" in imp.split(".")]
    assert not offenders, (
        f"{intake_path.relative_to(REPO_ROOT)} imports {offenders!r} — one or "
        f"more segments contain 'send'. External Send Gate violation."
    )


@pytest.mark.parametrize(
    "intake_path", INTAKE_PATHS, ids=lambda p: p.name
)
def test_intake_does_not_import_send_capable_libraries(intake_path: Path):
    """smtplib, email.mime.*, and `resend` are absent from intake imports."""
    imports = _imports_in(intake_path)
    forbidden_prefixes = ("smtplib", "email.mime", "resend")
    offenders = sorted(
        imp for imp in imports if any(imp.startswith(p) for p in forbidden_prefixes)
    )
    assert not offenders, (
        f"{intake_path.relative_to(REPO_ROOT)} imports {offenders!r}. "
        f"None of {forbidden_prefixes} are allowed in an intake script."
    )


@pytest.mark.parametrize(
    "intake_path", INTAKE_PATHS, ids=lambda p: p.name
)
def test_intake_does_not_directly_import_requests(intake_path: Path):
    """intake-pair routes all HTTP through `shared/*` wrappers; no direct
    `requests` import — keeps the SMTP/email-relay attack surface OFF the
    files even via an indirect HTTP path."""
    imports = _imports_in(intake_path)
    assert "requests" not in imports, (
        f"{intake_path.relative_to(REPO_ROOT)} imports `requests` directly. "
        "All HTTP must go through shared/* wrappers (smartsheet_client, "
        "box_client, anthropic_client, graph_client). Direct requests import "
        "re-opens the SMTP/email-relay attack surface the intake pair is "
        "designed to gate."
    )


def test_intake_pair_does_not_import_graph_send_mail():
    """Belt-and-suspenders: neither intake.py nor intake_poll.py may import
    `graph_client.send_mail` specifically. The broad `send_mail` substring
    check in tests/test_capability_gating.py catches this too, but pinning
    the exact attribute here makes the contract obvious for future readers
    grepping for the gate enforcement."""
    for path in INTAKE_PATHS:
        imports = _imports_in(path)
        offenders = [imp for imp in imports if imp.endswith(".send_mail")]
        assert not offenders, (
            f"{path.relative_to(REPO_ROOT)} imports {offenders!r} — direct "
            f"send_mail attribute import is forbidden in any generation "
            f"script per Foundation Mission v8 Invariant 1."
        )
