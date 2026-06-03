"""ITS_Project_Routing sheet reads — project name → Box folder ID (E1).

Replaces the hardcoded `shared.defaults.BOX_PROJECT_FOLDERS` dict as the
canonical project→Box-folder mapping, so a non-developer can onboard a project
by adding a row to the `ITS_Project_Routing` sheet instead of editing code +
redeploying. Mirrors the `shared.trusted_contacts` sheet-read pattern (Op Stds
v16 §33 family): TTL-cached read, typed-row projection, sheet-not-wired and
read-failure both degrade to the `BOX_PROJECT_FOLDERS` fallback (never crash).

Sheet schema (one row per project):
    Project Name    TEXT_NUMBER (primary, exact-match key)
    Box Folder ID   TEXT_NUMBER (the project's Box folder under ITS DATA)
    Active          CHECKBOX    (false = retired; excluded from resolution)
    Notes           TEXT_NUMBER

Cutover (mirrors the trusted-contacts cluster; FLIP precedes SEED):
    1. `scripts/migrations/build_its_project_routing_sheet.py` builds the sheet.
    2. Flip `SHEET_PROJECT_ROUTING` in `shared/sheet_ids.py` to the new id.
    3. `scripts/migrations/seed_its_project_routing.py` seeds it from
       `BOX_PROJECT_FOLDERS` (reads the flipped constant, so it follows step 2).
Until step 2 (the flip), `SHEET_PROJECT_ROUTING == 0` and every lookup falls back
to `BOX_PROJECT_FOLDERS` — i.e. behavior is unchanged pre-cutover (no regression).

In-process cache: 60-second TTL, same rationale as `trusted_contacts` — a
single 60-second `intake_poll` cycle reuses cached state; an operator edit
takes effect on the next cycle.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from . import defaults, sheet_ids, smartsheet_client

LOGGER = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class ProjectRoute:
    """One ITS_Project_Routing row projected to typed form."""

    project_name: str
    box_folder_id: str
    active: bool
    notes: str
    row_id: int


_cache: tuple[list[ProjectRoute], float] | None = None


def _row_to_route(row: dict[str, Any]) -> ProjectRoute | None:
    """Project one Smartsheet row dict to a ProjectRoute, or None on bad data."""
    raw_name = row.get("Project Name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    raw_folder = row.get("Box Folder ID")
    # Smartsheet may return a numeric folder ID as int/float; coerce to a
    # digit string (Box folder IDs are opaque numeric strings).
    if isinstance(raw_folder, bool):  # bool is an int subclass — never a folder ID
        box_folder_id = ""
    elif isinstance(raw_folder, (int, float)):
        box_folder_id = str(int(raw_folder))
    elif isinstance(raw_folder, str):
        box_folder_id = raw_folder.strip()
    else:
        box_folder_id = ""
    row_id = row.get("_row_id")
    if not isinstance(row_id, int):
        return None
    return ProjectRoute(
        project_name=raw_name.strip(),
        box_folder_id=box_folder_id,
        # CHECKBOX reads as a Python bool; a blank/absent cell is falsy →
        # treat as inactive (deny-by-default for a half-filled row).
        active=bool(row.get("Active")),
        notes=str(row.get("Notes") or ""),
        row_id=row_id,
    )


def _load_routes() -> list[ProjectRoute]:
    """Fetch + cache the ITS_Project_Routing sheet. TTL-keyed at module scope.

    Returns an empty list (cached) when the sheet is not yet wired
    (`SHEET_PROJECT_ROUTING == 0` → SmartsheetNotFoundError) or on a transient
    read failure — the caller's `BOX_PROJECT_FOLDERS` fallback covers both, and
    caching empty avoids hammering Smartsheet during cutover / an outage.
    """
    global _cache
    now = time.monotonic()
    if _cache is not None:
        routes, expires_at = _cache
        if now < expires_at:
            return routes

    try:
        rows = smartsheet_client.get_rows(sheet_ids.SHEET_PROJECT_ROUTING)
    except smartsheet_client.SmartsheetError as exc:
        # Sheet not wired (id 0 → 404) or a transient error: degrade to the
        # defaults fallback, never crash the intake cycle. Cache empty so the
        # next lookups in this cycle don't re-hit Smartsheet.
        if not isinstance(exc, smartsheet_client.SmartsheetNotFoundError):
            LOGGER.warning("project_routing: sheet read failed (%r); using fallback", exc)
        routes = []
        _cache = (routes, now + CACHE_TTL_SECONDS)
        return routes

    routes = [r for r in (_row_to_route(row) for row in rows) if r is not None]
    _cache = (routes, now + CACHE_TTL_SECONDS)
    return routes


def invalidate_cache() -> None:
    """Drop the in-process cache. Used by tests + ad-hoc operator scripts."""
    global _cache
    _cache = None


def get_folder_id(project_name: str) -> str:
    """Resolve a project's Box folder ID: ITS_Project_Routing (Active) → the
    `BOX_PROJECT_FOLDERS` defaults fallback → `""` on a total miss.

    Warn-not-crash: a miss returns `""` (the caller soft-fails to its error
    list), never raises. When the sheet IS wired but the project resolves only
    from the hardcoded fallback, a WARN flags the onboarding gap (add the
    project to the sheet); pre-cutover (sheet id 0) the fallback is expected and
    silent.
    """
    for route in _load_routes():
        if route.project_name == project_name and route.active:
            return route.box_folder_id

    fallback = defaults.BOX_PROJECT_FOLDERS.get(project_name, "")
    if fallback and sheet_ids.SHEET_PROJECT_ROUTING:
        LOGGER.warning(
            "project_routing: %r resolved from the hardcoded BOX_PROJECT_FOLDERS "
            "fallback, not ITS_Project_Routing — add it to the sheet.",
            project_name,
        )
    return fallback
