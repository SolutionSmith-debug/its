"""Stand up the full Smartsheet/Box tenant from zero — builders + auto-FLIP + seeds.

The orchestrator that turns the ~25-script manual cutover sequence (interleaved with
hand-pasted FLIP edits of shared/sheet_ids.py) into ONE attended run. The circle the
operator asked for (2026-07-22): seed/builder scripts create every sheet, and
sheet_ids_regen.py rewrites the constants automatically between stages — no ID is
ever hand-pasted. FLIP still precedes SEED; the flip is just mechanical now.

How the interleave works
    Builders run as SUBPROCESSES (each fresh import of shared/sheet_ids.py picks up
    the values the preceding regen stage wrote — zero builder refactoring), with an
    auto-fed "y" for their single LiveWriteGate prompt. THIS process gives ONE master
    y/N gate up front; the per-builder prompts remain the control when scripts run
    standalone, and the master gate is the control here. sheet_ids_regen runs with
    --write (non-strict) between stages: it flips what exists, leaves later-stage
    constants untouched, and the FINAL stage runs it with --check (strict parity).

What is deliberately SKIPPED (with reasons, so nobody hunts for a phantom):
    - seed_its_active_jobs.py    — seeds the 6 pre-portal demo projects; the live job
                                   set is restored from the pre-wipe dump instead
                                   (seeding both would pollute the portal dropdown).
    - seed_its_project_routing.py — seeds Box folder ids from defaults.BOX_PROJECT_FOLDERS,
                                   which are DEAD after the full Box wipe (ITS DATA and
                                   the 1111A/1111B templates are gone). The routing lane
                                   is dormant until projects are re-cloned; seeding dead
                                   ids would be worse than an empty sheet.
    - build_its_trusted_contacts_sheet.py + seed — the trusted-contacts lane is dormant
                                   (SHEET_TRUSTED_CONTACTS=0 by design, pre-wipe parity).
    - The ~72 demo tracker sheets — content, not structure (in the dump if ever needed).

Manual gates: NONE remain. The former ITS_Active_Jobs AUTO_NUMBER gate was stale
pre-Slice-6 doctrine — since P2.5 Slice 6 the portal assigns the canonical
JOB-###### (Worker job_counter) and the mirror WRITES it, so 'Job ID' and
'Portal Job Key' are plain TEXT columns extend_its_active_jobs_phase3.py now
creates via the API. (The _manual_gate machinery stays for any future genuinely
UI-only step.)

Preconditions: all org.solutionsmith.its.* daemons UNLOADED (verified, same guard as
wipe_tenant.py); Box OAuth as the dedicated ITS identity; ITS_SMARTSHEET_TOKEN in
Keychain. Post-run: commit the regenerated ID surfaces via PR, delete
~/its/state/heartbeat_row_ids.json, reload the fleet dark (see the printed epilogue).

Run from ~/its while daemons are down:
    python3 scripts/migrations/standup.py --list
    python3 scripts/migrations/standup.py [--dump DIR] [--start-at STAGE] [--skip-shares]
"""
from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import requests  # type: ignore[import-untyped]  # noqa: E402

from shared import keychain  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = "scripts/migrations"
DUMP_ROOT = pathlib.Path.home() / "its" / "logs" / "migrations"
BASE = "https://api.smartsheet.com/2.0"

# Sheets whose ROWS are restored from the pre-wipe dump (workspace name, sheet name,
# sheet_ids constant of the rebuilt target). Everything else restarts empty by design
# (pending review rows discarded per the 2026-07-22 operator decision).
RESTORE_SHEETS: tuple[tuple[str, str, str], ...] = (
    ("ITS –– Safety Portal", "ITS_Active_Jobs", "SHEET_ACTIVE_JOBS"),
    ("ITS — Progress Reporting", "ITS_Active_Jobs_Progress", "SHEET_ACTIVE_JOBS_PROGRESS"),
    ("ITS — Purchase Orders", "ITS_Vendors", "SHEET_ITS_VENDORS"),
    ("ITS — Subcontracts", "ITS_Subcontractors", "SHEET_ITS_SUBCONTRACTORS"),
    ("ITS — Operations", "Subcontractor DB", "SHEET_SUBCONTRACTOR_DB"),
    ("ITS — Operations", "Vendor DB", "SHEET_VENDOR_DB"),
    ("ITS — Operations", "Equipment Master", "SHEET_EQUIPMENT_MASTER"),
)

# Workspaces whose SHARE lists (F22 approver sets) are restored from the dump.
RESHARE_WORKSPACES: tuple[str, ...] = (
    "ITS –– Safety Portal",
    "ITS — Progress Reporting",
    "ITS — Purchase Orders",
    "ITS — Subcontracts",
    "ITS — Human Review",
    "Forefront Portfolio — ITS Demo",
)

BOX_ROOT_CONFIG_ROWS: tuple[tuple[str, str, str], ...] = (
    # (Box folder name, ITS_Config Setting, Workstream)
    ("ITS Safety Reports", "safety_reports.box.portal_root_folder_id", "safety_reports"),
    ("ITS Progress Reports", "progress_reports.box.portal_root_folder_id",
     "progress_reports"),
)


def _confirm(prompt: str) -> bool:
    """Master y/N gate (tests monkeypatch). EOF counts as a decline."""
    try:
        return input(f"{prompt} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def _manual_gate(instruction: str) -> None:
    """Pause for a UI-only step; loops until the operator confirms it is done.

    EOF (a piped/non-interactive run reaching a gate) aborts the stage cleanly —
    the resume hint then names this stage, the operator does the UI step, and
    the run continues with --start-at the FOLLOWING stage.
    """
    print(f"\n=== MANUAL STEP ===\n{instruction}")
    while True:
        try:
            answer = input("Done? [y/N] ").strip().lower()
        except EOFError as exc:
            raise StageFailedError(
                "manual step reached with no interactive stdin — do the step above, "
                "then resume at the NEXT stage") from exc
        if answer == "y":
            return
        print("Waiting — complete the step above, then answer y.")


def _run_script(rel_path: str, *args: str) -> None:
    """Run a builder/seeder subprocess, auto-answering its y/N gate(s)."""
    cmd = [sys.executable, str(REPO_ROOT / rel_path), *args]
    print(f"\n--- $ {' '.join(cmd[1:])}")
    result = subprocess.run(cmd, input="y\n" * 8, text=True, cwd=REPO_ROOT, check=False)
    if result.returncode != 0:
        raise StageFailedError(f"{rel_path} exited {result.returncode}")


class StageFailedError(RuntimeError):
    pass


def _run_many(*rel_paths: str) -> None:
    for rel_path in rel_paths:
        _run_script(rel_path)


# The builder->flip contract: each interleaved regen names the constants its
# preceding builder stage JUST created and retries until THOSE resolve —
# Smartsheet's create->read propagation lag can outlast any fixed number of
# retries' heuristics (the 2026-07-23 run saw a >3s window leave
# WORKSPACE_SAFETY_PORTAL stale-dead), so the expected set is explicit and an
# unresolved expected constant FAILS the stage instead of drifting.
_HR_FOLDERS = tuple(f"FOLDER_HR_{s}" for s in (
    "SAFETY_REPORTS", "SUBCONTRACTS", "PURCHASE_ORDERS_AND_MATERIALS",
    "EMAIL_TRIAGE", "AI_EMPLOYEE", "PERSONNEL"))
_PROJECTS = ("BRADLEY_1", "BRADLEY_2", "BRIMFIELD_1", "BRIMFIELD_2",
             "HUNTLEY", "ROCKFORD")
REGEN_EXPECT: dict[str, tuple[str, ...]] = {
    "regen-system": ("WORKSPACE_SYSTEM", "FOLDER_SYSTEM_CONFIG",
                     "FOLDER_SYSTEM_LOGS", "FOLDER_SYSTEM_QUEUES",
                     "FOLDER_SYSTEM_DAEMONS"),
    "regen-system-sheets": ("SHEET_CONFIG", "SHEET_ERRORS", "SHEET_QUARANTINE",
                            "SHEET_REVIEW_QUEUE", "SHEET_DAEMON_HEALTH"),
    "regen-safety-portal": ("WORKSPACE_SAFETY_PORTAL", "FOLDER_SAFETY_PORTAL",
                            "FOLDER_FORM_CATALOG"),
    "regen-safety-sheets": ("SHEET_ACTIVE_JOBS", "SHEET_FORMS_CATALOG",
                            "SHEET_WSR_HUMAN_REVIEW", "SHEET_ORPHANED_REPORTS"),
    "regen-progress": ("WORKSPACE_PROGRESS_REPORTING", "FOLDER_PROGRESS_CONTROL"),
    "regen-progress-sheets": ("SHEET_WPR_HUMAN_REVIEW", "SHEET_ACTIVE_JOBS_PROGRESS"),
    "regen-po": ("WORKSPACE_PURCHASE_ORDERS", "FOLDER_PO_CONTROL"),
    "regen-po-sheets": ("SHEET_ITS_VENDORS", "SHEET_PO_LOG",
                        "SHEET_PO_PENDING_REVIEW", "SHEET_ESTIMATE_LOG",
                        "SHEET_RFQ_LOG", "SHEET_RFQ_PENDING_REVIEW"),
    "regen-subcontracts": ("WORKSPACE_SUBCONTRACTS", "FOLDER_SC_CONTROL"),
    "regen-subcontract-sheets": ("SHEET_ITS_SUBCONTRACTORS", "SHEET_SUBCONTRACT_LOG",
                                 "SHEET_SUBCONTRACT_PENDING_REVIEW"),
    "regen-job-folders": ("FOLDER_PO_JOBS", "FOLDER_SC_JOBS"),
    "regen-legacy": (
        "WORKSPACE_HUMAN_REVIEW", "WORKSPACE_OPERATIONS", "WORKSPACE_ARCHIVE",
        "WORKSPACE_DEMO", *_HR_FOLDERS, "FOLDER_OPERATIONS_MASTER_DBS",
        "FOLDER_ARCHIVE_CLOSED_PROJECTS", "FOLDER_ACTIVE_PROJECTS",
        "FOLDER_PORTFOLIO_ROLLUPS", "FOLDER_FIELD_REPORTS",
        *(f"FOLDER_PROJECT_{p}" for p in _PROJECTS),
        *(f"FOLDER_FIELD_REPORTS_{p}" for p in _PROJECTS),
        "SHEET_TIME_OFF", "SHEET_WPR_PENDING_REVIEW", "SHEET_SUBCONTRACTOR_DB",
        "SHEET_VENDOR_DB", "SHEET_EQUIPMENT_MASTER",
        "safety_reports/week_folder.py:TEMPLATE_DAILY_REPORTS_SHEET_ID",
        "safety_reports/week_folder.py:TEMPLATE_WEEKLY_ROLLUP_SHEET_ID",
    ),
    "regen-config-sheets": ("SHEET_PROJECT_ROUTING", "SHEET_PICKLIST_SYNC_CONFIG"),
}


def _regen_stage(stage_name: str) -> None:
    expect_args: list[str] = []
    for const in REGEN_EXPECT[stage_name]:
        expect_args += ["--expect", const]
    _run_script(f"{MIGRATIONS}/sheet_ids_regen.py", "--write",
                "--retry-missing", "10", *expect_args)


def _regen(*args: str) -> None:
    _run_script(f"{MIGRATIONS}/sheet_ids_regen.py", *args)


def _regen_entry(stage_name: str) -> Stage:
    return (stage_name, lambda: _regen_stage(stage_name))



def _fresh_sheet_ids() -> Any:
    """Re-import shared.sheet_ids so post-regen constants are visible in-process."""
    import shared.sheet_ids
    return importlib.reload(shared.sheet_ids)


def _headers() -> dict[str, str]:
    token = keychain.get_secret("ITS_SMARTSHEET_TOKEN")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---- composite stage bodies ----------------------------------------------


def _stage_box_roots() -> None:
    """build_box_roots + auto-paste of the two ITS_Config rows (circle closed).

    The builder's own identity gate is auto-fed like every other prompt, so the
    Box login is printed HERE first — the attended operator sees which account
    the roots land in before the subprocess runs (the builder prints it again).
    """
    from shared import box_client, smartsheet_client
    me = box_client.get_client().user().get()
    print(f"[info] Box identity for this stage: {me.login} ({me.name})")
    _run_script(f"{MIGRATIONS}/build_box_roots.py")
    sheet_ids = _fresh_sheet_ids()
    all_roots = [i for i in box_client.list_folder("0", limit=1000)
                 if i.get("type") == "folder"]
    for folder_name, setting, workstream in BOX_ROOT_CONFIG_ROWS:
        matches = [str(i.get("id")) for i in all_roots if i.get("name") == folder_name]
        if len(matches) > 1:
            raise StageFailedError(
                f"Box root {folder_name!r} is AMBIGUOUS ({len(matches)} folders: "
                f"{matches}) — reconcile before the config paste")
        if not matches:
            raise StageFailedError(f"Box root {folder_name!r} not found after builder run")
        folder_id = matches[0]
        existing = smartsheet_client.get_rows(
            sheet_ids.SHEET_CONFIG,
            filters={"Setting": setting, "Workstream": workstream})
        if existing:
            row = existing[0]
            if str(row.get("Value")) != folder_id:
                smartsheet_client.update_rows(sheet_ids.SHEET_CONFIG, [{
                    "_row_id": row["_row_id"], "Value": folder_id}])
                print(f"[ok] ITS_Config {setting} updated -> {folder_id}")
            else:
                print(f"[skip] ITS_Config {setting} already {folder_id}")
        else:
            smartsheet_client.add_rows(sheet_ids.SHEET_CONFIG, [{
                "Setting": setting, "Value": folder_id, "Workstream": workstream,
                "Description": f"Box root folder id for {folder_name!r} "
                               "(auto-pasted by standup.py from build_box_roots)."}])
            print(f"[ok] ITS_Config {setting} added -> {folder_id}")


def _find_dump_sheet(dump_dir: pathlib.Path, workspace: str, sheet: str,
                     ) -> dict[str, Any] | None:
    ws_dir = dump_dir / "smartsheet" / workspace.replace("/", "_")
    if not ws_dir.is_dir():
        return None
    matches = []
    for path in sorted(ws_dir.glob("*.sheet.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("name") == sheet:
            matches.append(data)
    if len(matches) > 1:
        raise StageFailedError(
            f"dump ambiguity: {len(matches)} sheets named {sheet!r} in {workspace!r}")
    return matches[0] if matches else None


def _stage_restore_rows(dump_dir: pathlib.Path) -> None:
    """Restore data-bearing SoR rows from the pre-wipe dump (idempotent by primary).

    Raw REST (not smartsheet_client.add_rows) so structured objectValues —
    MULTI_PICKLIST cells like ITS_Vendors "Supply Categories" and
    ITS_Subcontractors "Trades" — round-trip intact; a title-keyed plain value
    would flatten them to a display string the API rejects. Cells map dump
    title -> REBUILT sheet column id; dump columns absent on the rebuilt
    schema are reported, never silently dropped.
    """
    from shared import smartsheet_client
    sheet_ids = _fresh_sheet_ids()
    for workspace, sheet_name, constant in RESTORE_SHEETS:
        target_id = getattr(sheet_ids, constant, 0)
        if not target_id:
            print(f"[WARN] restore skipped: {constant} unresolved (sheet {sheet_name!r})")
            continue
        dump = _find_dump_sheet(dump_dir, workspace, sheet_name)
        if dump is None:
            print(f"[WARN] restore skipped: {sheet_name!r} not in dump {workspace!r}")
            continue
        dump_writable = {
            c["title"] for c in dump.get("columns", [])
            if c.get("title") and not c.get("systemColumnType")
        }
        target_cols = smartsheet_client.list_columns_with_options(target_id)
        target_id_by_title = {c.get("title"): int(c["id"]) for c in target_cols}
        writable = dump_writable & set(target_id_by_title)
        dropped = dump_writable - set(target_id_by_title)
        if dropped:
            print(f"[WARN] {sheet_name!r}: dump column(s) absent on the rebuilt sheet, "
                  f"values NOT restored: {sorted(dropped)}")
        primary = next(
            (c["title"] for c in dump.get("columns", []) if c.get("primary")), None)
        if primary is None:
            print(f"[WARN] restore skipped: {sheet_name!r} has no primary column in dump")
            continue
        existing_primaries = {
            r.get(primary) for r in smartsheet_client.get_rows(target_id)}
        payload: list[dict[str, Any]] = []
        for row in dump.get("rows", []):
            if row.get(primary) in existing_primaries:
                continue
            object_values = row.get("_ov") or {}
            cells: list[dict[str, Any]] = []
            for title in sorted(writable):
                col_id = target_id_by_title[title]
                if title in object_values:
                    cells.append({"columnId": col_id,
                                  "objectValue": object_values[title]})
                elif row.get(title) is not None:
                    cells.append({"columnId": col_id, "value": row[title]})
            if cells:
                payload.append({"toBottom": True, "cells": cells})
        if not payload:
            print(f"[skip] {sheet_name!r}: nothing to restore "
                  f"({len(dump.get('rows', []))} dumped, all present or empty).")
            continue
        for i in range(0, len(payload), 300):
            r = requests.post(f"{BASE}/sheets/{target_id}/rows", headers=_headers(),
                              json=payload[i:i + 300], timeout=120)
            if r.status_code != 200:
                raise StageFailedError(
                    f"restore add-rows failed for {sheet_name!r}: "
                    f"HTTP {r.status_code} {r.text[:300]}")
        print(f"[ok] {sheet_name!r}: restored {len(payload)} row(s) from dump.")
    _reconcile_progress_job_ids(sheet_ids)


def _reconcile_progress_job_ids(sheet_ids: Any) -> None:
    """Verify ITS_Active_Jobs_Progress 'Job ID' matches the safety sheet's values.

    Both sheets' 'Job ID' are plain TEXT restored VERBATIM from the dump (the
    portal owns the JOB-###### number — Slice 6), so this is a belt-and-braces
    consistency check, expected to be a no-op: it reconciles by 'Project Name',
    rewrites any drifted progress value, and WARNs on rows with no safety
    counterpart. (Its original premise — AUTO_NUMBER renumbering on the safety
    side — was stale pre-Slice-6 doctrine; kept because a cheap cross-sheet
    verify is worth keeping either way.)
    """
    from shared import smartsheet_client
    if not (getattr(sheet_ids, "SHEET_ACTIVE_JOBS", 0)
            and getattr(sheet_ids, "SHEET_ACTIVE_JOBS_PROGRESS", 0)):
        print("[WARN] job_id_reconcile skipped: an Active-Jobs constant is unresolved.")
        return
    safety = smartsheet_client.get_rows(sheet_ids.SHEET_ACTIVE_JOBS)
    progress = smartsheet_client.get_rows(sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS)
    safety_by_project = {r.get("Project Name"): r.get("Job ID") for r in safety
                        if r.get("Project Name")}
    updates = []
    for row in progress:
        project = row.get("Project Name")
        want = safety_by_project.get(project)
        if want is None:
            print(f"[WARN] job_id_reconcile: progress row {project!r} has no safety "
                  "counterpart — reconcile manually.")
            continue
        if row.get("Job ID") != want:
            updates.append({"_row_id": row["_row_id"], "Job ID": want})
    if updates:
        smartsheet_client.update_rows(sheet_ids.SHEET_ACTIVE_JOBS_PROGRESS, updates)
        print(f"[ok] job_id_reconcile: {len(updates)} progress Job ID(s) re-aligned "
              "to the safety sheet's regenerated auto-numbers.")
    else:
        print("[ok] job_id_reconcile: safety and progress Job IDs already aligned.")


def _stage_restore_shares(dump_dir: pathlib.Path) -> None:
    """Re-share rebuilt workspaces from the dumped share lists (F22 approver sets).

    §46: workspace membership IS approval authority, so this write is guarded
    like the builders — the target workspace must be OWNER-access and uniquely
    named, and every dumped share this tool CANNOT restore (a GROUP share has
    no email) is WARNed, never silently dropped: a silently narrower approver
    set on Purchase Orders / Subcontracts would fail-close the send gates with
    no explanation.
    """
    ws_listing = requests.get(f"{BASE}/workspaces?includeAll=true",
                              headers=_headers(), timeout=30)
    ws_listing.raise_for_status()
    live_by_name: dict[str, list[dict[str, Any]]] = {}
    for ws in ws_listing.json().get("data", []):
        live_by_name.setdefault(str(ws.get("name")), []).append(ws)
    for name in RESHARE_WORKSPACES:
        meta_path = (dump_dir / "smartsheet" / name.replace("/", "_")
                     / "_workspace.json")
        if not meta_path.is_file():
            print(f"[WARN] shares skipped: no dump for {name!r}")
            continue
        shares = json.loads(meta_path.read_text(encoding="utf-8")).get("shares", [])
        targets = live_by_name.get(name, [])
        if len(targets) != 1:
            print(f"[WARN] shares skipped: {len(targets)} live workspaces named {name!r}")
            continue
        target = targets[0]
        if target.get("accessLevel") != "OWNER":
            print(f"[WARN] shares skipped: workspace {name!r} accessLevel="
                  f"{target.get('accessLevel')} != OWNER — refusing to write shares "
                  "into a workspace this token does not own.")
            continue
        added = 0
        for share in shares:
            email = share.get("email")
            level = share.get("accessLevel")
            if level == "OWNER":
                continue  # ownership is implicit on the rebuilt workspace
            if not email:
                print(f"  [WARN] share_not_restorable: {name!r} had a non-email share "
                      f"(type={share.get('type')}, name={share.get('name')!r}, "
                      f"accessLevel={level}) — GROUP shares must be re-added by hand "
                      "or the approver set is narrower than pre-wipe.")
                continue
            r = requests.post(
                f"{BASE}/workspaces/{int(target['id'])}/shares?sendEmail=false",
                headers=_headers(),
                json=[{"email": email, "accessLevel": level}], timeout=30)
            if r.status_code == 200:
                added += 1
            else:
                # already-shared or invalid — loud but non-fatal (operator reconciles)
                print(f"  [WARN] share {email} ({level}) on {name!r}: "
                      f"HTTP {r.status_code} {r.text[:120]}")
        print(f"[ok] {name!r}: {added} share(s) restored.")


def _stage_final_verify() -> None:
    _regen("--check")
    print("\n--- verify_cutover (config rows; sandbox values allowed — rehearsal, "
          "not a phase verdict)")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.verify_cutover", "--only", "config",
         "--allow-sandbox"],
        cwd=REPO_ROOT, check=False)
    if result.returncode != 0:
        raise StageFailedError("verify_cutover --only config failed")


# ---- the stage table ------------------------------------------------------

Stage = tuple[str, Callable[[], None]]


def build_stages(dump_dir: pathlib.Path | None, *, skip_shares: bool) -> list[Stage]:
    def run(script: str, *args: str) -> Callable[[], None]:
        return lambda: _run_script(script, *args)

    stages: list[Stage] = [
        ("system-workspace", run(f"{MIGRATIONS}/build_system_workspace.py")),
        _regen_entry("regen-system"),
        ("system-sheets", run(f"{MIGRATIONS}/build_system_sheets.py")),
        _regen_entry("regen-system-sheets"),
        ("seed-config-baseline", run("scripts/seed_its_config.py")),
        ("safety-portal-workspace", run(f"{MIGRATIONS}/build_safety_portal_workspace.py")),
        _regen_entry("regen-safety-portal"),
        ("safety-portal-sheets", lambda: _run_many(
            f"{MIGRATIONS}/build_its_active_jobs_sheet.py",
            f"{MIGRATIONS}/build_its_forms_catalog_sheet.py",
            f"{MIGRATIONS}/build_wsr_human_review_sheet.py",
            f"{MIGRATIONS}/build_orphaned_reports_sheet.py",
        )),
        _regen_entry("regen-safety-sheets"),
        # phase3 now also creates the TEXT 'Job ID' + 'Portal Job Key' columns —
        # both plain writable columns (the portal assigns JOB-######, Slice 6),
        # so the former manual AUTO_NUMBER UI gate is GONE (it was stale
        # pre-Slice-6 doctrine; an AUTO_NUMBER would reject every mirror write).
        ("active-jobs-phase3", run(f"{MIGRATIONS}/extend_its_active_jobs_phase3.py")),
        ("active-jobs-contact-cols",
         run(f"{MIGRATIONS}/add_active_jobs_contact_routing_columns.py")),
        ("progress-workspace", run(f"{MIGRATIONS}/build_progress_reporting_workspace.py")),
        _regen_entry("regen-progress"),
        ("progress-sheets", lambda: _run_many(
            f"{MIGRATIONS}/build_wpr_human_review_sheet.py",
            f"{MIGRATIONS}/build_its_active_jobs_progress_sheet.py",
        )),
        _regen_entry("regen-progress-sheets"),
        ("po-workspace", run(f"{MIGRATIONS}/build_purchase_orders_workspace.py")),
        _regen_entry("regen-po"),
        ("po-sheets", lambda: _run_many(
            f"{MIGRATIONS}/build_its_vendors_sheet.py",
            f"{MIGRATIONS}/build_po_log_sheet.py",
            f"{MIGRATIONS}/build_po_pending_review_sheet.py",
            f"{MIGRATIONS}/build_estimate_log_sheet.py",
            f"{MIGRATIONS}/build_rfq_log_sheet.py",
            f"{MIGRATIONS}/build_rfq_pending_review_sheet.py",
        )),
        _regen_entry("regen-po-sheets"),
        ("subcontracts-workspace", run(f"{MIGRATIONS}/build_subcontracts_workspace.py")),
        _regen_entry("regen-subcontracts"),
        ("subcontract-sheets", lambda: _run_many(
            f"{MIGRATIONS}/build_its_subcontractors_sheet.py",
            f"{MIGRATIONS}/build_subcontract_log_sheet.py",
            f"{MIGRATIONS}/build_subcontract_pending_review_sheet.py",
        )),
        _regen_entry("regen-subcontract-sheets"),
        ("job-folders", run(f"{MIGRATIONS}/build_job_folders.py")),
        _regen_entry("regen-job-folders"),
        ("legacy-workspaces", run(f"{MIGRATIONS}/build_legacy_workspaces.py")),
        _regen_entry("regen-legacy"),
        ("system-config-sheets", lambda: _run_many(
            f"{MIGRATIONS}/build_its_project_routing_sheet.py",
            f"{MIGRATIONS}/create_picklist_sync_config.py",
        )),
        _regen_entry("regen-config-sheets"),
        ("box-roots", _stage_box_roots),
        ("docs-index", run(f"{MIGRATIONS}/build_docs_index_sheet.py")),
    ]
    if dump_dir is not None:
        stages.append(("restore-rows", lambda: _stage_restore_rows(dump_dir)))
    stages.append(("seeders", lambda: _run_many(
        f"{MIGRATIONS}/seed_its_forms_catalog.py",
        f"{MIGRATIONS}/extend_its_forms_catalog_parent_variant.py",
        f"{MIGRATIONS}/seed_its_vendors.py",
        f"{MIGRATIONS}/seed_its_subcontractors.py",
        f"{MIGRATIONS}/seed_safety_intake_config.py",
        f"{MIGRATIONS}/seed_safety_intake_polling_config.py",
        f"{MIGRATIONS}/seed_safety_recipients_config.py",
        f"{MIGRATIONS}/seed_po_materials_config.py",
        f"{MIGRATIONS}/seed_estimates_config.py",
        f"{MIGRATIONS}/seed_rfq_config.py",
        f"{MIGRATIONS}/seed_rfq_send_config.py",
        f"{MIGRATIONS}/seed_subcontracts_config.py",
        f"{MIGRATIONS}/seed_subcontracts_send_config.py",
        f"{MIGRATIONS}/seed_config_actuator_config.py",
        f"{MIGRATIONS}/seed_daemon_gate_config.py",
        f"{MIGRATIONS}/seed_generate_and_interval_config.py",
    )))
    if dump_dir is not None and not skip_shares:
        stages.append(("restore-shares", lambda: _stage_restore_shares(dump_dir)))
    stages.append(("final-verify", _stage_final_verify))
    return stages


def _resolve_dump_dir(explicit: pathlib.Path | None) -> pathlib.Path:
    """Resolve the restore source, refusing every ambiguous case LOUDLY.

    - No dump found and --no-restore not passed -> abort (a silent fresh-tenant
      fallback would quietly skip the row/share restore the operator expects).
    - MULTIPLE prewipe dumps -> abort and demand an explicit --dump: a partial
      re-wipe after a failed first attempt creates a second, INCOMPLETE dump,
      and auto-picking "newest" would restore from the wrong one.
    """
    if explicit is not None:
        if not explicit.is_dir():
            raise StageFailedError(f"dump dir {explicit} does not exist")
        return explicit
    candidates = sorted(DUMP_ROOT.glob("prewipe_*"))
    if not candidates:
        raise StageFailedError(
            f"no prewipe_* dump under {DUMP_ROOT} — pass --dump DIR, or pass "
            "--no-restore explicitly for a genuine fresh-tenant stand-up")
    if len(candidates) > 1:
        listing = ", ".join(c.name for c in candidates)
        raise StageFailedError(
            f"{len(candidates)} prewipe dumps exist ({listing}) — a partial re-wipe "
            "produces an incomplete second dump, so auto-picking is unsafe; pass "
            "--dump with the FULL (usually first) dump explicitly")
    return candidates[0]


def _require_daemons_down() -> bool:
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True,
                         timeout=30, check=True).stdout
    loaded = [line.split()[-1] for line in out.splitlines()
              if "org.solutionsmith.its." in line]
    if loaded:
        print(f"[abort] daemons_loaded: {len(loaded)} org.solutionsmith.its.* job(s) "
              "still loaded — the stand-up rewrites shared/sheet_ids.py on the live "
              "tree; a running fleet would pick up half-flipped constants mid-run. "
              "Unload first (same precondition as wipe_tenant.py).")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Orchestrated tenant stand-up: builders + auto-FLIP + seeds.")
    parser.add_argument("--dump", type=pathlib.Path, default=None,
                        help="prewipe dump dir for row/share restore "
                             "(default: newest logs/migrations/prewipe_*).")
    parser.add_argument("--no-restore", action="store_true",
                        help="Fresh-tenant mode: skip row/share restore entirely.")
    parser.add_argument("--skip-shares", action="store_true",
                        help="Skip the workspace share restore stage.")
    parser.add_argument("--start-at", default=None, metavar="STAGE",
                        help="Resume from a named stage (see --list).")
    parser.add_argument("--list", action="store_true", help="Print stages and exit.")
    args = parser.parse_args()

    dump_dir: pathlib.Path | None = None
    if not args.no_restore:
        try:
            dump_dir = _resolve_dump_dir(args.dump)
        except StageFailedError as exc:
            print(f"[abort] {exc}")
            return 1
    stages = build_stages(dump_dir, skip_shares=args.skip_shares)

    if args.list:
        for name, _fn in stages:
            print(name)
        return 0

    names = [n for n, _ in stages]
    start = 0
    if args.start_at:
        if args.start_at not in names:
            print(f"[abort] unknown stage {args.start_at!r}. --list shows stages.")
            return 1
        start = names.index(args.start_at)

    if not _require_daemons_down():
        return 1
    print(f"[info] {len(stages)} stages; starting at {names[start]!r}. "
          f"Dump: {dump_dir if dump_dir else 'NONE (fresh-tenant mode)'}")
    if not _confirm("Proceed with the LIVE tenant stand-up?"):
        print("[skip] Operator declined; nothing run.")
        return 0

    for name, fn in stages[start:]:
        print(f"\n=== STAGE {name} ===")
        try:
            fn()
        except KeyboardInterrupt:
            print(f"\n[abort] interrupted during stage {name!r}. Every builder is "
                  f"idempotent — resume: python3 {MIGRATIONS}/standup.py --start-at {name}")
            return 1
        except Exception as exc:  # noqa: BLE001 — ANY stage failure gets the resume hint
            label = type(exc).__name__ if not isinstance(exc, StageFailedError) else "stage"
            print(f"\n[abort] stage {name!r} failed ({label}): {exc}\n"
                  f"Fix, then resume: python3 {MIGRATIONS}/standup.py --start-at {name}")
            return 1
    print(
        "\n[ok] Stand-up complete. Epilogue (in order):\n"
        "  1. git diff — review the regenerated ID surfaces (sheet_ids.py, "
        "system_map.py, week_folder.py); update any test pins the regen sweep "
        "reported; land it all via PR.\n"
        "  2. rm ~/its/state/heartbeat_row_ids.json (daemons re-provision their "
        "ITS_Daemon_Health rows); review approver_set_baseline.json (Check U).\n"
        "  3. After the PR merges and ~/its is pulled: reload the fleet via "
        "scripts/launchd/install.sh load <plist> per daemon (po-send/rfq-send stay "
        "unloaded — send gate).\n"
        "  4. Gates are seeded DARK (=false) — the production posture. Read each "
        "row's Description before flipping; send-gate flips escalate to Seth.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
