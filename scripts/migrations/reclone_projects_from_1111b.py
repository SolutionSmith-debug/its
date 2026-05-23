"""Post-1111B canonical cutover — archive legacy + re-clone projects from 1111B.

Closes the loop on the 1111B blueprint absorbed in PR #67 and materialized
in PR #70. Replaces the 6 legacy 1111A-derived project clones with
fresh clones of 1111B (the canonical blueprint), archiving the legacy
folders for audit trail.

Execution order (Box requires unique names within a parent):

  1. **Archive** the 6 legacy clones first. Create
     `ITS DATA / 99. Legacy 1111A Clones / ` and move each old project
     folder there, renamed to `<Project> (legacy 1111A)`. Sort-to-end
     `99.` prefix per 1111B convention so the legacy area doesn't clutter
     the active workspace.
  2. **Re-clone** 1111B six times under ITS DATA, named with each
     project's display name (Bradley 1, Bradley 2, ..., Rockford).
     Uses `copy_with_lock_retry` (Box source-folder-lock retry, 20-min
     budget per PR #56 evidence). Box deep-copy is async; we wait for
     the top-level children to populate before considering the clone
     done.
  3. **Verify** each new clone against the 1111B blueprint
     (`RENAME_MAP` target names from `box_build_1111b_blueprint`).
     267-descendant count + every RENAME_MAP target present at its
     expected path.
  4. **Emit a mapping JSON** at
     `~/its/logs/migrations/reclone_1111b_folder_ids.json` mapping each
     project slug to `{old_id, new_id, status}` so the downstream
     `BOX_PROJECT_FOLDERS` update knows exactly which IDs to swap.

Re-runnable idempotently. If a project is already cutover (legacy
already in the archive area, new canonical clone already present and
verified), the script reports `completed_previously` and continues.
Per-project failure is logged + counted, the run continues to the
remaining projects.

CLI:

  python scripts/migrations/reclone_projects_from_1111b.py
  python scripts/migrations/reclone_projects_from_1111b.py --project bradley_1
  python scripts/migrations/reclone_projects_from_1111b.py --dry-run
  python scripts/migrations/reclone_projects_from_1111b.py --verify-only

Scope discipline:
  - Mirror tenant only.
  - 1111A and the 6 legacy clones are archived, NOT deleted.
  - This script does NOT update `shared/defaults.py BOX_PROJECT_FOLDERS`
    — that's a code change handled in the same PR but as a separate
    edit step (after this script's mapping JSON is generated).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from boxsdk.exception import BoxAPIException  # type: ignore[import-untyped]

from shared import box_client
from shared.defaults import BOX_PROJECT_FOLDERS
from shared.error_log import its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "scripts.migrations.reclone_projects_from_1111b"

# Box constants (mirror tenant).
PARENT_FOLDER_ID = "382010286207"  # ITS DATA
SOURCE_1111B_ID = "383696567483"  # 1111B (Copy for new projects) — materialized PR #70

# Archive area for the legacy 1111A-derived clones.
LEGACY_ARCHIVE_FOLDER_NAME = "99. Legacy 1111A Clones"
LEGACY_SUFFIX = " (legacy 1111A)"

# Expected descendant count per cloned project (matches 1111B itself).
EXPECTED_DESCENDANT_COUNT = 267

# Top-level children expected after deep-copy populates. Matches 1111B's
# top-level: 12 Portfolio folders + (Project # & Name) Field + (Project # & Name) Office.
EXPECTED_TOPLEVEL_COUNT = 14

# Lock-retry budget for Box deep-copy. Same as PR #56 / PR #70.
LOCK_RETRY_MAX_ATTEMPTS = 40
LOCK_RETRY_WAIT_SECONDS = 30

# Deep-copy polling budget per project clone.
DEEP_COPY_TIMEOUT_SECONDS = 1200  # 20 minutes per project (1111B is bigger than 1111A subfolders)
DEEP_COPY_POLL_INTERVAL_SECONDS = 15

# Logging.
LOG_DIR = Path.home() / "its" / "logs" / "migrations"
LOG_PATH = LOG_DIR / "reclone_projects_from_1111b.log"
MAPPING_JSON_PATH = LOG_DIR / "reclone_1111b_folder_ids.json"
COMPLIANCE_REPORT_PATH_FMT = LOG_DIR / "reclone_project_{slug}_report.txt"

log = logging.getLogger("reclone_projects_from_1111b")

# Project slugs → display names. Single source of truth for the 6-project
# cutover. Slugs match `shared/defaults.py BOX_PROJECT_FOLDERS` keys
# (lowercased + underscored) so the mapping JSON keys stay consistent.
PROJECT_SLUG_TO_NAME: dict[str, str] = {
    "bradley_1": "Bradley 1",
    "bradley_2": "Bradley 2",
    "brimfield_1": "Brimfield 1",
    "brimfield_2": "Brimfield 2",
    "huntley": "Huntley",
    "rockford": "Rockford",
}


# ---- Logging setup ------------------------------------------------------


def _configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = logging.FileHandler(LOG_PATH, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    log.addHandler(stream_handler)


# ---- Helpers (replicated per preservation; mirrors box_build_1111b_blueprint) ----


def _is_lock_error(exc: BoxAPIException) -> bool:
    if exc.status != 500:
        return False
    message = (exc.message or "").lower()
    return "lock" in message


def _find_child(client: Any, parent_id: str, name: str) -> str | None:
    items = client.folder(parent_id).get_items(
        limit=200, fields=["id", "name", "type"]
    )
    for item in items:
        if item.type == "folder" and item.name == name:
            return str(item.id)
    return None


def _count_child_folders(client: Any, folder_id: str) -> int:
    items = client.folder(folder_id).get_items(
        limit=200, fields=["id", "name", "type"]
    )
    return sum(1 for item in items if item.type == "folder")


def _count_all_descendants(client: Any, folder_id: str) -> int:
    """Recursive descendant count (including self)."""
    count = 1
    items = client.folder(folder_id).get_items(
        limit=200, fields=["id", "name", "type"]
    )
    for item in items:
        if item.type == "folder":
            count += _count_all_descendants(client, str(item.id))
    return count


def copy_with_lock_retry(
    client: Any,
    source_id: str,
    parent_id: str,
    name: str,
    *,
    max_attempts: int = LOCK_RETRY_MAX_ATTEMPTS,
    wait_seconds: int = LOCK_RETRY_WAIT_SECONDS,
) -> str:
    """Clone `source_id` → `parent_id/name`; retry on lock errors.

    Same lock-retry pattern as PR #56 (`box_clone_1111a_to_projects`) and
    PR #70 (`box_build_1111b_blueprint`). Bails on non-lock errors.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            new_folder = client.folder(source_id).copy(
                parent_folder=client.folder(parent_id),
                name=name,
            )
            return str(new_folder.id)
        except BoxAPIException as e:
            if not _is_lock_error(e):
                raise
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Lock-retry budget exhausted after {max_attempts} attempts "
                    f"({max_attempts * wait_seconds}s) cloning {source_id} -> "
                    f"{parent_id} as {name!r}. Last error: HTTP {e.status}: {e.message}"
                ) from e
            log.info(
                "[lock] attempt %s/%s: source locked, waiting %ss",
                attempt,
                max_attempts,
                wait_seconds,
            )
            time.sleep(wait_seconds)
    raise AssertionError("unreachable")  # pragma: no cover


def wait_for_deep_copy_complete(
    client: Any,
    folder_id: str,
    *,
    expected_count: int = EXPECTED_TOPLEVEL_COUNT,
    timeout_seconds: int = DEEP_COPY_TIMEOUT_SECONDS,
    poll_interval: int = DEEP_COPY_POLL_INTERVAL_SECONDS,
) -> tuple[bool, int]:
    """Poll until `folder_id` has >= `expected_count` direct sub-folders."""
    deadline = time.time() + timeout_seconds
    current = 0
    while time.time() < deadline:
        current = _count_child_folders(client, folder_id)
        if current >= expected_count:
            return True, current
        time.sleep(poll_interval)
    return False, current


# ---- Blueprint compliance check (subset of PR #70's verify_blueprint) --


def _load_rename_map() -> dict[tuple[str, str], str]:
    """Load RENAME_MAP from the 1111B blueprint module via sys.path."""
    migrations_dir = Path(__file__).resolve().parent
    if str(migrations_dir) not in sys.path:
        sys.path.insert(0, str(migrations_dir))
    import box_build_1111b_blueprint as build_mod  # noqa: E402

    return build_mod.RENAME_MAP


def _resolve_path(client: Any, root_id: str, parent_path: str) -> str | None:
    """Walk root_id by slash-separated parent_path. Returns None on missing segment."""
    if parent_path == "":
        return root_id
    current = root_id
    for segment in parent_path.split("/"):
        child_id = _find_child(client, current, segment)
        if child_id is None:
            return None
        current = child_id
    return current


def verify_clone(
    client: Any, root_id: str, project_name: str
) -> tuple[bool, str]:
    """Verify a project clone matches the 1111B blueprint. Returns (passed, report)."""
    rename_map = _load_rename_map()
    lines: list[str] = []
    lines.append(
        f"COMPLIANCE REPORT — {project_name} — "
        f"generated {datetime.now(UTC).isoformat()}"
    )
    lines.append(f"Root folder_id: {root_id}")
    lines.append("")

    descendant_count = _count_all_descendants(client, root_id)
    total_pass = descendant_count == EXPECTED_DESCENDANT_COUNT
    lines.append(
        f"[{'PASS' if total_pass else 'FAIL'}] Total folder count: "
        f"{descendant_count} (expected {EXPECTED_DESCENDANT_COUNT})"
    )
    lines.append("")
    lines.append("Per-folder target verification (RENAME_MAP):")

    targets_present = 0
    targets_missing = 0
    for (parent_path, _src), target_name in rename_map.items():
        parent_id = _resolve_path(client, root_id, parent_path)
        if parent_id is None:
            lines.append(
                f"  [FAIL] parent path {parent_path!r} unresolved (expected target {target_name!r})"
            )
            targets_missing += 1
            continue
        target_id = _find_child(client, parent_id, target_name)
        if target_id is None:
            lines.append(
                f"  [FAIL] {parent_path!r}/{target_name!r} missing"
            )
            targets_missing += 1
        else:
            lines.append(
                f"  [PASS] {parent_path!r}/{target_name!r} (folder_id={target_id})"
            )
            targets_present += 1

    lines.append("")
    lines.append("Summary:")
    lines.append(f"  Targets present: {targets_present}")
    lines.append(f"  Targets missing: {targets_missing}")
    lines.append(
        f"  Total folders: {descendant_count} (expected {EXPECTED_DESCENDANT_COUNT})"
    )
    passed = total_pass and targets_missing == 0
    lines.append("")
    lines.append(f"OVERALL: {'PASS' if passed else 'FAIL'}")
    return passed, "\n".join(lines) + "\n"


def _write_compliance_report(slug: str, report: str) -> Path:
    path = Path(str(COMPLIANCE_REPORT_PATH_FMT).format(slug=slug))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report)
    return path


# ---- Phase A: archive legacy clones ------------------------------------


def ensure_legacy_archive_folder(client: Any, *, dry_run: bool = False) -> str:
    """Ensure `ITS DATA / 99. Legacy 1111A Clones` exists; return its ID."""
    existing = _find_child(client, PARENT_FOLDER_ID, LEGACY_ARCHIVE_FOLDER_NAME)
    if existing is not None:
        log.info(
            "legacy archive folder already exists at folder_id=%s; skipping create",
            existing,
        )
        return existing
    if dry_run:
        log.info(
            "[dry-run] would create %r under parent %s",
            LEGACY_ARCHIVE_FOLDER_NAME,
            PARENT_FOLDER_ID,
        )
        return "(dry-run)"
    log.info(
        "creating archive folder %r under parent %s",
        LEGACY_ARCHIVE_FOLDER_NAME,
        PARENT_FOLDER_ID,
    )
    new = client.folder(PARENT_FOLDER_ID).create_subfolder(LEGACY_ARCHIVE_FOLDER_NAME)
    return str(new.id)


def archive_one_legacy(
    client: Any,
    *,
    slug: str,
    archive_folder_id: str,
    dry_run: bool = False,
) -> tuple[str, str | None]:
    """Move + rename one legacy clone into the archive. Returns (status, archived_id_or_None).

    Status values:
      - `archived`: legacy folder was just moved + renamed.
      - `already_archived`: archive already has a folder named `<Project> (legacy 1111A)`.
      - `legacy_missing`: no folder with the legacy ID was found under ITS DATA AND
        no archived version was found either. Either operator moved it manually or
        the slug's legacy ID in BOX_PROJECT_FOLDERS is stale.
    """
    project_name = PROJECT_SLUG_TO_NAME[slug]
    archived_name = f"{project_name}{LEGACY_SUFFIX}"
    legacy_id = BOX_PROJECT_FOLDERS[project_name]

    if archive_folder_id != "(dry-run)":
        existing_archived = _find_child(client, archive_folder_id, archived_name)
        if existing_archived is not None:
            log.info(
                "[%s] already archived as %r (folder_id=%s); skipping",
                slug,
                archived_name,
                existing_archived,
            )
            return "already_archived", existing_archived

    if dry_run:
        log.info(
            "[dry-run] [%s] would move legacy folder_id=%s under archive %s and rename to %r",
            slug,
            legacy_id,
            archive_folder_id,
            archived_name,
        )
        return "archived", legacy_id

    # Confirm legacy still lives under ITS DATA root before moving.
    legacy_parent = _find_child(client, PARENT_FOLDER_ID, project_name)
    if legacy_parent is None or legacy_parent != legacy_id:
        log.warning(
            "[%s] legacy folder_id=%s not found under ITS DATA as %r — skipping archive",
            slug,
            legacy_id,
            project_name,
        )
        return "legacy_missing", None

    log.info(
        "[%s] moving legacy folder_id=%s -> archive %s and renaming to %r",
        slug,
        legacy_id,
        archive_folder_id,
        archived_name,
    )
    client.folder(legacy_id).move(client.folder(archive_folder_id), name=archived_name)
    return "archived", legacy_id


# ---- Phase B: re-clone 1111B per project --------------------------------


def reclone_one_project(
    client: Any,
    *,
    slug: str,
    dry_run: bool = False,
    force_replace_partial: bool = False,
) -> tuple[str, str | None]:
    """Clone 1111B → `ITS DATA / <Project>`. Returns (status, new_id_or_None).

    Status values:
      - `cloned`: fresh clone created from 1111B.
      - `existing_matches`: a folder with the canonical name already exists
        AND verification passes — skip and report idempotently.
      - `existing_mismatched`: a folder with the canonical name exists but
        does NOT match 1111B's shape. Without `force_replace_partial`,
        refuses to overwrite (operator must resolve manually). With
        `force_replace_partial=True`, deletes the partial clone (safe
        because legacy was archived first; the canonical name is reserved
        for the new 1111B clone) and re-clones.
    """
    project_name = PROJECT_SLUG_TO_NAME[slug]
    existing = _find_child(client, PARENT_FOLDER_ID, project_name)
    if existing is not None:
        # Check if it's a 1111B-shape clone (proxy: descendant count).
        if dry_run:
            log.info(
                "[dry-run] [%s] %r already exists at folder_id=%s; would verify shape",
                slug,
                project_name,
                existing,
            )
            return "existing_matches", existing
        count = _count_all_descendants(client, existing)
        if count == EXPECTED_DESCENDANT_COUNT:
            log.info(
                "[%s] %r already exists at folder_id=%s with correct descendant count "
                "(%s); treating as already-cloned",
                slug,
                project_name,
                existing,
                count,
            )
            return "existing_matches", existing
        if force_replace_partial:
            log.warning(
                "[%s] %r at folder_id=%s has descendant count %s (expected %s); "
                "--force-replace-partial enabled, DELETING partial clone before re-clone",
                slug,
                project_name,
                existing,
                count,
                EXPECTED_DESCENDANT_COUNT,
            )
            client.folder(existing).delete(recursive=True)
            existing = None  # fall through to clone path
        else:
            log.warning(
                "[%s] %r already exists at folder_id=%s with descendant count %s "
                "(expected %s); not safe to clone over (re-run with "
                "--force-replace-partial to delete + retry)",
                slug,
                project_name,
                existing,
                count,
                EXPECTED_DESCENDANT_COUNT,
            )
            return "existing_mismatched", existing

    if dry_run:
        log.info(
            "[dry-run] [%s] would clone 1111B (%s) -> %s as %r",
            slug,
            SOURCE_1111B_ID,
            PARENT_FOLDER_ID,
            project_name,
        )
        return "cloned", "(dry-run)"

    log.info(
        "[%s] cloning 1111B (%s) -> %s as %r",
        slug,
        SOURCE_1111B_ID,
        PARENT_FOLDER_ID,
        project_name,
    )
    new_id = copy_with_lock_retry(
        client,
        source_id=SOURCE_1111B_ID,
        parent_id=PARENT_FOLDER_ID,
        name=project_name,
    )
    log.info(
        "[%s] clone returned folder_id=%s; waiting for deep-copy to populate top-level",
        slug,
        new_id,
    )
    completed, top_count = wait_for_deep_copy_complete(client, new_id)
    if not completed:
        log.warning(
            "[%s] deep-copy timeout — only %s/%s top-level children populated within "
            "budget; verification will surface specific gaps",
            slug,
            top_count,
            EXPECTED_TOPLEVEL_COUNT,
        )
    else:
        log.info(
            "[%s] deep-copy top-level populated (%s/%s); proceeding",
            slug,
            top_count,
            EXPECTED_TOPLEVEL_COUNT,
        )

    # Box deep-copy of descendants continues async after top-level shows up.
    # Wait for the full descendant count to reach EXPECTED_DESCENDANT_COUNT
    # before declaring success — otherwise verification false-fails on a
    # still-populating tree.
    deadline = time.time() + DEEP_COPY_TIMEOUT_SECONDS
    while time.time() < deadline:
        count = _count_all_descendants(client, new_id)
        if count >= EXPECTED_DESCENDANT_COUNT:
            log.info(
                "[%s] full descendant count reached %s/%s; clone complete",
                slug,
                count,
                EXPECTED_DESCENDANT_COUNT,
            )
            break
        log.info(
            "[%s] deep-copy in progress: %s/%s descendants — sleeping %ss",
            slug,
            count,
            EXPECTED_DESCENDANT_COUNT,
            DEEP_COPY_POLL_INTERVAL_SECONDS,
        )
        time.sleep(DEEP_COPY_POLL_INTERVAL_SECONDS)
    else:
        log.warning(
            "[%s] deep-copy descendant-count timeout — verification will surface gaps",
            slug,
        )

    return "cloned", new_id


# ---- Mapping JSON -------------------------------------------------------


def _write_mapping_json(mapping: dict[str, dict[str, Any]]) -> None:
    MAPPING_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAPPING_JSON_PATH.write_text(json.dumps(mapping, indent=2) + "\n")
    log.info("wrote folder-id mapping to %s", MAPPING_JSON_PATH)


def _load_mapping_json() -> dict[str, dict[str, Any]] | None:
    if not MAPPING_JSON_PATH.exists():
        return None
    try:
        parsed = json.loads(MAPPING_JSON_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---- Public flow --------------------------------------------------------


def cutover_one_project(
    client: Any,
    *,
    slug: str,
    archive_folder_id: str,
    dry_run: bool = False,
    force_replace_partial: bool = False,
) -> dict[str, Any]:
    """Archive legacy + clone 1111B + verify, all for one project."""
    archive_status, archived_id = archive_one_legacy(
        client, slug=slug, archive_folder_id=archive_folder_id, dry_run=dry_run
    )
    clone_status, new_id = reclone_one_project(
        client, slug=slug, dry_run=dry_run, force_replace_partial=force_replace_partial
    )

    verify_passed = False
    report_path: Path | None = None
    if not dry_run and new_id is not None and new_id != "(dry-run)":
        verify_passed, report = verify_clone(
            client, new_id, PROJECT_SLUG_TO_NAME[slug]
        )
        report_path = _write_compliance_report(slug, report)

    return {
        "slug": slug,
        "project_name": PROJECT_SLUG_TO_NAME[slug],
        "old_id": BOX_PROJECT_FOLDERS[PROJECT_SLUG_TO_NAME[slug]],
        "new_id": new_id,
        "archive_status": archive_status,
        "clone_status": clone_status,
        "verify_passed": verify_passed,
        "compliance_report": str(report_path) if report_path else None,
    }


def verify_only_one_project(client: Any, slug: str) -> dict[str, Any]:
    """Verify-only mode: walk the project's current clone against 1111B blueprint."""
    project_name = PROJECT_SLUG_TO_NAME[slug]
    existing = _find_child(client, PARENT_FOLDER_ID, project_name)
    if existing is None:
        log.warning("[%s] %r not found under ITS DATA — nothing to verify", slug, project_name)
        return {
            "slug": slug,
            "project_name": project_name,
            "new_id": None,
            "verify_passed": False,
            "error": "folder_missing",
        }
    passed, report = verify_clone(client, existing, project_name)
    report_path = _write_compliance_report(slug, report)
    return {
        "slug": slug,
        "project_name": project_name,
        "new_id": existing,
        "verify_passed": passed,
        "compliance_report": str(report_path),
    }


@its_error_log(SCRIPT_NAME)
@require_active
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Cutover the 6 project clones from 1111A-derived to 1111B-derived. "
            "Archives legacy folders + re-clones from 1111B + verifies each + "
            "emits a folder-id mapping JSON the downstream BOX_PROJECT_FOLDERS "
            "update consumes."
        ),
    )
    parser.add_argument(
        "--project",
        choices=sorted(PROJECT_SLUG_TO_NAME.keys()),
        default=None,
        help="Operate on one project slug; defaults to all 6.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned operations without writes.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify-only: walk each project clone vs. 1111B blueprint; no writes.",
    )
    parser.add_argument(
        "--force-replace-partial",
        action="store_true",
        help=(
            "When the canonical-name folder already exists but has the WRONG "
            "descendant count (a partial clone from an interrupted prior run), "
            "DELETE the partial folder and re-clone. Safe because legacy was "
            "archived first; the canonical name is reserved for the new 1111B "
            "clone. Use only when re-running after a bash-timeout-killed run."
        ),
    )
    args = parser.parse_args(argv)

    _configure_logging()
    client = box_client.get_client()

    project_slugs = (
        [args.project] if args.project is not None else list(PROJECT_SLUG_TO_NAME.keys())
    )

    if args.verify_only:
        log.info("--verify-only mode: skipping archive + clone phases")
        results: dict[str, dict[str, Any]] = {}
        for slug in project_slugs:
            results[slug] = verify_only_one_project(client, slug)
        log.info("verify-only complete: %s", json.dumps(results, indent=2))
        all_passed = all(r.get("verify_passed") for r in results.values())
        return 0 if all_passed else 1

    log.info(
        "starting cutover (dry_run=%s, projects=%s)",
        args.dry_run,
        project_slugs,
    )

    archive_folder_id = ensure_legacy_archive_folder(client, dry_run=args.dry_run)

    mapping: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for slug in project_slugs:
        try:
            result = cutover_one_project(
                client,
                slug=slug,
                archive_folder_id=archive_folder_id,
                dry_run=args.dry_run,
                force_replace_partial=args.force_replace_partial,
            )
        except Exception as exc:  # noqa: BLE001 — per-project fence
            log.error("[%s] cutover failed: %r", slug, exc)
            mapping[slug] = {
                "old_id": BOX_PROJECT_FOLDERS[PROJECT_SLUG_TO_NAME[slug]],
                "new_id": None,
                "status": "failed",
                "error": repr(exc),
            }
            failures.append(slug)
            continue
        mapping[slug] = {
            "old_id": result["old_id"],
            "new_id": result["new_id"],
            "status": (
                "completed_previously"
                if result["archive_status"] == "already_archived"
                and result["clone_status"] == "existing_matches"
                else "cutover_complete"
            ),
            "verify_passed": result["verify_passed"],
            "compliance_report": result["compliance_report"],
        }

    if not args.dry_run:
        _write_mapping_json(mapping)

    log.info(
        "cutover summary: %s",
        json.dumps(
            {
                "projects": len(project_slugs),
                "failures": failures,
                "mapping_json": str(MAPPING_JSON_PATH) if not args.dry_run else "(dry-run)",
            },
            indent=2,
        ),
    )
    if failures:
        return 1
    if not args.dry_run and not all(
        m.get("verify_passed", False) for m in mapping.values() if m.get("new_id")
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
