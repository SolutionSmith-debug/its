"""Inspect Closeout K-1 source — find Bradley 1 rows + check picklist values."""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ss_api import get_sheet  # noqa: E402

SOURCE = 220029969715076
DEST = 4973530390155140


def main() -> None:
    src = get_sheet(SOURCE)
    dst = get_sheet(DEST)

    src_cols = {c["title"]: c for c in src["columns"]}
    dst_cols = {c["title"]: c for c in dst["columns"]}

    print(f"Source: {src['name']}")
    print(f"  rows: {len(src['rows'])}, cols: {len(src['columns'])}")
    print()

    # Check destination picklist options
    print("=== Destination picklist options ===")
    for title in ["Evergreen Submission Status", "Ball in Court", "Luminace Review Status", "Category"]:
        opts = dst_cols[title].get("options", [])
        print(f"  {title}: {opts}")
    print()

    # How are rows tagged with project? Check first 15 rows
    print("=== First 15 source rows ===")
    for r in src["rows"][:15]:
        cells = {c["columnId"]: c.get("value", "") for c in r["cells"]}
        delv = str(cells.get(src_cols["Exhibit K-1 Deliverables"]["id"], ""))[:60]
        pct = cells.get(src_cols["% Complete"]["id"])
        bic = cells.get(src_cols["Ball in Court"]["id"])
        ess = cells.get(src_cols["Evergreen Submission Status"]["id"])
        print(f"  r{r['rowNumber']:3d} | parentId={r.get('parentId')} | Delv='{delv}' | %={pct} | BiC={bic} | ESS={ess}")
    print()

    # Look for project tagging — could be in row hierarchy (parentId), or could be a parent-row name
    print("=== Distinct parent rows (likely project containers) ===")
    parents = {}
    for r in src["rows"]:
        parents[r["id"]] = r
    parent_ids_referenced = set(r.get("parentId") for r in src["rows"] if r.get("parentId"))
    for pid in parent_ids_referenced:
        if pid in parents:
            pr = parents[pid]
            cells = {c["columnId"]: c.get("value", "") for c in pr["cells"]}
            delv = str(cells.get(src_cols["Exhibit K-1 Deliverables"]["id"], ""))[:80]
            print(f"  parent rowId={pid} (rowNumber={pr['rowNumber']}): '{delv}'")
    print()

    # Count children per parent
    children_per_parent = Counter(r.get("parentId") for r in src["rows"] if r.get("parentId"))
    print("Children per parent:")
    for pid, n in children_per_parent.most_common():
        if pid in parents:
            pr = parents[pid]
            cells = {c["columnId"]: c.get("value", "") for c in pr["cells"]}
            delv = str(cells.get(src_cols["Exhibit K-1 Deliverables"]["id"], ""))[:50]
            print(f"  '{delv}' → {n} children")

    # All distinct values in Evergreen Submission Status and Ball in Court (for picklist check)
    print()
    print("=== Source distinct picklist values ===")
    for src_title in ["Evergreen Submission Status", "Ball in Court", "Luminace Review Status"]:
        cid = src_cols[src_title]["id"]
        vals = Counter(str(c.get("value", "")) for r in src["rows"] for c in r["cells"] if c["columnId"] == cid)
        print(f"  {src_title}:")
        for v, n in vals.most_common():
            print(f"    '{v}' x {n}")


if __name__ == "__main__":
    main()
