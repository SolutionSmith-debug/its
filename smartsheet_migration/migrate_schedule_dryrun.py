"""Bradley 1 Schedule migration — DRY RUN.

Source: 1417836219027332 (Bradley 12.8.25)
Dest:   6008505839341444 (Bradley 1 / Schedule)

Plan:
1. Pull source rows, filter to leaf tasks (Filter By List is non-empty).
2. Map each row through SRC_TO_DEST_TITLE.
3. Print sample + value-distribution sanity checks. DO NOT write.

Run real migration via migrate_schedule.py.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ss_api import get_sheet  # noqa: E402

SOURCE = 1417836219027332
DEST = 6008505839341444

# title → title mapping
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
    # "Project Name & Sub-Section" — dropped
}


def main() -> None:
    # Get sheets
    print("Fetching source + destination sheets...")
    src = get_sheet(SOURCE)
    dst = get_sheet(DEST)

    src_cols_by_title = {c["title"]: c for c in src["columns"]}
    dst_cols_by_title = {c["title"]: c for c in dst["columns"]}

    # Verify destination has all target columns
    for src_title, dst_title in SRC_TO_DEST_TITLE.items():
        assert dst_title in dst_cols_by_title, f"Missing dest column: {dst_title}"

    # Filter rows: keep only those with Filter By List non-empty
    src_filter_col_id = src_cols_by_title["Filter By List"]["id"]
    leaf_rows = []
    filtered_out = []
    for r in src["rows"]:
        cells = {c["columnId"]: c.get("value") for c in r["cells"]}
        if cells.get(src_filter_col_id):
            leaf_rows.append(r)
        else:
            filtered_out.append(r)

    print(f"Source total rows: {len(src['rows'])}")
    print(f"  - Leaf tasks (Filter By List populated): {len(leaf_rows)}")
    print(f"  - Filtered out: {len(filtered_out)}")
    print()

    # Show what we're filtering out
    print("Rows being filtered OUT:")
    for r in filtered_out:
        cells = {c["columnId"]: c.get("value") for c in r["cells"]}
        tn = cells.get(src_cols_by_title["Task Name"]["id"], "")
        pns = cells.get(src_cols_by_title["Project Name & Sub-Section"]["id"], "")
        print(f"  r{r['rowNumber']:2d}: Task='{tn}' PNS='{pns}'")
    print()

    # Value distribution sanity checks
    def colvals(rows, title):
        cid = src_cols_by_title[title]["id"]
        return Counter(str(c.get("value", "")) for r in rows for c in r["cells"] if c["columnId"] == cid)

    print("Distribution: Filter By List → Phase (mapped)")
    for v, n in sorted(colvals(leaf_rows, "Filter By List").items()):
        print(f"  {v:<20} x {n}")
    print()

    print("Distribution: Contract Milestone")
    for v, n in sorted(colvals(leaf_rows, "Contract Milestone").items()):
        print(f"  '{v:<15}' x {n}")
    print()

    # Verify Phase picklist accepts all source values
    dst_phase_opts = set(dst_cols_by_title["Phase"]["options"])
    src_phase_vals = {v for v in colvals(leaf_rows, "Filter By List") if v}
    missing = src_phase_vals - dst_phase_opts
    if missing:
        print(f"⚠️  Phase picklist MISSING values: {missing}")
    else:
        print(f"✓ Phase picklist covers all {len(src_phase_vals)} source values")
    print()

    # Verify Contract Milestone
    dst_cm_opts = set(dst_cols_by_title["Contract Milestone"]["options"])
    src_cm_vals = {v for v in colvals(leaf_rows, "Contract Milestone") if v}
    missing_cm = src_cm_vals - dst_cm_opts
    if missing_cm:
        print(f"⚠️  Contract Milestone picklist MISSING values: {missing_cm}")
    else:
        print(f"✓ Contract Milestone picklist covers all {len(src_cm_vals)} source values")
    print()

    print("Distribution: % Done")
    for v, n in sorted(colvals(leaf_rows, "% Done").items()):
        print(f"  '{v:<15}' x {n}")
    print()

    # Sample first 3 mapped rows
    print("=== Sample mapped rows (first 3) ===")
    for r in leaf_rows[:3]:
        cells = {c["columnId"]: c.get("value") for c in r["cells"]}
        print(f"\nrow {r['rowNumber']}:")
        for src_title, dst_title in SRC_TO_DEST_TITLE.items():
            sval = cells.get(src_cols_by_title[src_title]["id"])
            dst_col_id = dst_cols_by_title[dst_title]["id"]
            print(f"  {src_title!r} → {dst_title!r}  (dest col {dst_col_id})  value={sval!r}")


if __name__ == "__main__":
    main()
