"""Capability-gating tests for Foundation Mission v6 Invariant 1 (External Send Gate).

The architectural invariant: scripts that call the Anthropic API to generate customer-facing
content MUST NOT have the capability to send externally. Scripts that send externally MUST
NOT have an AI step. Enforced by static import inspection.

A successful prompt injection at the AI layer cannot cause external transmission, because
the AI is in a different process from the transmitter.

How to extend this test:
- When a new generation script lands, add it to GATED_SCRIPTS.
- When a new send script lands, add it to SEND_SCRIPTS.

The lists are currently empty because the Safety Reports two-process refactor (`weekly_generate.py`
+ `weekly_send.py`) hasn't landed yet. The test framework is in place — adding new entries is
the entire enforcement mechanism for new workstreams.

Run with: pytest -q tests/test_capability_gating.py
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Generation scripts: must NOT import any send capability.
# Each entry: (relative path from repo root, list of forbidden import substrings)
GATED_SCRIPTS: list[tuple[str, list[str]]] = [
    # ("safety_reports/weekly_generate.py", ["graph_client", "send_mail"]),
    # ("po_materials/standard_rfq_generate.py", ["graph_client", "send_mail"]),
    # ("po_materials/racking_module_rfq_generate.py", ["graph_client", "send_mail"]),
    # ("subcontracts/subcontract_generate.py", ["graph_client", "send_mail"]),
]

# Send scripts: must NOT import any AI capability.
SEND_SCRIPTS: list[tuple[str, list[str]]] = [
    # ("safety_reports/weekly_send.py", ["anthropic_client", "anthropic"]),
    # ("po_materials/rfq_send.py", ["anthropic_client", "anthropic"]),
    # ("subcontracts/subcontract_send.py", ["anthropic_client", "anthropic"]),
]


def _imports_in(path: Path) -> set[str]:
    """Return the set of imported module names + from-imports in a Python file."""
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


@pytest.mark.parametrize("rel_path,forbidden", GATED_SCRIPTS)
def test_generation_script_does_not_import_send(rel_path: str, forbidden: list[str]):
    """Generation scripts must not import any send capability (Invariant 1)."""
    path = REPO_ROOT / rel_path
    assert path.exists(), f"missing: {rel_path}"
    imports = _imports_in(path)
    for needle in forbidden:
        for imp in imports:
            assert needle not in imp, (
                f"{rel_path} imports {imp!r}, which contains forbidden {needle!r}. "
                "External Send Gate violation — generation scripts cannot have send capability."
            )


@pytest.mark.parametrize("rel_path,forbidden", SEND_SCRIPTS)
def test_send_script_does_not_import_ai(rel_path: str, forbidden: list[str]):
    """Send scripts must not import any AI capability (Invariant 1)."""
    path = REPO_ROOT / rel_path
    assert path.exists(), f"missing: {rel_path}"
    imports = _imports_in(path)
    for needle in forbidden:
        for imp in imports:
            assert needle not in imp, (
                f"{rel_path} imports {imp!r}, which contains forbidden {needle!r}. "
                "External Send Gate violation — send scripts cannot have AI capability."
            )


def test_lists_documented():
    """Sanity check — both lists exist and are typed correctly even when empty."""
    assert isinstance(GATED_SCRIPTS, list)
    assert isinstance(SEND_SCRIPTS, list)
