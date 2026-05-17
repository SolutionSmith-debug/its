"""Bradley 1 Schedule migration — REAL WRITE.

Reads source 1417836219027332, filters to leaf tasks, remaps row references in
Predecessors to destination row positions, writes to 6008505839341444.

Idempotent? NO. Running twice will create duplicate rows. Check destination
before re-running. (Pre-write check below aborts if dest has existing rows.)
"""
import re
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss_api import api, get_sheet

SOURCE = 1417836219027332
DEST = 6008505839341444

SRC_TO_DEST_TITLE = {
    "Task Name": "Task Name",
    "Contract Milestone": "Contract Milestone",
    "Duration": "Duration",
    "Start Date": "Start Date",
    "Completion Date": "Completion Date",
    "% Done": "% Done",
    "Company Responsible": "Company Responsible",
    "Assigned To/Resource": "Assigned To",
    "System Wattage DC + Notes / Status & Action Items": "Notes / Status",
    "Predecessors": "Predecessors",
    "Filter By List": "Phase",
}

# regex: leading digits = row number ref, the rest = FS/SS/lag modifier
PRED_CHUNK = re.compile(r"^\s*(\d+)\s*(.*)$")


def remap_predecessors(value, src_to_dst_rownum):
    """Remap '2, 7FS +30d, 4' from source rownums to dest rownums.

    Preserves FS/SS/FF/SF and ±Nd lag tokens. If a referenced source row is
    NOT in the leaf set (i.e. it was a section header), drop that chunk and
    log it. If parsing fails on any chunk, preserve raw value with a warning.
    """
    if not value:
        return value, []
    out_chunks = []
    warnings = []
    for chunk in str(value).split(","):
        m = PRED_CHUNK.match(chunk)
        if not m:
            warnings.append(f"unparseable chunk: {chunk!r}")
            return value, warnings   # bail; preserve raw
        src_rn = int(m.group(1))
        rest = m.group(2)
        if src_rn not in src_to_dst_rownum:
            warnings.append(f"reference to non-leaf source row {src_rn} (dropped)")
            continue
        dst_rn = src_to_dst_rownum[src_rn]
        out_chunks.append(f"{dst_rn}{rest}" if rest else str(dst_rn))
    return ", ".join(out_chunks), warnings


def main():
    print("Fetching source + destination...")
    src = get_sheet(SOURCE)
    dst = get_sheet(DEST)

    # Abort if destination is already populated (idempotency guard)
    if dst.get("rows"):
        print(f"⚠️  Destination already has {len(dst['rows'])} rows. Aborting to avoid duplicates.")
        print("   If you want to overwrite, delete dest rows first.")
        return 1

    src_cols_by_title = {c["title"]: c for c in src["columns"]}
    dst_cols_by_title = {c["title"]: c for c in dst["columns"]}

    # Filter to leaf rows
    fbl_id = src_cols_by_title["Filter By List"]["id"]
    leaf_rows = [r for r in src["rows"]
                 if any(c["columnId"] == fbl_id and c.get("value") for c in r["cells"])]
    print(f"Leaf rows to migrate: {len(leaf_rows)}")

    # Build src rownum → dst rownum map (dest is 1-indexed in order written)
    src_to_dst = {r["rowNumber"]: i + 1 for i, r in enumerate(leaf_rows)}

    # Build destination rows
    out_rows = []
    pred_warnings = []
    for r in leaf_rows:
        src_cells = {c["columnId"]: c.get("value") for c in r["cells"]}
        out_cells = []
        for src_title, dst_title in SRC_TO_DEST_TITLE.items():
            sval = src_cells.get(src_cols_by_title[src_title]["id"])
            if sval is None or sval == "":
                continue   # skip empty cells; Smartsheet won't fail on missing
            dst_col_id = dst_cols_by_title[dst_title]["id"]
            # Predecessors → remap
            if dst_title == "Predecessors":
                sval, warns = remap_predecessors(sval, src_to_dst)
                if warns:
                    pred_warnings.append((r["rowNumber"], sval, warns))
                if not sval:
                    continue
            out_cells.append({"columnId": dst_col_id, "value": sval})
        out_rows.append({"toBottom": True, "cells": out_cells})

    if pred_warnings:
        print("\nPredecessor remap warnings:")
        for src_rn, new_val, warns in pred_warnings:
            print(f"  source row {src_rn} → '{new_val}'  {warns}")
        print()

    print(f"Writing {len(out_rows)} rows to destination...")
    # Smartsheet caps add_rows at ~500/batch; we have 53 so single call is fine
    resp = api("POST", f"/sheets/{DEST}/rows", body=out_rows)
    written = resp.get("result", [])
    if isinstance(written, list):
        print(f"✓ Wrote {len(written)} rows. First dest row id: {written[0]['id']}")
    else:
        print(f"Response: {resp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
