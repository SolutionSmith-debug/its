"""Seed ITS_Vendors from the PO-corpus vendor set + the old Operations Vendor DB (S1).

Two sources, corpus first (it carries the richer profile data):

  1. **Corpus set** — the 8 vendors reverse-engineered from Evergreen's real PO corpus
     (docs/reports/2026-07-09_po_corpus_analysis.md §7): name, address, categories,
     region, default terms profile. Contact person/email/phone are NOT in the corpus —
     left blank pending Teala's vendor list (Day-1 external request).
  2. **One-time copy** of the old Operations "Vendor DB" rows
     (shared/sheet_ids.SHEET_VENDOR_DB — DECOMMISSIONED, retired-in-place). Its 2026-05-17
     auto-seeded stubs map: Vendor → Vendor Name, Primary Contact/Email/Phone → Contact
     fields; Specialty / Payment Terms / Vendor Type / Preferred Status / Notes fold into
     Notes with provenance. Region + Supply Categories stay blank (unknown — operator
     completes); Default Terms Profile defaults to standard_17.

Vendor Keys (VEN-######, decision D4) are allocated here sequentially past the highest
key already on the sheet — immutable once written; the D1 cache and po_send join on them.

Idempotency: rows match by NORMALIZED Vendor Name (lowercase, alphanumeric collapse);
near-duplicates where one normalized name contains the other (≥4 chars — e.g. corpus
"B2 Sales" vs old-DB "B2 Sales / Zpower") are SKIPPED LOUDLY for operator review rather
than double-seeded. Existing sheet rows are never modified.

FLIP precedes SEED: refuses to run while SHEET_ITS_VENDORS is 0.

Writes go through shared.smartsheet_client.add_rows → the picklist REGISTRY gates
Region / Supply Categories / Default Terms Profile / Active (registered in the same PR;
tests/test_po_s1_sheets.py pins corpus values ⊆ registry sets so this seed can never
drift outside its own write-gate).

    python3 scripts/migrations/seed_its_vendors.py --dry-run
    python3 scripts/migrations/seed_its_vendors.py
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

from shared import sheet_ids, smartsheet_client  # noqa: E402

VENDOR_KEY_RE = re.compile(r"^VEN-(\d{6})$")

# ---- Corpus seed set (docs/reports/2026-07-09_po_corpus_analysis.md §7) ----------
# Family-B issuers (Community Power Group) + guarantors (Coast Energy DevCo) are NOT
# vendors Evergreen buys from — excluded. Contacts pending Teala's list.
CORPUS_VENDORS: list[dict[str, Any]] = [
    {"Vendor Name": "Chint Power Systems (CPS)",
     "Address": "2801 N State Hwy 78 Ste 100, Wylie, TX",
     "Region": "National", "Supply Categories": ["inverters"],
     "Default Terms Profile": "chint_vendor",
     "Notes": "String inverters, FlexOM. 'CPS Standard Terms apply' on Chint POs (corpus §6.3)."},
    {"Vendor Name": "VSUN Solar USA Inc",
     "Address": "909 Corporate Way, Fremont, CA",
     "Region": "National", "Supply Categories": ["modules"],
     "Default Terms Profile": "negotiated_gtc",
     "Notes": "PV modules (per-watt POs). Negotiated multi-page GTC (attach-not-generate, corpus §6.4)."},
    {"Vendor Name": "Also Energy",
     "Address": "5400 Airport Blvd Ste 100, Boulder, CO",
     "Region": "National", "Supply Categories": ["other"],
     "Default Terms Profile": "standard_17",
     "Notes": "Monitoring / DAS."},
    {"Vendor Name": "B2 Sales",
     "Address": "1866 N Carlsbad St, Orange, CA",
     "Region": "West", "Supply Categories": ["switchgear"],
     "Default Terms Profile": "standard_17",
     "Notes": "Switchgear / distribution. POs often carry the vendor quote appended (Family C)."},
    {"Vendor Name": "American Steel",
     "Address": "525 S Sequoia Pkwy, Canby, OR",
     "Region": "West", "Supply Categories": ["racking"],
     "Default Terms Profile": "standard_17",
     "Notes": "Galvanized I-beams & plates (racking structural steel)."},
    {"Vendor Name": "W.O Grubb Crane Rental",
     "Address": "5120 Route 1, N Chesterfield, VA",
     "Region": "East", "Supply Categories": ["tools_rentals"],
     "Default Terms Profile": "standard_17",
     "Notes": "Crane / storage / logistics."},
    {"Vendor Name": "Ampacity, LLC (ATI)",
     "Address": "305 Dela Vina Ave, Monterey, CA",
     "Region": "West", "Supply Categories": ["racking"],
     "Default Terms Profile": "standard_17",
     "Notes": "Racking. Historic POs were CPG owner-form (Family B) — future ITS POs use Family A."},
    {"Vendor Name": "Rexel",
     "Address": "8428 Lee Hwy, Fairfax, VA",
     "Region": "East", "Supply Categories": ["transformers", "switchgear"],
     "Default Terms Profile": "standard_17",
     "Notes": "Transformers, switchboard (Eaton MV gear via Rexel). Historic POs were CPG owner-form."},
]


def _norm(name: str) -> str:
    """Normalize a vendor name for duplicate matching: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def is_duplicate_name(a: str, b: str) -> bool:
    """Whether two vendor names refer to the same vendor.

    Exact normalized match, or containment either way when the shorter normalized
    form is ≥4 chars (catches "B2 Sales" vs "B2 Sales / Zpower" without letting
    2–3-char fragments false-positive)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 4 and shorter in longer


def next_key_start(existing_keys: list[str]) -> int:
    """The next VEN- serial: max of well-formed existing keys + 1 (malformed ignored)."""
    highest = 0
    for key in existing_keys:
        m = VENDOR_KEY_RE.match((key or "").strip())
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def format_key(serial: int) -> str:
    return f"VEN-{serial:06d}"


def map_old_db_row(row: dict[str, Any], today: str) -> dict[str, Any]:
    """Map one old Operations Vendor DB row onto the ITS_Vendors schema.

    Region + Supply Categories deliberately absent (unknown for the old stubs —
    blanks pass the picklist gate and the operator completes them); everything the
    new schema has no column for folds into Notes with provenance."""
    notes_bits: list[str] = []
    for label, col in (("Specialty", "Specialty / Products"),
                       ("Payment terms", "Payment Terms"),
                       ("Type", "Vendor Type"),
                       ("Preferred", "Preferred Status")):
        val = str(row.get(col) or "").strip()
        if val:
            notes_bits.append(f"{label}: {val}")
    old_notes = str(row.get("Notes") or "").strip()
    if old_notes:
        notes_bits.append(old_notes)
    notes_bits.append(
        f"One-time copy from Operations Vendor DB ({sheet_ids.SHEET_VENDOR_DB}) on {today}."
    )
    return {
        "Vendor Name": str(row.get("Vendor") or "").strip(),
        "Contact Name": str(row.get("Primary Contact") or "").strip(),
        "Contact Email": str(row.get("Email") or "").strip(),
        "Contact Phone": str(row.get("Phone") or "").strip(),
        "Default Terms Profile": "standard_17",
        "Notes": " | ".join(notes_bits),
    }


def build_seed_rows(
    existing_names: list[str],
    existing_keys: list[str],
    old_db_rows: list[dict[str, Any]],
    today: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compose the rows to add (corpus first, then old-DB copies) + loud skip notes.

    Dedupes against the existing sheet AND within the batch via is_duplicate_name;
    allocates Vendor Keys sequentially past the sheet's highest existing key."""
    skips: list[str] = []
    to_add: list[dict[str, Any]] = []
    seen_names: list[str] = list(existing_names)
    serial = next_key_start(existing_keys)

    def _try_add(candidate: dict[str, Any], source: str) -> None:
        nonlocal serial
        name = candidate["Vendor Name"]
        if not name:
            skips.append(f"[skip] {source}: blank vendor name — not seeded.")
            return
        dup = next((s for s in seen_names if is_duplicate_name(name, s)), None)
        if dup is not None:
            skips.append(f"[skip] {source}: {name!r} matches existing/seeded {dup!r} — review manually.")
            return
        row = dict(candidate)
        row["Vendor Key"] = format_key(serial)
        row["Active"] = "Active"
        serial += 1
        seen_names.append(name)
        to_add.append(row)

    for vendor in CORPUS_VENDORS:
        candidate = dict(vendor)
        candidate.setdefault("Notes", "")
        candidate["Notes"] = (candidate["Notes"] + " " if candidate["Notes"] else "") + (
            f"Seeded from PO-corpus analysis on {today}; contact pending Teala's vendor list."
        )
        _try_add(candidate, "corpus")

    for old in old_db_rows:
        _try_add(map_old_db_row(old, today), "old Vendor DB")

    return to_add, skips


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed ITS_Vendors from the PO corpus + old Operations Vendor DB (PO S1)."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not sheet_ids.SHEET_ITS_VENDORS:
        print("[error] SHEET_ITS_VENDORS is still 0 in shared/sheet_ids.py (FLIP precedes SEED).\n"
              "        Run build_its_vendors_sheet.py first and flip the printed id.",
              file=sys.stderr)
        return 2

    today = datetime.now(UTC).date().isoformat()
    print(f"[info] Target: ITS_Vendors ({sheet_ids.SHEET_ITS_VENDORS})")
    print(f"[info] Old Vendor DB source: {sheet_ids.SHEET_VENDOR_DB}")
    print(f"[info] Mode: {'DRY-RUN' if args.dry_run else 'LIVE WRITE'}\n")

    existing = smartsheet_client.get_rows(sheet_ids.SHEET_ITS_VENDORS)
    existing_names = [str(r.get("Vendor Name") or "") for r in existing]
    existing_keys = [str(r.get("Vendor Key") or "") for r in existing]
    old_db_rows = smartsheet_client.get_rows(sheet_ids.SHEET_VENDOR_DB)
    print(f"[info] Existing ITS_Vendors rows: {len(existing)}; old Vendor DB rows: {len(old_db_rows)}\n")

    to_add, skips = build_seed_rows(existing_names, existing_keys, old_db_rows, today)
    for line in skips:
        print(line)
    for row in to_add:
        cats = ",".join(row.get("Supply Categories") or []) or "-"
        print(f"[plan] {row['Vendor Key']}  {row['Vendor Name']!r}  region={row.get('Region', '-')}"
              f"  categories={cats}  terms={row['Default Terms Profile']}")

    if not to_add:
        print("\n[ok] Nothing to seed — all candidates already present.")
        return 0
    if args.dry_run:
        print(f"\n[dry-run] Would add {len(to_add)} rows.")
        return 0

    row_ids = smartsheet_client.add_rows(sheet_ids.SHEET_ITS_VENDORS, to_add)
    print(f"\n[ok] added {len(row_ids)} vendor rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
