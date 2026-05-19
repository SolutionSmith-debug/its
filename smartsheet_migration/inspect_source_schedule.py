"""Quick inspection of source Bradley schedule rows."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ss_api import get_sheet  # noqa: E402

SOURCE_SCHEDULE = 1417836219027332


def main() -> None:
    sheet = get_sheet(SOURCE_SCHEDULE)
    cols = {c["id"]: c["title"] for c in sheet["columns"]}

    print(f"Sheet: {sheet['name']}")
    print(f"Total rows: {len(sheet['rows'])}")
    print(f"Columns: {len(sheet['columns'])}")
    print("=" * 100)

    # Show first 20 rows
    for r in sheet["rows"][:20]:
        cd = {cols[c["columnId"]]: c.get("value", "") for c in r["cells"]}
        pns = str(cd.get("Project Name & Sub-Section", ""))[:30]
        tn = str(cd.get("Task Name", ""))[:40]
        fbl = str(cd.get("Filter By List", ""))[:20]
        cm = str(cd.get("Contract Milestone", ""))[:15]
        sd = str(cd.get("Start Date", ""))[:10]
        ed = str(cd.get("Completion Date", ""))[:10]
        print(f"r{r['rowNumber']:2d} | PNS={pns:<30} | Task={tn:<40} | Filter={fbl:<20} | MS={cm:<15} | {sd}→{ed}")

    print("\n--- Filter By List distinct values ---")
    fbl_vals = set()
    for r in sheet["rows"]:
        cd = {cols[c["columnId"]]: c.get("value", "") for c in r["cells"]}
        v = cd.get("Filter By List")
        if v:
            fbl_vals.add(str(v))
    for v in sorted(fbl_vals):
        print(f"  - {v}")

    print("\n--- Rows with empty Task Name (candidate section headers) ---")
    empty_tn = 0
    for r in sheet["rows"]:
        cd = {cols[c["columnId"]]: c.get("value", "") for c in r["cells"]}
        if not cd.get("Task Name"):
            empty_tn += 1
            pns = str(cd.get("Project Name & Sub-Section", ""))[:50]
            print(f"  r{r['rowNumber']:2d} | PNS={pns}")
    print(f"Total: {empty_tn}")


if __name__ == "__main__":
    main()
