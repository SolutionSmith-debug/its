"""One-shot migration: clone the 1111A template into the 6 Forefront project folders.

Companion to PR #54 (R3 foundation). Closes the last Box-side prerequisite
for R3 session 1 (intake.py wiring): the empty-string values in
`shared.defaults.BOX_PROJECT_FOLDERS` get replaced with real Box folder
IDs sourced from this script's live output.

What it does
------------

For each of the 6 Forefront projects (Bradley 1, Bradley 2, Brimfield 1,
Brimfield 2, Huntley, Rockford):

  1. Look up by name under `PARENT_FOLDER_ID` (ITS DATA).
  2. If found:
      - Verify deep-copy completeness (poll until N subfolders >=
        EXPECTED_SUBFOLDER_COUNT or timeout). WARN-log if timeout.
      - Print `EXISTS: <project> -> <folder_id> (n/14 subfolders)`.
  3. If missing:
      - Initiate copy from `SOURCE_FOLDER_ID` (1111A template) with
        lock-aware retry (HTTP 500 / 'locked' messages).
      - Poll for deep-copy completeness.
      - Print `CREATED: <project> -> <folder_id> (n/14 subfolders)`.

End-of-run prints a 6-line `BOX_PROJECT_FOLDERS` snippet for paste into
`shared/defaults.py`.

Idempotency
-----------

Re-running with all 6 folders present makes zero copy calls, prints 6
EXISTS lines, exits 0 with the same 6 tuples. Safe to invoke repeatedly.

The lock gotcha
---------------

Box's async deep-copy holds a server-side lock on the source folder for
the duration of the operation. Subsequent copies (UI or API) from the
same source fail with HTTP 500 + "You cannot copy this folder because
either the source or destination folder is currently locked by another
operation" until the lock clears. Lock duration is variable; observed
range ~30s to several minutes for the 269-file / 14-subfolder template.

Mitigation: `copy_with_lock_retry` waits 30s between attempts, up to 40
attempts (20-minute total budget per copy). Hammering Box's queue does
not speed it up; the wait is non-shortenable.

Auth
----

`ITS_BOX_CLIENT_ID` / `ITS_BOX_CLIENT_SECRET` / `ITS_BOX_REFRESH_TOKEN`
in macOS Keychain (same path as runtime). Refresh-token rotation
persists transparently via `shared.box_client._store_tokens`.

Run from `~/its` with the venv activated:

    python3 scripts/migrations/box_clone_1111a_to_projects.py 2>&1 | tee /tmp/box_clone_output.log

Exit code 0 on success or full-idempotent re-run; nonzero only on
unrecoverable errors (auth failure, parent folder missing, etc.).
"""
from __future__ import annotations

import sys
import time
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from boxsdk.exception import BoxAPIException  # type: ignore[import-untyped]  # noqa: E402

from shared.box_client import get_client  # noqa: E402

# ---- Constants ----------------------------------------------------------

SOURCE_FOLDER_ID = "382384021749"  # 1111A (Copy for new projects)
PARENT_FOLDER_ID = "382010286207"  # ITS DATA
EXPECTED_SUBFOLDER_COUNT = 14
PROJECTS: list[str] = [
    "Bradley 1",
    "Bradley 2",
    "Brimfield 1",
    "Brimfield 2",
    "Huntley",
    "Rockford",
]

# Lock-retry tuning. See module docstring "The lock gotcha".
LOCK_RETRY_MAX_ATTEMPTS = 40
LOCK_RETRY_WAIT_SECONDS = 30

# Deep-copy completeness tuning.
DEEP_COPY_TIMEOUT_SECONDS = 600  # 10 minutes per folder
DEEP_COPY_POLL_INTERVAL_SECONDS = 10


# ---- Helpers ------------------------------------------------------------


def _is_lock_error(exc: BoxAPIException) -> bool:
    """Return True if `exc` looks like a Box source-folder-lock failure.

    Lock failures surface as HTTP 500 with a message containing 'locked'
    or 'lock' (case-insensitive). Distinguishing from generic 500s
    matters — we want to retry locks but bail on real server errors.
    """
    if exc.status != 500:
        return False
    message = (exc.message or "").lower()
    return "lock" in message


def _count_child_folders(client: Any, folder_id: str) -> int:
    """Return the number of sub-folders directly inside `folder_id`."""
    items = client.folder(folder_id).get_items(
        limit=100, fields=["id", "name", "type"]
    )
    return sum(1 for item in items if item.type == "folder")


def _find_project_folder_id(client: Any, parent_id: str, name: str) -> str | None:
    """Return the Box folder ID of `name` under `parent_id`, or None.

    Match by exact case-sensitive name. Box does not enforce uniqueness
    within a folder by convention; first match wins.
    """
    items = client.folder(parent_id).get_items(
        limit=200, fields=["id", "name", "type"]
    )
    for item in items:
        if item.type == "folder" and item.name == name:
            return str(item.id)
    return None


def copy_with_lock_retry(
    client: Any,
    source_id: str,
    parent_id: str,
    name: str,
    *,
    max_attempts: int = LOCK_RETRY_MAX_ATTEMPTS,
    wait_seconds: int = LOCK_RETRY_WAIT_SECONDS,
) -> str:
    """Clone `source_id` into `parent_id` as `name`; retry on lock errors.

    Returns the new folder ID. Retries on HTTP 500 + 'lock' in message.
    Bails on any other error (4xx name conflicts, perm denials, etc.).
    Budget: max_attempts * wait_seconds (default 40 * 30s = 20 min).
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
            print(
                f"    [lock] Attempt {attempt}/{max_attempts}: source locked, "
                f"waiting {wait_seconds}s ...",
                flush=True,
            )
            time.sleep(wait_seconds)
    # Unreachable; the loop either returns or raises.
    raise AssertionError("unreachable")


def wait_for_deep_copy_complete(
    client: Any,
    folder_id: str,
    *,
    expected_count: int = EXPECTED_SUBFOLDER_COUNT,
    timeout_seconds: int = DEEP_COPY_TIMEOUT_SECONDS,
    poll_interval: int = DEEP_COPY_POLL_INTERVAL_SECONDS,
) -> tuple[bool, int]:
    """Poll until `folder_id` has >= `expected_count` sub-folders.

    Box's deep-copy is async; the folder appears immediately with
    metadata but the child structure populates over seconds-to-minutes.

    Returns (completed, current_count). `completed=True` if the count
    reached `expected_count` within the budget; `completed=False` on
    timeout (folder still exists with whatever partial count we observed).
    """
    deadline = time.time() + timeout_seconds
    current = 0
    while time.time() < deadline:
        current = _count_child_folders(client, folder_id)
        if current >= expected_count:
            return True, current
        time.sleep(poll_interval)
    return False, current


# ---- Main flow ----------------------------------------------------------


def ensure_one_project(client: Any, project_name: str) -> tuple[str, str, int]:
    """Find-or-create the per-project clone. Returns (status, folder_id, count).

    `status` is "exists" if the folder already existed, "created"
    if this run created it. `count` is the observed sub-folder count
    (may be less than EXPECTED_SUBFOLDER_COUNT on deep-copy timeout —
    WARN-logged but not fatal).
    """
    existing = _find_project_folder_id(client, PARENT_FOLDER_ID, project_name)
    if existing is not None:
        # Already cloned; just verify completeness.
        completed, count = wait_for_deep_copy_complete(client, existing)
        if not completed:
            print(
                f"  [warn] {project_name}: deep-copy not complete after "
                f"{DEEP_COPY_TIMEOUT_SECONDS}s ({count}/{EXPECTED_SUBFOLDER_COUNT} "
                f"subfolders). Folder ID is valid; structure will fill in "
                f"asynchronously."
            )
        return "exists", existing, count

    print(f"  [copy] Initiating clone -> {project_name!r} ...", flush=True)
    new_id = copy_with_lock_retry(
        client, SOURCE_FOLDER_ID, PARENT_FOLDER_ID, project_name
    )
    print(f"  [copy] Clone initiated; folder_id={new_id}. Polling deep-copy ...",
          flush=True)
    completed, count = wait_for_deep_copy_complete(client, new_id)
    if not completed:
        print(
            f"  [warn] {project_name}: deep-copy not complete after "
            f"{DEEP_COPY_TIMEOUT_SECONDS}s ({count}/{EXPECTED_SUBFOLDER_COUNT} "
            f"subfolders). Folder ID is valid; structure will fill in "
            f"asynchronously."
        )
    return "created", new_id, count


def main() -> int:
    print(f"[info] Source folder = {SOURCE_FOLDER_ID} (1111A template)")
    print(f"[info] Parent folder = {PARENT_FOLDER_ID} (ITS DATA)")
    print(f"[info] Projects to ensure: {PROJECTS}")
    print()

    client = get_client()

    results: list[tuple[str, str, str, int]] = []  # (project, status, id, count)
    for project in PROJECTS:
        print(f"[{project}]")
        status, folder_id, count = ensure_one_project(client, project)
        prefix = "EXISTS" if status == "exists" else "CREATED"
        print(
            f"  {prefix}: {project} -> {folder_id} "
            f"({count}/{EXPECTED_SUBFOLDER_COUNT} subfolders)",
            flush=True,
        )
        results.append((project, status, folder_id, count))
        print()

    print("=" * 70)
    print("Summary:")
    for project, status, folder_id, count in results:
        print(
            f"  {project:<14s} {status:<8s} {folder_id:<16s} "
            f"({count}/{EXPECTED_SUBFOLDER_COUNT} subfolders)"
        )
    print()
    print("Paste into shared/defaults.py BOX_PROJECT_FOLDERS:")
    print()
    for project, _, folder_id, _ in results:
        print(f'    {project!r:<16s}: "{folder_id}",')
    return 0


if __name__ == "__main__":
    sys.exit(main())
