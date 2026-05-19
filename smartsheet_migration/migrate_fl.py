"""Financial Ledger migration: Bradley 1 only.

Source: 5931426745634692 ("4. Portfolio Financials")
Dest:   4956333341101956 (Bradley 1 / Financial Ledger)

Design (see ITS_Smartsheet_Handoff_v3 §6 and design discussion 2026-05-17):
- Source rows are overloaded: each row encodes up to 4 financial events
  (Contract, Change Order, Invoice, Payment).
- Dest is a ledger: one row per event with a Type column.
- This script "unfolds" each source row into 1-4 dest rows.
- Contract deduped to 1 per vendor; CO deduped to 1 per (vendor, CO#).

PRODUCTION NOTE (forward-looking, not handled here):
Each dest row WILL have a signed PDF attached at the row level in
production (signed contract → Contract row; vendor invoice PDF →
Invoice row; paid check/EFT receipt → Payment row). Smartsheet row
attachments live on the row itself, not a column; no schema change
needed. The per-event row model means one PDF per event, which is
the natural fit for downstream AI extraction.

CI note: keep ALL Smartsheet calls inside functions. Module-level
API calls break pytest collection (commit 3a89f6e issue).

Usage:
  python3 migrate_fl.py --mode dry        # parse + emit, no writes
  python3 migrate_fl.py --mode sample     # write Valmont block only
  python3 migrate_fl.py --mode full       # write all of Bradley 1
  python3 migrate_fl.py --mode sample --force   # bypass idempotency guard
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Iterable

from ss_api import api, add_rows

SRC_SHEET = 5931426745634692
DEST_SHEET = 4956333341101956
TARGET_PROJECT = "bradley 1"  # lowercase as in source c0
SAMPLE_VENDOR = "valmont"     # case-insensitive match against vendor name

# Source column indices (per Handoff v3 §3)
C_PROJECT = 0
C_CONTRACT = 1
C_CO_NUM = 2
C_CO_VAL = 3
C_SUM_TOTAL = 4
C_INV_DATE = 5
C_INV_AMT = 6
C_INV_NUM = 7
C_PAID_AMT = 8
C_PAID_DATE = 9
C_CHECK = 10
C_EFT = 11
C_BAL = 12

# Category prefix detection: lowercase substring → canonical label
CATEGORIES = [
    ("vendor:", "Vendor"),
    ("subcontractor:", "Subcontractor"),
    ("survey/engineering", "Survey/Engineering"),
    ("equipment rentals", "Equipment Rentals"),
    ("testing", "Testing"),
    ("permit", "Permit"),
    ("insurance", "Insurance"),
    ("bonding", "Bonding"),
    ("various", "Various"),
]

EFT_GLITCH = {"total committed", "paid above in tracker"}


def cell(row: dict, idx: int) -> Any:
    cells = row.get("cells", [])
    if idx >= len(cells):
        return None
    v = cells[idx].get("value")
    if v == "" or v is None:
        return None
    return v


def is_number(v: Any) -> bool:
    if v is None:
        return False
    try:
        float(v)
        return True
    except (ValueError, TypeError):
        return False


def parse_date(s: Any) -> str | None:
    """MM/DD/YY or MM/DD/YYYY → ISO YYYY-MM-DD. YY 00-69 → 20YY, else 19YY."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$", s)
    if not m:
        return None
    mo, dy, yr = m.groups()
    mo_i, dy_i, yr_i = int(mo), int(dy), int(yr)
    if yr_i < 100:
        yr_i = 2000 + yr_i if yr_i <= 69 else 1900 + yr_i
    return f"{yr_i:04d}-{mo_i:02d}-{dy_i:02d}"


def detect_category(c1: Any) -> str | None:
    if not isinstance(c1, str):
        return None
    s = c1.strip().lower()
    for prefix, label in CATEGORIES:
        if s.startswith(prefix):
            return label
    return None


def is_subtotal(r: dict) -> bool:
    """Subtotal rows have c4 populated and c2 blank (per Handoff v3)."""
    return cell(r, C_SUM_TOTAL) is not None and cell(r, C_CO_NUM) is None


def is_blank(r: dict) -> bool:
    return all(cell(r, k) is None for k in range(1, 15))


def is_target_project_row(r: dict) -> bool:
    c0 = cell(r, C_PROJECT)
    return isinstance(c0, str) and c0.strip().lower() == TARGET_PROJECT


def find_blocks(rows: list[dict]) -> list[dict]:
    """Walk source rows; return list of {category, vendor, txns} for Bradley 1."""
    blocks = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if is_target_project_row(r):
            cat = detect_category(cell(r, C_CONTRACT))
            if cat is not None:
                vendor_raw = cell(r, C_CO_NUM)
                vendor = str(vendor_raw).strip() if vendor_raw is not None else ""
                # Skip column-header row (i+1) and walk transactions until next vendor header or subtotal
                j = i + 2
                txns = []
                while j < len(rows):
                    rj = rows[j]
                    if not is_target_project_row(rj):
                        # Project changed or empty c0 — block ends
                        break
                    if detect_category(cell(rj, C_CONTRACT)) is not None:
                        # Next vendor block
                        break
                    if is_subtotal(rj):
                        # Skip subtotal and stop block
                        j += 1
                        break
                    if is_blank(rj):
                        j += 1
                        continue
                    txns.append(rj)
                    j += 1
                blocks.append({"category": cat, "vendor": vendor, "txns": txns, "src_row": rows[i].get("rowNumber")})
                i = j
                continue
        i += 1
    return blocks


def derive_payment_method(check: Any, eft: Any) -> tuple[str, str, list[str]]:
    """Return (method, reference, warnings). Method may be '' if undeterminable."""
    warnings: list[str] = []
    # c10 first
    if check is not None:
        sv = str(check).strip()
        low = sv.lower()
        if low == "amex":
            return "Card", "AMEX", warnings
        if low == "ach":
            return "ACH", "", warnings
        if "online" in low:  # 'online' or 'online payment'
            return "", "online payment", warnings
        if "wire" in low:
            return "Wire", "", warnings
        if "eft" in low:
            return "EFT", "", warnings
        # Numeric → Check
        if is_number(sv):
            try:
                n = int(float(sv))
                return "Check", str(n), warnings
            except ValueError:
                pass
        warnings.append(f"unknown c10 value: {sv!r}")
    # c11 fallback
    if eft is not None:
        sv = str(eft).strip()
        low = sv.lower()
        if low == "eft":
            return "EFT", "", warnings
        if low == "wire":
            return "Wire", "", warnings
        if low in EFT_GLITCH or is_number(sv):
            warnings.append(f"glitch c11 value: {sv!r} (dropped)")
            return "", "", warnings
        warnings.append(f"unknown c11 value: {sv!r}")
    return "", "", warnings


def fmt_amount(v: Any) -> Any:
    """Return numeric for the Amount column (Smartsheet TEXT_NUMBER accepts numbers)."""
    if v is None:
        return None
    try:
        f = float(v)
        # Round to 2dp for cleanliness; preserve as float
        return round(f, 2)
    except (ValueError, TypeError):
        return None


def emit_rows_for_block(block: dict, skipped_log: list[str]) -> list[dict]:
    """Return a flat list of dest rows for this vendor block. No parent /
    no hierarchy — keeps the sheet compatible with Smartsheet Forms,
    which only append flat rows. Category notation lands on the Contract
    row (one per vendor); when the sheet is sorted by Vendor + Date,
    that row sits first in each vendor's group, so PMs see the category
    once per vendor without 14× repetition.
    """
    vendor = block["vendor"]
    category = block["category"]

    if not vendor:
        skipped_log.append(
            f"BLOCK src_row={block['src_row']}: orphan unnamed {category} block ({len(block['txns'])} txns) — SKIPPED"
        )
        return []

    rows: list[dict] = []
    seen_co = set()
    contract_emitted = False

    for r in block["txns"]:
        row_num = r.get("rowNumber")
        contract_val = cell(r, C_CONTRACT)
        co_num = cell(r, C_CO_NUM)
        co_val = cell(r, C_CO_VAL)
        inv_date = parse_date(cell(r, C_INV_DATE))
        inv_amt = cell(r, C_INV_AMT)
        inv_num = cell(r, C_INV_NUM)
        paid_amt = cell(r, C_PAID_AMT)
        paid_date = parse_date(cell(r, C_PAID_DATE))
        check = cell(r, C_CHECK)
        eft = cell(r, C_EFT)

        # ---- Contract row (once per vendor; carries Category note) ----
        if not contract_emitted and is_number(contract_val):
            rows.append({
                "Transaction": f"{vendor} — Contract",
                "Vendor": vendor,
                "Type": "Contract",
                "Date": inv_date or paid_date or "",
                "Amount": fmt_amount(contract_val),
                "Status": "Issued",
                "Notes": f"Category: {category}",
            })
            contract_emitted = True

        # ---- Change Order row (once per CO#) ----
        if co_num is not None and is_number(co_num) and co_num not in seen_co:
            seen_co.add(co_num)
            try:
                co_label = f"CO #{int(float(co_num))}"
            except (ValueError, TypeError):
                co_label = f"CO #{co_num}"
            co_amount = fmt_amount(co_val) if is_number(co_val) else 0
            rows.append({
                "Transaction": f"{vendor} — {co_label}",
                "Vendor": vendor,
                "Type": "Change Order",
                "Reference #": co_label,
                "Date": inv_date or paid_date or "",
                "Amount": co_amount,
                "Status": "Issued",
            })

        # ---- Invoice row ----
        if is_number(inv_amt):
            if inv_num is None:
                inv_ref = ""
            elif isinstance(inv_num, (int, float)):
                inv_ref = str(int(inv_num)) if float(inv_num).is_integer() else str(inv_num)
            else:
                inv_ref = str(inv_num).strip()
                if inv_ref.endswith(".0") and is_number(inv_ref):
                    inv_ref = inv_ref[:-2]
            paid_in_full = (
                is_number(paid_amt)
                and is_number(inv_amt)
                and abs(float(paid_amt) - float(inv_amt)) < 0.01
            )
            resolved_inv_date = inv_date or paid_date
            if inv_ref:
                txn_label = f"{vendor} — Invoice {inv_ref}"
            elif resolved_inv_date:
                txn_label = f"{vendor} — Invoice {resolved_inv_date}"
            else:
                txn_label = f"{vendor} — Invoice"
            rows.append({
                "Transaction": txn_label,
                "Vendor": vendor,
                "Type": "Invoice",
                "Reference #": inv_ref,
                "Date": inv_date or paid_date or "",
                "Amount": fmt_amount(inv_amt),
                "Status": "Paid" if paid_in_full else "Issued",
            })

        # ---- Payment row ----
        if is_number(paid_amt):
            method, ref, warns = derive_payment_method(check, eft)
            for w in warns:
                skipped_log.append(f"src_row={row_num} payment: {w}")
            if inv_num is None:
                inv_ref_note = ""
            elif isinstance(inv_num, (int, float)) and float(inv_num).is_integer():
                inv_ref_note = f"Inv #{int(inv_num)}"
            else:
                inv_ref_note = f"Inv #{inv_num}"
            resolved_pmt_date = paid_date or inv_date
            if ref:
                txn_label = f"{vendor} — Payment {ref}"
            elif method:
                txn_label = f"{vendor} — Payment {method}"
            elif resolved_pmt_date:
                txn_label = f"{vendor} — Payment {resolved_pmt_date}"
            else:
                txn_label = f"{vendor} — Payment"
            rows.append({
                "Transaction": txn_label,
                "Vendor": vendor,
                "Type": "Payment",
                "Reference #": ref,
                "Date": paid_date or inv_date or "",
                "Amount": fmt_amount(paid_amt),
                "Status": "Paid",
                "Payment Method": method,
                "Notes": inv_ref_note,
            })

    return rows


def build_row_payload(d: dict, col_map: dict[str, int], parent_id: int | None = None) -> dict:
    """Convert a dict row into a Smartsheet row payload. Honors _expanded
    flag; sets parentId if provided (mutually exclusive with toBottom)."""
    cells = []
    for title, col_id in col_map.items():
        if title.startswith("_"):
            continue
        val = d.get(title)
        if val is None or val == "":
            continue
        cells.append({"columnId": col_id, "value": val, "strict": True})
    payload: dict = {"cells": cells}
    if parent_id is not None:
        payload["parentId"] = parent_id
    else:
        payload["toBottom"] = True
    if "_expanded" in d:
        payload["expanded"] = d["_expanded"]
    return payload


def chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_source_rows() -> list[dict]:
    s = api("GET", f"/sheets/{SRC_SHEET}")
    return s.get("rows", [])


def fetch_dest_col_map() -> dict[str, int]:
    s = api("GET", f"/sheets/{DEST_SHEET}")
    return {c["title"]: c["id"] for c in s["columns"]}


def fetch_dest_row_count() -> int:
    s = api("GET", f"/sheets/{DEST_SHEET}", query={"include": "rowPermalink"})
    return len(s.get("rows", []))


def fetch_dest_row_ids() -> list[int]:
    s = api("GET", f"/sheets/{DEST_SHEET}")
    return [r["id"] for r in s.get("rows", [])]


def clear_dest(row_ids: list[int]) -> int:
    """Delete all rows in dest. Smartsheet allows up to 450 IDs per DELETE."""
    if not row_ids:
        return 0
    total = 0
    for batch in chunks(row_ids, 400):
        ids_str = ",".join(str(i) for i in batch)
        api("DELETE", f"/sheets/{DEST_SHEET}/rows", query={"ids": ids_str})
        total += len(batch)
    return total


def run(mode: str, force: bool = False, clear: bool = False) -> int:
    print(f"=== FL migration — mode={mode} force={force} clear={clear} ===")
    rows = fetch_source_rows()
    print(f"Source rows fetched: {len(rows)}")
    blocks = find_blocks(rows)
    print(f"Bradley 1 blocks: {len(blocks)}")

    skipped_log: list[str] = []
    all_dest: list[dict] = []
    for b in blocks:
        emitted = emit_rows_for_block(b, skipped_log)
        all_dest.extend(emitted)

    print(f"Dest rows emitted (flat, all Bradley 1): {len(all_dest)}")
    if skipped_log:
        print(f"Warnings/skips: {len(skipped_log)}")
        for w in skipped_log[:30]:
            print(f"  • {w}")
        if len(skipped_log) > 30:
            print(f"  • ... and {len(skipped_log) - 30} more")

    # Mode-specific filter
    if mode == "sample":
        all_dest = [d for d in all_dest if d.get("Vendor", "").lower() == SAMPLE_VENDOR]
        print(f"Sample subset ({SAMPLE_VENDOR}): {len(all_dest)} rows")
    elif mode == "dry":
        print("\n--- Dry run: first 20 rows ---")
        for d in all_dest[:20]:
            preview = {k: v for k, v in d.items() if v not in (None, "")}
            print(f"  {preview}")
        print("\n--- Per-vendor tallies ---")
        by_vendor: dict[str, dict[str, int]] = {}
        for d in all_dest:
            v = d.get("Vendor") or "(unnamed)"
            t = d.get("Type", "?")
            by_vendor.setdefault(v, {}).setdefault(t, 0)
            by_vendor[v][t] += 1
        for v, types in sorted(by_vendor.items()):
            total = sum(types.values())
            type_str = " / ".join(f"{t}={n}" for t, n in sorted(types.items()))
            print(f"  {v:<40} total={total:<4}  ({type_str})")
        return 0
    elif mode != "full":
        print(f"Unknown mode: {mode}", file=sys.stderr)
        return 2

    if not all_dest:
        print("Nothing to write.", file=sys.stderr)
        return 1

    if clear:
        existing_ids = fetch_dest_row_ids()
        if existing_ids:
            n = clear_dest(existing_ids)
            print(f"Cleared {n} existing rows from dest.")

    existing_count = len(fetch_dest_row_ids())
    print(f"Dest sheet existing rows: {existing_count}")
    if existing_count > 0 and not force:
        print("REFUSING to write — dest is non-empty. Re-run with --force or --clear to bypass.", file=sys.stderr)
        return 3

    col_map = fetch_dest_col_map()
    payloads = [build_row_payload(d, col_map, parent_id=None) for d in all_dest]
    print(f"Writing {len(payloads)} rows in batches of 200…")
    total = 0
    for batch in chunks(payloads, 200):
        resp = add_rows(DEST_SHEET, batch)
        n = len(resp.get("result", []))
        total += n
        print(f"  batch wrote {n} rows")
    print(f"DONE. Total rows written: {total}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["dry", "sample", "full"], required=True)
    p.add_argument("--force", action="store_true", help="bypass idempotency guard")
    p.add_argument("--clear", action="store_true", help="delete all existing dest rows before writing")
    args = p.parse_args()
    return run(args.mode, args.force, args.clear)


if __name__ == "__main__":
    sys.exit(main())
