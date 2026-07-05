"""One-shot migration: replace the always-empty `Started`/`Ended` columns on the live per-job
`<Job> — Hours Log` sheets with a single `Task` column (2026-07-05 Task-column change).

The portal daily-report time form never populates the wall-clock start/end times, so the Hours Log
carried two permanently-blank columns; meanwhile every time entry already records `task_id`
(→ `task_assignments.description`), which is the field crews actually want. The code change (worker
`/hours-pending` projection, `progress_reports/hours_log.py` schema, the `fieldops_sync` hours pass)
only affects NEWLY created sheets — this migration brings the EXISTING live sheets into line.

TWO PHASES — ORDER-CRITICAL (smartsheet_client.add_rows RAISES KeyError on an unknown column
title, so the running daemon must never write to a column that does not exist):

  1. python3 scripts/migrations/hours_log_task_column.py --phase add --commit
     Run BEFORE deploying the new Mac code. Adds `Task` (idempotent). The still-running OLD daemon
     keeps writing Started/Ended (still present → no KeyError) and simply leaves the new Task column
     blank; the OLD Worker's `work_started_at`/`_ended_at` response fields are ignored by nothing
     yet — this phase is purely additive and safe at any time.

  2. operator deploys the Worker (`npm run deploy`) + the Mac daemon (`git -C ~/its pull origin
     main`). The new daemon writes `Task` (now present ✓) and stops writing Started/Ended.

  3. python3 scripts/migrations/hours_log_task_column.py --phase drop --commit
     Run AFTER the new Mac code is live (it no longer writes Started/Ended → dropping them cannot
     KeyError). Deletes `Started` + `Ended` (idempotent).

PREVIEW is the default (no `--commit`) — it lists the matched sheets and the exact column change,
writing nothing. NAME-GUARDED: only sheets whose title ends in `" — Hours Log"` are ever touched.
Idempotent: a present Task / an absent Started-Ended is skipped, so re-running a phase is a clean
no-op. Developer-Operator one-shot (a live Smartsheet schema write; §43 low-class once the deploys
land). Auth: `ITS_SMARTSHEET_TOKEN` from macOS Keychain.

CLI:
    python3 scripts/migrations/hours_log_task_column.py --phase add             # PREVIEW
    python3 scripts/migrations/hours_log_task_column.py --phase add   --commit  # add Task
    python3 scripts/migrations/hours_log_task_column.py --phase drop  --commit  # drop Started/Ended
    # optional: --sheet-id <id> (repeatable) to skip discovery and target explicit sheet(s).

Exit 0 on success/no-op; nonzero on error.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import requests  # type: ignore[import-untyped]

# Allow running from repo root without installing the package.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from progress_reports import hours_log  # noqa: E402
from shared import keychain  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"

SUFFIX = hours_log.SHEET_SUFFIX          # " — Hours Log" — the name-guard
COL_TASK = hours_log.COL_TASK            # "Task"
COL_HOURS = hours_log.COL_HOURS          # "Hours" (Task is inserted right after it)
COL_STARTED = hours_log.COL_STARTED if hasattr(hours_log, "COL_STARTED") else "Started"
COL_ENDED = hours_log.COL_ENDED if hasattr(hours_log, "COL_ENDED") else "Ended"
DROP_TITLES = (COL_STARTED, COL_ENDED)
TASK_TYPE = "TEXT_NUMBER"


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_json(path: str) -> dict[str, Any]:
    r = requests.get(BASE + path, headers=_headers())
    r.raise_for_status()
    return r.json()


def _post_json(path: str, body: Any) -> dict[str, Any]:
    r = requests.post(BASE + path, headers=_headers(), json=body)
    r.raise_for_status()
    return r.json()


def _delete_json(path: str) -> dict[str, Any]:
    r = requests.delete(BASE + path, headers=_headers())
    r.raise_for_status()
    return r.json()


def _discover_hours_log_sheets() -> list[tuple[int, str]]:
    """Find every sheet whose title ends in the `" — Hours Log"` name-guard via /search.

    Returns [(sheet_id, name), …]. The name-guard makes this safe to run without an explicit
    --sheet-id: only Hours Log sheets are ever returned (and re-verified per sheet below).
    """
    data = _get_json("/search?query=Hours%20Log")
    out: list[tuple[int, str]] = []
    for r in data.get("results", []):
        if r.get("objectType") != "sheet":
            continue
        name = str(r.get("text") or "")
        sid = r.get("objectId") or r.get("id")
        if name.endswith(SUFFIX) and isinstance(sid, int):
            out.append((sid, name))
    return out


def _resolve_targets(explicit: list[int]) -> list[tuple[int, str]]:
    if explicit:
        targets: list[tuple[int, str]] = []
        for sid in explicit:
            sheet = _get_json(f"/sheets/{sid}?include=columns")
            targets.append((sid, str(sheet.get("name") or "")))
        return targets
    return _discover_hours_log_sheets()


def _guard(name: str) -> None:
    if not name.endswith(SUFFIX):
        raise RuntimeError(
            f"refusing to touch sheet {name!r}: name does not end in {SUFFIX!r} (name-guard)."
        )


def _add_task(sheet_id: int, name: str, *, commit: bool) -> str:
    """Add the `Task` TEXT_NUMBER column right after `Hours` if absent. Idempotent."""
    _guard(name)
    sheet = _get_json(f"/sheets/{sheet_id}?include=columns")
    columns = sheet.get("columns", [])
    by_title = {c["title"]: c for c in columns}
    if COL_TASK in by_title:
        if by_title[COL_TASK]["type"] != TASK_TYPE:
            raise RuntimeError(
                f"sheet={sheet_id} {name!r}: column {COL_TASK!r} exists but type="
                f"{by_title[COL_TASK]['type']!r}, not {TASK_TYPE} — Tier-3 schema fix."
            )
        print(f"  [skip] {name!r}: {COL_TASK!r} already present ({TASK_TYPE}).")
        return "exists"
    hours_col = by_title.get(COL_HOURS)
    index = (hours_col["index"] + 1) if hours_col else len(columns)
    if not commit:
        print(f"  [preview] {name!r}: would CREATE {COL_TASK!r} ({TASK_TYPE}) at index={index}.")
        return "would-create"
    result = _post_json(
        f"/sheets/{sheet_id}/columns", [{"title": COL_TASK, "type": TASK_TYPE, "index": index}]
    )
    created = result.get("result", [])
    if not created:
        raise RuntimeError(f"unexpected column-create response: {result!r}")
    print(f"  [ok] {name!r}: created {COL_TASK!r} (id={created[0]['id']}, index={index}).")
    return "created"


def _drop_started_ended(sheet_id: int, name: str, *, commit: bool) -> int:
    """Delete `Started` + `Ended` if present. Idempotent. Returns the number deleted (or would)."""
    _guard(name)
    sheet = _get_json(f"/sheets/{sheet_id}?include=columns")
    by_title = {c["title"]: c for c in sheet.get("columns", [])}
    n = 0
    for title in DROP_TITLES:
        col = by_title.get(title)
        if col is None:
            print(f"  [skip] {name!r}: {title!r} already absent.")
            continue
        n += 1
        if not commit:
            print(f"  [preview] {name!r}: would DELETE {title!r} (id={col['id']}).")
            continue
        _delete_json(f"/sheets/{sheet_id}/columns/{col['id']}")
        print(f"  [ok] {name!r}: deleted {title!r} (id={col['id']}).")
    return n


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hours Log: add the Task column / drop the retired Started+Ended columns.",
    )
    parser.add_argument("--phase", required=True, choices=["add", "drop"],
                        help="add = create Task (run BEFORE the code deploy); "
                             "drop = delete Started/Ended (run AFTER the code deploy).")
    parser.add_argument("--commit", action="store_true",
                        help="Actually write. Without it, PREVIEW only (no write).")
    parser.add_argument("--sheet-id", type=int, action="append", default=[],
                        help="Target sheet ID(s); repeatable. Omit to auto-discover by name.")
    args = parser.parse_args()

    print(f"[info] Mode: {'LIVE WRITE (--commit)' if args.commit else 'PREVIEW (default)'}")
    print(f"[info] Phase: {args.phase}  (name-guard suffix {SUFFIX!r})")

    targets = _resolve_targets(args.sheet_id)
    if not targets:
        print("[info] No matching Hours Log sheets found — nothing to do (new sheets are created "
              "with the Task column already).")
        return 0
    print(f"[info] Matched {len(targets)} sheet(s):")
    for sid, name in targets:
        print(f"         {sid}  {name!r}")
    print()

    if args.phase == "add":
        for sid, name in targets:
            _add_task(sid, name, commit=args.commit)
    else:
        total = 0
        for sid, name in targets:
            total += _drop_started_ended(sid, name, commit=args.commit)
        print()
        verb = "deleted" if args.commit else "would delete"
        print(f"[info] {verb} {total} column(s) across {len(targets)} sheet(s).")

    if not args.commit:
        print("\nRe-run with --commit to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
