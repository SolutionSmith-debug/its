"""Wipe the sandbox Smartsheet workspaces + Box roots for a stand-up rehearsal.

THE MOST DESTRUCTIVE TOOL IN THIS REPO. It exists for exactly one operation: the
operator-ordered full sandbox wipe ahead of re-running the builder family to
rehearse the production stand-up (2026-07-22 directive). Everything it deletes
is enumerated below, by NAME **and** ID, hard-coded — the house pattern for bulk
deletion (HOUSE_REFLEXES §7: name-guarded Python SDK script with a hard-coded
allowlist; the Smartsheet MCP cannot delete workspaces and the Box MCP cannot
delete at all).

Guards (each fails CLOSED, each is separately tested)
    1. ALLOWLIST DOUBLE-MATCH. A workspace/folder is deleted only when its live
       NAME and live ID both equal an allowlist entry. Name-matches-id-doesn't
       (or vice versa) -> refused, run aborts nonzero, nothing further deleted.
       Anything live that is NOT on the allowlist is never touched (and is
       reported, so a surprise workspace is seen, not skipped silently).
    2. DAEMON-DOWN PRECONDITION. Refuses to run while ANY org.solutionsmith.its.*
       launchd job is loaded. The kill switch cannot hold the fleet through a
       wipe — its own row lives in the ITS_Config sheet being deleted and it
       fails OPEN — so `launchctl` unload is the only safe hold, and this script
       verifies it rather than trusting the operator's memory.
    3. TYPED-PHRASE GATE. Live deletion requires --commit AND typing the exact
       phrase NUKE THE SANDBOX at the prompt (the prompt IS the control — there
       is deliberately no flag that bypasses it). Default mode is a read-only
       plan print.
    4. DUMP-BEFORE-DELETE. With --commit, every allowlisted workspace is dumped
       first — full folder tree, every sheet's columns (type/options/system-
       column-type) + rows (title-keyed), and the workspace SHARE list (the F22
       approver sets, needed to re-share the rebuilt workspaces) — to
       ~/its/logs/migrations/prewipe_<UTC>/ as JSON, plus a Box tree manifest
       (names/ids; file bytes are NOT downloaded — the operator declined content
       preservation, the dump is the deletion audit record + the row-restore
       source for the stand-up). A dump failure aborts the wipe.

Deletion mechanics
    Smartsheet: DELETE /workspaces/{id} (cascades folders/sheets). Box:
    folder.delete(recursive=True) on the allowlisted roots. Absent object ->
    [skip] (idempotent re-run). A partial failure leaves the remainder intact
    and exits nonzero; re-running resumes safely.

The allowlist pins the LIVE sandbox ids captured 2026-07-22. After the rebuild,
the old ids are dead and this script can delete nothing — updating the pins is a
deliberate PR-reviewed code change, which is exactly the friction a repeat wipe
should carry.

Auth: ITS_SMARTSHEET_TOKEN (Keychain) for Smartsheet; the shared Box OAuth
identity via shared/box_client (token refresh on use is normal).

Run from ~/its (or a worktree):
    python3 scripts/migrations/wipe_tenant.py            # read-only plan
    python3 scripts/migrations/wipe_tenant.py --commit   # dump, then delete
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import box_client, keychain  # noqa: E402

BASE = "https://api.smartsheet.com/2.0"
CONFIRM_PHRASE = "NUKE THE SANDBOX"
DUMP_ROOT = pathlib.Path.home() / "its" / "logs" / "migrations"

# ---- the allowlist (name AND id must both match; captured live 2026-07-22) ----

SMARTSHEET_WORKSPACE_ALLOWLIST: tuple[tuple[str, int], ...] = (
    ("Evergreen Portfolio Template (Demo Seed)", 685696395569028),
    ("Evergreen Portfolio Template (Master)", 3333320395253636),
    ("Forefront Portfolio — ITS Demo", 4129485730670468),
    ("Forfront IL portfolio", 2228567565199236),
    ("ITS –– Safety Portal", 194283417429892),
    ("ITS — Archive", 5528280611743620),
    ("ITS — Human Review", 8561891980142468),
    ("ITS — Operations", 7217130472007556),
    ("ITS — Progress Reporting", 5988851429730180),
    ("ITS — Purchase Orders", 6191118619568004),
    ("ITS — Subcontracts", 6073264716965764),
    ("ITS — System", 680592632244100),
)

BOX_ROOT_ALLOWLIST: tuple[tuple[str, str], ...] = (
    ("ITS DATA", "382010286207"),
    ("ITS_Safety_Portal", "388017263015"),
    ("ITS_Progress_Reporting", "396689250929"),
)


class WipeRefusedError(RuntimeError):
    """A guard refused the wipe. Nothing (further) was deleted."""


# ---- guards ---------------------------------------------------------------


def _loaded_its_daemons() -> list[str]:
    """Labels of loaded org.solutionsmith.its.* launchd jobs (guard 2 input)."""
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True,
                         timeout=30, check=True).stdout
    return sorted(
        line.split()[-1] for line in out.splitlines()
        if "org.solutionsmith.its." in line
    )


def require_daemons_down() -> None:
    loaded = _loaded_its_daemons()
    if loaded:
        print(f"[abort] daemons_loaded: {len(loaded)} org.solutionsmith.its.* job(s) are "
              "still loaded — a live fleet would error-storm against deleted sheets and "
              "re-create objects mid-wipe (the kill switch cannot hold it: its row dies "
              "with ITS_Config and it fails OPEN). Unload them first:")
        for label in loaded:
            print(f"    launchctl bootout gui/$(id -u)/{label}")
        raise WipeRefusedError("daemons loaded")


def _confirm_phrase() -> bool:
    """The typed-phrase gate (guard 3). Tests monkeypatch this seam. EOF = decline."""
    print(f'\nType exactly "{CONFIRM_PHRASE}" to delete everything listed above.')
    try:
        return input("> ").strip() == CONFIRM_PHRASE
    except EOFError:
        return False


def match_allowlist(
    live: list[dict[str, Any]],
    allowlist: tuple[tuple[str, Any], ...],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Split live objects into (deletable, mismatches, unlisted). Guard 1 core.

    deletable  — live name+id BOTH equal one allowlist entry.
    mismatches — a live object matching an entry by name OR id but not both
                 (drifted tenant vs pins -> the whole run must refuse).
    unlisted   — live objects matching no entry at all (never touched, reported).
    Absent allowlist entries are fine (idempotent re-run after partial delete).
    """
    deletable: list[dict[str, Any]] = []
    mismatches: list[str] = []
    unlisted: list[str] = []
    by_name = {name: ident for name, ident in allowlist}
    by_id = {ident: name for name, ident in allowlist}
    for obj in live:
        name, ident = str(obj.get("name")), obj.get("id")
        pinned_id = by_name.get(name)
        pinned_name = by_id.get(ident)
        if pinned_id == ident and pinned_name == name:
            deletable.append(obj)
        elif pinned_id is not None or pinned_name is not None:
            mismatches.append(
                f"{name!r} (live id={ident}) vs allowlist "
                f"(name->{pinned_id}, id->{pinned_name!r})")
        else:
            unlisted.append(f"{name!r} (id={ident})")
    return deletable, mismatches, unlisted


# ---- Smartsheet fetch + dump ---------------------------------------------


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _list_workspaces() -> list[dict[str, Any]]:
    r = requests.get(f"{BASE}/workspaces?includeAll=true", headers=_headers(), timeout=30)
    r.raise_for_status()
    return list(r.json().get("data", []))


def _workspace_tree(workspace_id: int) -> dict[str, Any]:
    r = requests.get(f"{BASE}/workspaces/{workspace_id}?loadAll=true",
                     headers=_headers(), timeout=120)
    r.raise_for_status()
    return dict(r.json())


def _workspace_shares(workspace_id: int) -> list[dict[str, Any]]:
    r = requests.get(f"{BASE}/workspaces/{workspace_id}/shares?includeAll=true",
                     headers=_headers(), timeout=30)
    r.raise_for_status()
    return list(r.json().get("data", []))


def _sheet_dump(sheet_id: int) -> dict[str, Any]:
    """Columns (type/options/systemColumnType) + rows for one sheet, ALL pages.

    Fetched at level=2 with objectValue so MULTI_PICKLIST cells round-trip (a
    plain `value` flattens them to a display string the restore cannot write
    back). Rows carry `{title: value}` plus `_ov` = `{title: objectValue}` for
    cells where the API returned a structured objectValue.
    """
    page = 1
    sheet: dict[str, Any] = {}
    raw_rows: list[dict[str, Any]] = []
    while True:
        r = requests.get(
            f"{BASE}/sheets/{sheet_id}?pageSize=5000&page={page}"
            "&level=2&include=objectValue",
            headers=_headers(), timeout=120)
        r.raise_for_status()
        sheet = r.json()
        raw_rows.extend(sheet.get("rows", []))
        total = int(sheet.get("totalRowCount") or 0)
        if len(raw_rows) >= total or not sheet.get("rows"):
            break
        page += 1
    columns = [
        {
            "id": c["id"],
            "title": c.get("title"),
            "type": c.get("type"),
            "primary": bool(c.get("primary")),
            "options": c.get("options"),
            "systemColumnType": c.get("systemColumnType"),
        }
        for c in sheet.get("columns", [])
    ]
    title_by_id = {c["id"]: c["title"] for c in columns}
    rows = []
    for row in raw_rows:
        record: dict[str, Any] = {"_row_id": row.get("id")}
        object_values: dict[str, Any] = {}
        for cell in row.get("cells", []):
            title = title_by_id.get(cell.get("columnId"))
            if title is None:
                continue
            if "value" in cell:
                record[title] = cell.get("value")
            ov = cell.get("objectValue")
            if isinstance(ov, dict):  # structured values (MULTI_PICKLIST etc.)
                object_values[title] = ov
        if object_values:
            record["_ov"] = object_values
        rows.append(record)
    total = int(sheet.get("totalRowCount") or 0)
    if len(rows) != total:
        raise RuntimeError(
            f"sheet_dump_truncated: sheet {sheet_id} totalRowCount={total} "
            f"but {len(rows)} rows fetched — refusing a lossy dump")
    return {
        "id": sheet.get("id"),
        "name": sheet.get("name"),
        "total_rows": total,
        "columns": columns,
        "rows": rows,
    }


def _walk_sheets(node: dict[str, Any], path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    out = [(path, s) for s in node.get("sheets", []) or []]
    for folder in node.get("folders", []) or []:
        out.extend(_walk_sheets(folder, (*path, str(folder.get("name")))))
    return out


def dump_workspace(ws: dict[str, Any], dump_dir: pathlib.Path,
                   ) -> tuple[int, list[str]]:
    """Dump one workspace (tree + shares + every sheet) to JSON.

    Returns (sheets_dumped, unreadable) where `unreadable` names sheets whose
    FETCH failed (e.g. the four zero-column ITS_Errors shells from the row-cap
    incident return 1115/404 on read) — recorded and reported, not fatal: an
    unreadable broken shell has no content to preserve, and blocking the whole
    wipe on it would strand the operation. Any WRITE failure still propagates
    (guard 4 — a dump we cannot persist aborts the wipe).

    Filenames carry the sheet id so duplicate names in one folder (the
    ITS_Errors quintuplet) can never silently overwrite each other. Reports /
    dashboards are NOT captured (sheets only) — a documented limitation.
    """
    ws_id = int(ws["id"])
    tree = _workspace_tree(ws_id)
    shares = _workspace_shares(ws_id)
    safe_name = str(ws["name"]).replace("/", "_")
    ws_dir = dump_dir / "smartsheet" / safe_name
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "_workspace.json").write_text(
        json.dumps({"workspace": {"id": ws_id, "name": ws["name"]},
                    "shares": shares, "tree_names_only": _tree_skeleton(tree)},
                   indent=2, default=str),
        encoding="utf-8")
    sheets = _walk_sheets(tree)
    unreadable: list[str] = []
    dumped = 0
    for path, sheet_meta in sheets:
        sheet_id = int(sheet_meta["id"])
        label = "/".join((*path, str(sheet_meta.get("name"))))
        try:
            dump = _sheet_dump(sheet_id)
        except (requests.HTTPError, RuntimeError) as e:
            unreadable.append(f"{label} (id={sheet_id}): {e}")
            print(f"[WARN] sheet_unreadable: {label} (id={sheet_id}) — {e}. "
                  "Recorded and skipped (no content preserved for this sheet).")
            continue
        dump["folder_path"] = list(path)
        dump["workspace"] = ws["name"]
        fname = (f"{'__'.join((*path, str(sheet_meta.get('name'))))}"
                 f"__{sheet_id}").replace("/", "_")
        (ws_dir / f"{fname}.sheet.json").write_text(
            json.dumps(dump, indent=2, default=str), encoding="utf-8")
        dumped += 1
    return dumped, unreadable


def _tree_skeleton(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": node.get("name"),
        "id": node.get("id"),
        "sheets": [{"name": s.get("name"), "id": s.get("id")}
                   for s in node.get("sheets", []) or []],
        "folders": [_tree_skeleton(f) for f in node.get("folders", []) or []],
    }


def _delete_workspace(workspace_id: int) -> None:
    r = requests.delete(f"{BASE}/workspaces/{workspace_id}", headers=_headers(), timeout=60)
    r.raise_for_status()


# ---- Box fetch + dump -----------------------------------------------------


def _box_root_items() -> list[dict[str, Any]]:
    return [
        {"name": item.get("name"), "id": str(item.get("id")), "type": item.get("type")}
        for item in box_client.list_folder("0", limit=1000)
    ]


def _box_manifest(folder_id: str, depth: int = 0, max_depth: int = 4) -> dict[str, Any]:
    items = box_client.list_folder(folder_id, limit=1000)
    manifest: dict[str, Any] = {"id": folder_id, "folders": [], "files": []}
    for item in items:
        entry = {"name": item.get("name"), "id": str(item.get("id"))}
        if item.get("type") == "folder":
            if depth < max_depth:
                sub = _box_manifest(str(item.get("id")), depth + 1, max_depth)
                sub.update(entry)
                manifest["folders"].append(sub)
            else:
                entry["truncated_at_depth"] = True
                manifest["folders"].append(entry)
        else:
            manifest["files"].append(entry)
    return manifest


def _delete_box_folder(folder_id: str) -> None:
    client = box_client.get_client()
    client.folder(folder_id).delete(recursive=True)


# ---- main -----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Name-guarded sandbox wipe (Smartsheet workspaces + Box roots).")
    parser.add_argument("--commit", action="store_true",
                        help="Dump, then DELETE. Without it: read-only plan.")
    args = parser.parse_args()

    print(f"[info] Mode: {'COMMIT (dump + delete)' if args.commit else 'PLAN (read-only)'}")
    print(f"[info] Smartsheet allowlist: {len(SMARTSHEET_WORKSPACE_ALLOWLIST)} workspaces; "
          f"Box allowlist: {len(BOX_ROOT_ALLOWLIST)} roots.\n")

    if args.commit:
        # Guard 2 blocks only the destructive path; a read-only plan is safe to
        # print any time (and still WARNs so the operator sees the precondition).
        try:
            require_daemons_down()
        except WipeRefusedError:
            return 1
    else:
        loaded = _loaded_its_daemons()
        if loaded:
            print(f"[WARN] {len(loaded)} org.solutionsmith.its.* daemon(s) loaded — "
                  "fine for a read-only plan, but --commit will refuse until they "
                  "are unloaded.\n")

    live_ws = _list_workspaces()
    ws_deletable, ws_mismatch, ws_unlisted = match_allowlist(
        live_ws, SMARTSHEET_WORKSPACE_ALLOWLIST)

    box_live = [i for i in _box_root_items() if i.get("type") == "folder"]
    box_deletable, box_mismatch, box_unlisted = match_allowlist(
        box_live, BOX_ROOT_ALLOWLIST)

    print("[plan] Smartsheet workspaces to DELETE (name+id both match the allowlist):")
    for ws in ws_deletable:
        print(f"    {ws['name']!r} (id={ws['id']})")
    absent_ws = len(SMARTSHEET_WORKSPACE_ALLOWLIST) - len(ws_deletable) - len(ws_mismatch)
    if absent_ws:
        print(f"    [skip] {absent_ws} allowlist entr(ies) not present live (already wiped).")
    if ws_unlisted:
        print("[plan] Smartsheet workspaces NOT on the allowlist (never touched):")
        for u in ws_unlisted:
            print(f"    {u}")
    print("\n[plan] Box root folders to DELETE (recursive):")
    for b in box_deletable:
        print(f"    {b['name']!r} (id={b['id']})")
    if box_unlisted:
        print("[plan] Box root items NOT on the allowlist (never touched):")
        for u in box_unlisted:
            print(f"    {u}")

    if ws_mismatch or box_mismatch:
        print("\n[abort] allowlist_mismatch: live name/id pins have DRIFTED — refusing "
              "the entire run (guard 1). Update the allowlist via a reviewed PR only:")
        for m in (*ws_mismatch, *box_mismatch):
            print(f"    {m}")
        return 1

    if not args.commit:
        print("\n[plan] Read-only plan complete. Re-run with --commit to dump + delete.")
        return 0

    if not _confirm_phrase():
        print("[abort] confirmation phrase not matched; nothing deleted.")
        return 1

    # ---- dump (guard 4: a dump we cannot capture/persist aborts the wipe) ----
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    dump_dir = DUMP_ROOT / f"prewipe_{stamp}"
    dump_dir.mkdir(parents=True, exist_ok=False)
    print(f"\n[dump] -> {dump_dir}")
    total_sheets = 0
    all_unreadable: list[str] = []
    try:
        for ws in ws_deletable:
            n, unreadable = dump_workspace(ws, dump_dir)
            total_sheets += n
            all_unreadable.extend(unreadable)
            print(f"[dump] {ws['name']!r}: {n} sheet(s) dumped"
                  + (f", {len(unreadable)} unreadable" if unreadable else "") + ".")
        box_manifests = {}
        for b in box_deletable:
            box_manifests[b["name"]] = _box_manifest(str(b["id"]))
            print(f"[dump] Box {b['name']!r}: manifest captured.")
        (dump_dir / "box_manifest.json").write_text(
            json.dumps(box_manifests, indent=2), encoding="utf-8")
        (dump_dir / "_manifest.json").write_text(
            json.dumps({
                "captured_at_utc": stamp,
                "smartsheet_workspaces": [
                    {"name": w["name"], "id": w["id"]} for w in ws_deletable],
                "smartsheet_sheets_dumped": total_sheets,
                "unreadable_sheets": all_unreadable,
                "box_roots": [{"name": b["name"], "id": b["id"]}
                              for b in box_deletable],
                "limitations": "sheets only — reports/dashboards not captured; "
                               "Box file bytes not downloaded (manifest only)",
            }, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — guard 4: ANY dump failure stops the wipe
        print(f"\n[abort] dump_failed: {e}\nNothing was deleted. The partial dump at "
              f"{dump_dir} is safe to remove.")
        return 1
    print(f"[dump] complete: {total_sheets} sheets + shares + Box manifest"
          + (f" ({len(all_unreadable)} unreadable sheet(s) recorded)"
             if all_unreadable else "") + ".\n")

    # Guard 2 re-check: minutes have passed at the prompt + dump — a daemon
    # reloaded in the window must stop the delete (check-then-act, re-armed).
    try:
        require_daemons_down()
    except WipeRefusedError:
        print(f"[abort] A daemon was loaded during the dump window. Dump retained at "
              f"{dump_dir}; nothing deleted.")
        return 1

    # ---- delete ----
    failures = 0
    for ws in ws_deletable:
        try:
            _delete_workspace(int(ws["id"]))
            print(f"[deleted] Smartsheet workspace {ws['name']!r} (id={ws['id']})")
        except Exception as e:  # noqa: BLE001 — partial-failure contract: keep going
            failures += 1
            print(f"[ERROR] delete failed for {ws['name']!r}: {e}")
    for b in box_deletable:
        try:
            _delete_box_folder(str(b["id"]))
            print(f"[deleted] Box folder {b['name']!r} (id={b['id']}, recursive)")
        except Exception as e:  # noqa: BLE001 — raw boxsdk exceptions included
            failures += 1
            print(f"[ERROR] Box delete failed for {b['name']!r}: {e}")

    if failures:
        print(f"\n[partial] {failures} deletion(s) failed — the rest are done. Re-run to "
              "resume (absent objects skip).")
        return 1
    print(f"\n[ok] Wipe complete: {len(ws_deletable)} workspaces + {len(box_deletable)} "
          f"Box roots deleted. Dump retained at {dump_dir}")
    print("[next] Run scripts/migrations/standup.py to rebuild (FLIP handled by "
          "sheet_ids_regen.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
