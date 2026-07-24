"""Arg-parsing tests for scripts/smoke_test_graph.py (PR-B6, Phase-1 cutover).

The Graph smoke test is an ATTENDED operational script — these tests never
touch the network or Keychain; they only pin the argparse surface:

- a no-args parse resolves to the sandbox (mirror) defaults, so a bare run
  behaves byte-identically to the pre-parameterization script;
- all three mailbox roles (granted/send-from, recipient, denied-probe) are
  overridable for a production-tenant run;
- ``--help`` exits 0 before any network/Keychain access.

The script is imported via the same sys.path-insert pattern as
tests/test_watchdog.py so ``import smoke_test_graph`` resolves the script
as a top-level module.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import smoke_test_graph  # noqa: E402  — must come after sys.path insertion above


def test_defaults_are_the_mirror_mailboxes() -> None:
    """No-args parse == the pre-parameterization hardcoded sandbox values."""
    args = smoke_test_graph.parse_args([])
    assert args.mailbox == "safety@evergreenmirror.com"
    assert args.to == "seths@evergreenmirror.com"
    assert args.denied_mailbox == "jacobs@evergreenmirror.com"


def test_all_three_mailbox_roles_are_overridable() -> None:
    args = smoke_test_graph.parse_args(
        [
            "--mailbox",
            "its@example.test",
            "--to",
            "operator@example.test",
            "--denied-mailbox",
            "persona@example.test",
        ]
    )
    assert args.mailbox == "its@example.test"
    assert args.to == "operator@example.test"
    assert args.denied_mailbox == "persona@example.test"


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """--help must short-circuit (exit 0) before any Keychain/network step."""
    with pytest.raises(SystemExit) as exc:
        smoke_test_graph.parse_args(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--mailbox" in out
    assert "--denied-mailbox" in out


def test_short_display_helper() -> None:
    assert smoke_test_graph._short("safety@evergreenmirror.com") == "safety@"
    assert smoke_test_graph._short("its@example.test") == "its@"
