"""Intake-specific capability gating tests.

`tests/test_capability_gating.py` enforces the cross-workstream External
Send Gate (Foundation Mission v6 Invariant 1) via the GATED_SCRIPTS list
that intake.py is already enrolled in. This file pins the intake-specific
nuances surfaced in the R3 session 1 brief that aren't part of the
generic GATED_SCRIPTS contract:

  1. No import from any `*_send*` module (e.g. the future `weekly_send`).
  2. No import path with `send` as a segment, even if the parent module
     might be otherwise legitimate.
  3. The capability surface of intake.py specifically — no plain
     `requests` import (Smartsheet/Box wrapping is via `shared/*`).

These pins exist so a future maintainer adding e.g. a "send_followup"
helper module to safety_reports doesn't accidentally enable a back-door
send capability for intake.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INTAKE_PATH = REPO_ROOT / "safety_reports" / "intake.py"


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


def test_intake_does_not_import_any_send_named_module():
    """No import segment may contain 'send'. Covers weekly_send + future helpers."""
    imports = _imports_in(INTAKE_PATH)
    offenders = [imp for imp in imports if "send" in imp.split(".")]
    assert not offenders, (
        f"safety_reports/intake.py imports {offenders!r} — one or more segments "
        f"contain 'send'. External Send Gate violation."
    )


def test_intake_does_not_import_send_capable_libraries():
    """smtplib, email.mime.*, and `resend` are absent from intake imports."""
    imports = _imports_in(INTAKE_PATH)
    forbidden_prefixes = ("smtplib", "email.mime", "resend")
    offenders = sorted(
        imp for imp in imports if any(imp.startswith(p) for p in forbidden_prefixes)
    )
    assert not offenders, (
        f"safety_reports/intake.py imports {offenders!r}. "
        f"None of {forbidden_prefixes} are allowed in an intake script."
    )


def test_intake_does_not_directly_import_requests():
    """intake.py routes all HTTP through `shared/*` wrappers; no direct
    `requests` import — keeps the SMTP/email-relay attack surface OFF the
    file even via an indirect HTTP path."""
    imports = _imports_in(INTAKE_PATH)
    assert "requests" not in imports, (
        "safety_reports/intake.py imports `requests` directly. All HTTP "
        "must go through shared/* wrappers (smartsheet_client, box_client, "
        "anthropic_client). Direct requests import re-opens the SMTP/email-"
        "relay attack surface this file is designed to gate."
    )
