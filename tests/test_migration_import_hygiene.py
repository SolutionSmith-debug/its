"""Import-hygiene tests for the smartsheet_migration scripts.

Ensures that three one-off migration scripts can be imported without
running their module-level work — historically they called `get_sheet()`
at import time, which required `SMARTSHEET_TOKEN` to be set in the env.
Per the M4 tech_debt entry, top-level API work was wrapped in
`if __name__ == "__main__":`; these tests lock that in so the regression
doesn't recur silently.

Run with: pytest -q tests/test_migration_import_hygiene.py
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

MIGRATION_DIR = Path(__file__).resolve().parent.parent / "smartsheet_migration"
if str(MIGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(MIGRATION_DIR))


@pytest.mark.parametrize(
    "module_name",
    [
        "inspect_closeout",
        "inspect_source_schedule",
        "migrate_schedule_dryrun",
    ],
)
def test_module_imports_without_smartsheet_token(module_name, monkeypatch):
    """Importing must NOT require SMARTSHEET_TOKEN in env.

    Pre-fix behavior: top-level `get_sheet(...)` call ran during import,
    which forced the env var to be set or import would raise. Post-fix:
    the API work moved into `main()` behind `if __name__ == "__main__":`,
    so importing the module is a no-op for env requirements.

    If this test regresses, look for module-level code that calls
    `ss_api.api()` / `ss_api.get_sheet()` etc. instead of being inside
    `main()`.
    """
    monkeypatch.delenv("SMARTSHEET_TOKEN", raising=False)
    # Force a fresh import; importlib caches across tests in the same
    # session and we want the no-token-set path to actually exercise.
    sys.modules.pop(module_name, None)
    importlib.import_module(module_name)
