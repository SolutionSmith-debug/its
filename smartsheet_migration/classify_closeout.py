"""Classify every row in Closeout K-1 source as:
  - master_rollup: row 1 (TOTAL SHEET)
  - section: rows matching '^[A-Z]\\.\\s' (e.g. 'A. System Testing Overview')
  - subsection: aggregate rows (no boolean % Complete, no ESS/BiC) that aren't sections
  - deliverable: actual K-1 line items
"""
import re
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ss_api import get_sheet

SOURCE = 220029969715076

SECTION_RE = re.compile(r"^[A-Z]\.\s")

def classify(row, cols):
    cells = {c["columnId"]: c.get("value") for c in row["cells"]}
    delv = str(cells.get(cols["Exhibit K-1 Deliverables"]["id"], "")).strip()
    pct = cells.get(cols["% Complete"]["id"])
    ess = cells.get(cols["Evergreen Submission Status"]["id"])
    bic = cells.get(cols["Ball in Court"]["id"])
    if delv == "TOTAL SHEET % COMPLETE":
        return "master_rollup"
    if SECTION_RE.match(delv):
        return "section"
    # Boolean % (True/False) + populated ESS/BiC = deliverable
    if isinstance(pct, bool):
        return "deliverable"
    # Has ESS or BiC populated → likely deliverable even if % isn't bool
    if ess or bic:
        return "deliverable"
    # Otherwise — % is a percentage-string (rollup), or row is empty → subsection / aggregate
    return "subsection"


def main():
    src = get_sheet(SOURCE)
    cols = {c["title"]: c for c in src["columns"]}

    counts = {"master_rollup": 0, "section": 0, "subsection": 0, "deliverable": 0}
    for r in src["rows"]:
        k = classify(r, cols)
        counts[k] += 1

    print(f"Total rows: {len(src['rows'])}")
    print("Classification:")
    for k, n in counts.items():
        print(f"  {k:<15} {n}")
    print()

    # Print full structure with classification
    print("=== FULL STRUCTURE ===")
    for r in src["rows"]:
        cells = {c["columnId"]: c.get("value") for c in r["cells"]}
        delv = str(cells.get(cols["Exhibit K-1 Deliverables"]["id"], ""))[:60]
        kind = classify(r, cols)
        marker = {"master_rollup": "★", "section": "▼", "subsection": "▸", "deliverable": "  "}[kind]
        print(f"  r{r['rowNumber']:3d} [{kind:<12}] {marker} {delv}")


if __name__ == "__main__":
    main()
