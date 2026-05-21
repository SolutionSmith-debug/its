"""Sanity test for scripts/migrations/box_clone_1111a_to_projects.py.

The migration script is operational (talks to Box's live API + waits for
async deep-copies); meaningful unit-level coverage would require mocking
the entire boxsdk surface, which is high-effort low-value. The one
worthwhile invariant is the project-list parity between this script and
`shared.sheet_ids.PROJECT_NAME_BY_FOLDER_ID` — drift between the two
would mean a 7th project added to Smartsheet without a matching Box
clone (or vice versa), and the failure mode would only surface at R3
session 1 wiring time. This test pins the parity.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

MIGRATION_DIR = Path(__file__).resolve().parent.parent / "scripts" / "migrations"
if str(MIGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(MIGRATION_DIR))


def test_projects_match_smartsheet_project_name_map():
    """Script `PROJECTS` must equal `sheet_ids.PROJECT_NAME_BY_FOLDER_ID` values.

    Catches drift where a 7th project lands in `shared/sheet_ids.py` (via
    `FOLDER_PROJECT_*` constants + `PROJECT_NAME_BY_FOLDER_ID` map) but
    the Box-side cascade script doesn't grow a matching entry — or the
    inverse. Set equality, order-insensitive, no value comparison
    (Box folder IDs live in `shared.defaults.BOX_PROJECT_FOLDERS`, not
    here).
    """
    from shared import sheet_ids

    sys.modules.pop("box_clone_1111a_to_projects", None)
    box_clone = importlib.import_module("box_clone_1111a_to_projects")

    assert set(box_clone.PROJECTS) == set(
        sheet_ids.PROJECT_NAME_BY_FOLDER_ID.values()
    )
