"""Closeout K-1 migration — REAL WRITE.

Per Seth's Q1-Q5 decisions (2026-05-16):
  Q1: use whole source sheet (no per-project version exists)
  Q2: 'Owner Provided' → 'Not Applicable' in ESS and Luminace Review Status
  Q3: % Complete: True → '100%'; preserve percentage strings; blank stays blank
  Q4: section headers migrate as parent rows; children indent under them
  Q5: Date Modified + Modified By → append '[Modified YYYY-MM-DD by EMAIL]' to Latest Comment

Source: 220029969715076 (6. Portfolio Closeout)
Dest:   4973530390155140 (Bradley 1 / Closeout — Exhibit K-1)

Idempotency: aborts if destination already has rows.
"""
import os
import re
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss_api import api, get_sheet

SOURCE = 220029969715076
DEST = 4973530390155140
SECTION_RE = re.compile(r"^[A-Z]\.\s")


def normalize_status(v):
    """Q2: Owner Provided → Not Applicable. Empty → None."""
    if not v:
        return None
    s = str(v).strip()
    if s == "Owner Provided":
        return "Not Applicable"
    # Fix the lowercase 'y' in 'Not yet Submitted' → 'Not Yet Submitted'
    if s == "Not yet Submitted":
        return "Not Yet Submitted"
    return s


def normalize_ball_in_court(v):
    """Strip decorators like ** and <<>> in Ball in Court."""
    if not v:
        return None
    s = str(v).strip()
    # **COMPLETE** → Complete
    s = s.replace("**", "")
    # <<Evergreen<< → Evergreen ; >>Luminace>> → Luminace
    s = s.replace("<<", "").replace(">>", "").strip()
    # Title-case after stripping
    if s.upper() == s:
        s = s.title()  # "COMPLETE" → "Complete"
    return s if s else None


def normalize_pct_complete(v):
    """Q3: True → '100%'; preserve strings; blank stays blank."""
    if v is True:
        return "100%"
    if v is False:
        return "0%"
    if v is None or v == "":
        return None
    return str(v)


def build_comment(latest, date_modified, modified_by):
    """Q5: append '[Modified YYYY-MM-DD by EMAIL]' to Latest Comment if either present."""
    parts = []
    if latest:
        parts.append(str(latest).strip())
    if date_modified or modified_by:
        d = str(date_modified)[:10] if date_modified else "unknown"
        m = str(modified_by) if modified_by else "unknown"
        parts.append(f"[Modified {d} by {m}]")
    return "\n".join(parts) if parts else None


def classify(cells, src_cols):
    delv = str(cells.get(src_cols["Exhibit K-1 Deliverables"]["id"], "") or "").strip()
    pct = cells.get(src_cols["% Complete"]["id"])
    ess = cells.get(src_cols["Evergreen Submission Status"]["id"])
    bic = cells.get(src_cols["Ball in Court"]["id"])
    if not delv:
        return "empty"
    if delv == "TOTAL SHEET % COMPLETE":
        return "master_rollup"
    if SECTION_RE.match(delv):
        return "section"
    if isinstance(pct, bool):
        return "deliverable"
    if ess or bic:
        return "deliverable"
    return "subsection"


def build_cells(src_cells_by_id, src_cols, dst_cols, include_section_data=True):
    """Build destination cells dict for a row.

    For section/subsection rows (include_section_data=False), we only carry the
    Deliverable text; the rest of the data is per-deliverable and not meaningful
    at the section level.
    """
    out = []
    delv = src_cells_by_id.get(src_cols["Exhibit K-1 Deliverables"]["id"])
    if delv:
        out.append({"columnId": dst_cols["Deliverable"]["id"], "value": str(delv)})

    if not include_section_data:
        return out

    # % Complete (Q3 normalization)
    pct = normalize_pct_complete(src_cells_by_id.get(src_cols["% Complete"]["id"]))
    if pct:
        out.append({"columnId": dst_cols["% Complete"]["id"], "value": pct})

    # Evergreen Submission Status (Q2)
    ess = normalize_status(src_cells_by_id.get(src_cols["Evergreen Submission Status"]["id"]))
    if ess:
        out.append({"columnId": dst_cols["Evergreen Submission Status"]["id"], "value": ess})

    # Ball in Court (clean decorators)
    bic = normalize_ball_in_court(src_cells_by_id.get(src_cols["Ball in Court"]["id"]))
    if bic:
        out.append({"columnId": dst_cols["Ball in Court"]["id"], "value": bic})

    # Luminace Review Status (Q2)
    lrs = normalize_status(src_cells_by_id.get(src_cols["Luminace Review Status"]["id"]))
    if lrs:
        out.append({"columnId": dst_cols["Luminace Review Status"]["id"], "value": lrs})

    # Latest Comment + Q5 append
    comment = build_comment(
        src_cells_by_id.get(src_cols["Latest Comment"]["id"]),
        src_cells_by_id.get(src_cols["Date Modified"]["id"]),
        src_cells_by_id.get(src_cols["Modified By"]["id"]),
    )
    if comment:
        out.append({"columnId": dst_cols["Latest Comment"]["id"], "value": comment})

    # Modified By → Comment Author (in addition to Q5 append)
    mb = src_cells_by_id.get(src_cols["Modified By"]["id"])
    if mb:
        out.append({"columnId": dst_cols["Comment Author"]["id"], "value": str(mb)})

    return out


def write_row(parent_id, cells, to_bottom=True):
    """Write one row, return its new row ID."""
    body = {"toBottom": to_bottom, "cells": cells}
    if parent_id is not None:
        body["parentId"] = parent_id
        body.pop("toBottom", None)   # parentId + toBottom conflict; use parent's bottom
        body["toBottom"] = True
    resp = api("POST", f"/sheets/{DEST}/rows", body=[body])
    return resp["result"][0]["id"]


def main():
    print("Fetching source + destination...")
    src = get_sheet(SOURCE)
    dst = get_sheet(DEST)

    if dst.get("rows"):
        print(f"⚠️  Destination already has {len(dst['rows'])} rows. Aborting.")
        return 1

    src_cols = {c["title"]: c for c in src["columns"]}
    dst_cols = {c["title"]: c for c in dst["columns"]}

    # Walk rows, classify, write hierarchically
    current_section_id = None
    current_subsection_id = None
    stats = {"section": 0, "subsection": 0, "deliverable": 0, "skipped": 0}
    skipped_kinds = {}

    for i, row in enumerate(src["rows"]):
        cells_by_id = {c["columnId"]: c.get("value") for c in row["cells"]}
        kind = classify(cells_by_id, src_cols)

        if kind == "empty":
            stats["skipped"] += 1
            skipped_kinds["empty"] = skipped_kinds.get("empty", 0) + 1
            continue
        if kind == "master_rollup":
            stats["skipped"] += 1
            skipped_kinds["master_rollup"] = skipped_kinds.get("master_rollup", 0) + 1
            continue

        if kind == "section":
            # Section header — top-level row, only Deliverable text matters
            cells = build_cells(cells_by_id, src_cols, dst_cols, include_section_data=False)
            new_id = write_row(parent_id=None, cells=cells)
            current_section_id = new_id
            current_subsection_id = None
            stats["section"] += 1
            print(f"  [section]    {row['rowNumber']:3d}: wrote id={new_id}")

        elif kind == "subsection":
            # Subsection under current section. If no section yet, treat as top-level.
            cells = build_cells(cells_by_id, src_cols, dst_cols, include_section_data=False)
            new_id = write_row(parent_id=current_section_id, cells=cells)
            current_subsection_id = new_id
            stats["subsection"] += 1
            print(f"  [subsection] {row['rowNumber']:3d}: wrote id={new_id} parent={current_section_id}")

        elif kind == "deliverable":
            parent = current_subsection_id or current_section_id
            cells = build_cells(cells_by_id, src_cols, dst_cols, include_section_data=True)
            new_id = write_row(parent_id=parent, cells=cells)
            stats["deliverable"] += 1
            print(f"  [deliv]      {row['rowNumber']:3d}: wrote id={new_id} parent={parent}")

    print()
    print("=== Migration complete ===")
    for k, n in stats.items():
        print(f"  {k:<12} {n}")
    print(f"  skipped breakdown: {skipped_kinds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
