"""PO S1 structural tests — builder↔registry parity, the WSR schema-twin contract,
and the seed's pure helpers.

These are the S1 controls-that-bite:

  * Builder option lists vs the picklist REGISTRY value sets (the #247→#253 class:
    an option the builder creates but the write-gate lacks blocks the live write
    path with PicklistViolationError — invisible to mocks).
  * The PO_Pending_Review schema-twin contract: identical (title, type,
    systemColumnType) tuples to the WPR builder, and every `wsr_review` COL_*
    protocol title present — this is what lets the shared send engine bind a
    po_review module in S5 without surgery. Retitle a protocol column and these red.
  * Corpus seed values ⊆ the registry sets (the seed can never drift outside its
    own write-gate).
  * MULTI_PICKLIST plumbing: list-aware validate_cell + the objectValue cell build.

All Smartsheet calls mocked — never hits the live API.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# sys.path-driven import (scripts/ has no __init__.py) — mirrors
# tests/test_add_wsr_workstream_column.py.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import build_its_vendors_sheet as vendors_mig  # noqa: E402
import build_po_log_sheet as po_log_mig  # noqa: E402
import build_po_pending_review_sheet as po_review_mig  # noqa: E402
import build_wpr_human_review_sheet as wpr_mig  # noqa: E402
import seed_its_vendors as seed  # noqa: E402

from safety_reports import wsr_review  # noqa: E402
from shared import picklist_validation, sheet_ids, smartsheet_client  # noqa: E402
from shared.picklist_validation import PicklistViolationError, validate_cell  # noqa: E402

# ---- Builder ↔ REGISTRY option parity -------------------------------------


def test_vendors_builder_options_match_registry_sets():
    assert set(vendors_mig.REGION_OPTIONS) == picklist_validation._VENDOR_REGION_VALUES
    assert set(vendors_mig.SUPPLY_CATEGORY_OPTIONS) == picklist_validation._VENDOR_SUPPLY_CATEGORY_VALUES
    assert set(vendors_mig.TERMS_PROFILE_OPTIONS) == picklist_validation._VENDOR_TERMS_PROFILE_VALUES
    assert set(vendors_mig.ACTIVE_OPTIONS) == picklist_validation._ACTIVE_LIFECYCLE_VALUES


def test_po_log_builder_status_options_match_registry_set():
    assert set(po_log_mig.STATUS_OPTIONS) == picklist_validation._PO_LOG_STATUS_VALUES


def test_po_review_builder_options_match_registry_sets():
    assert set(po_review_mig.SEND_STATUS_OPTIONS) == picklist_validation._PO_SEND_STATUS_VALUES
    assert set(po_review_mig.WORKSTREAM_OPTIONS) == picklist_validation._PO_WORKSTREAM_VALUES


def test_po_send_status_set_is_the_wsr_sending_inclusive_set():
    """The PO review sheet rides the SAME shared send engine — the write-ahead SENDING
    marker contract comes with it, so the Send Status gate must be the WSR set."""
    assert picklist_validation._PO_SEND_STATUS_VALUES == picklist_validation._WSR_SEND_STATUS_VALUES
    assert "SENDING" in picklist_validation._PO_SEND_STATUS_VALUES


def test_po_workstream_value_set_is_po_materials_only():
    """P1b contamination guard vocabulary: the PO review sheet's tag is exactly
    {po_materials} — a safety/progress tag here must raise at the write gate."""
    assert picklist_validation._PO_WORKSTREAM_VALUES == frozenset({"po_materials"})
    for family in ("safety", "progress"):
        assert family not in picklist_validation._PO_WORKSTREAM_VALUES


# ---- The WSR schema-twin contract (S5 engine bind) ------------------------


def _shape(schema: list[dict]) -> list[tuple]:
    return [(c["title"], c["type"], c.get("systemColumnType")) for c in schema]


def test_po_review_schema_is_exact_structural_twin_of_wpr():
    """(title, type, systemColumnType) tuples identical, in order, to the WPR builder —
    options/descriptions differ by design (Workstream tag, PO semantics), structure
    may not. This is the strongest static guarantee the S5 SendConfig bind holds."""
    assert _shape(po_review_mig.COLUMN_SCHEMA) == _shape(wpr_mig.COLUMN_SCHEMA)


def test_po_review_schema_carries_every_wsr_protocol_title():
    """Every wsr_review COL_* constant must be a column title here — the send engine
    reads rows by these exact titles (Vendor Key rides in 'Job ID', the PO date in
    'Week Of', the PO PDF in 'Compiled PDF'; S1 protocol-slot contract)."""
    protocol_titles = {
        v for n, v in vars(wsr_review).items()
        if n.startswith("COL_") and isinstance(v, str)
    }
    assert protocol_titles, "no COL_* constants found in wsr_review — test wiring broke"
    builder_titles = {c["title"] for c in po_review_mig.COLUMN_SCHEMA}
    missing = protocol_titles - builder_titles
    assert not missing, (
        f"PO_Pending_Review builder is missing protocol column(s) {sorted(missing)} — "
        "the shared send engine binds by these titles (S1 schema-twin contract)."
    )


def test_po_review_datetime_columns_are_date_not_abstract_datetime():
    """ABSTRACT_DATETIME is not API-creatable (errorCode 1142 — the live-verified WPR
    lesson). Regression pin: the twin must ship DATE."""
    types = {c["title"]: c["type"] for c in po_review_mig.COLUMN_SCHEMA}
    assert types["Approved At"] == "DATE"
    assert types["Sent At"] == "DATE"


def test_po_registry_entries_conditional_on_real_sheet_ids():
    """Placeholder-0 guard: no REGISTRY entry until the operator flips the real ids
    post-build (mirrors the Trusted Contacts / progress-sheets guard)."""
    for sid in (sheet_ids.SHEET_ITS_VENDORS, sheet_ids.SHEET_PO_LOG,
                sheet_ids.SHEET_PO_PENDING_REVIEW):
        if sid == 0:
            assert sid not in picklist_validation.REGISTRY
        else:
            assert sid in picklist_validation.REGISTRY


# ---- MULTI_PICKLIST plumbing ----------------------------------------------


def test_validate_cell_list_value_validates_each_element():
    # Uses a live-registered entry (ITS_Errors.Severity) so the test bites while the
    # PO sheet ids are still 0 placeholders.
    validate_cell(sheet_ids.SHEET_ERRORS, "Severity", ["INFO", "WARN"])


def test_validate_cell_list_value_rejects_bad_element():
    with pytest.raises(PicklistViolationError) as exc:
        validate_cell(sheet_ids.SHEET_ERRORS, "Severity", ["INFO", "BOGUS"])
    assert exc.value.value == "BOGUS"


def test_corpus_seed_values_within_registry_sets():
    """The seed can never drift outside its own write-gate: every corpus vendor's
    Region / Supply Categories / Default Terms Profile value is registry-allowed."""
    for vendor in seed.CORPUS_VENDORS:
        assert vendor["Region"] in picklist_validation._VENDOR_REGION_VALUES, vendor
        assert vendor["Default Terms Profile"] in picklist_validation._VENDOR_TERMS_PROFILE_VALUES, vendor
        for cat in vendor["Supply Categories"]:
            assert cat in picklist_validation._VENDOR_SUPPLY_CATEGORY_VALUES, vendor


def test_resolve_cells_builds_multi_picklist_object_value(mocker):
    mocker.patch.object(smartsheet_client, "_column_map",
                        return_value={"Supply Categories": 111, "Vendor Name": 222})
    cells = smartsheet_client._resolve_cells(
        12345, {"Vendor Name": "Chint", "Supply Categories": ["inverters", "modules"]},
    )
    by_col = {c.column_id: c for c in cells}
    multi = by_col[111]
    assert multi.object_value is not None
    assert list(multi.object_value.values) == ["inverters", "modules"]
    plain = by_col[222]
    assert plain.value == "Chint"


# ---- Seed pure helpers -----------------------------------------------------


def test_is_duplicate_name_containment_and_exact():
    assert seed.is_duplicate_name("B2 Sales", "B2 Sales / Zpower")
    assert seed.is_duplicate_name("b2sales", "B2 Sales")
    assert not seed.is_duplicate_name("Chint Power Systems (CPS)", "VSUN Solar USA Inc")
    # short-fragment guard: 2–3-char normalized names never containment-match
    assert not seed.is_duplicate_name("AB", "ABsolutely Different Vendor")


def test_next_key_start_ignores_malformed_keys():
    assert seed.next_key_start([]) == 1
    assert seed.next_key_start(["VEN-000003", "VEN-000010", "garbage", "", "VEN-12"]) == 11
    assert seed.format_key(11) == "VEN-000011"


def test_map_old_db_row_field_mapping_and_provenance():
    row = {
        "Vendor": " Maddox ", "Primary Contact": "Jo Doe", "Email": "jo@maddox.com",
        "Phone": "555-1234", "Specialty / Products": "Transformers",
        "Payment Terms": "Net 30", "Vendor Type": "Material",
        "Preferred Status": "Preferred", "Notes": "Legacy stub.",
    }
    mapped = seed.map_old_db_row(row, "2026-07-09")
    assert mapped["Vendor Name"] == "Maddox"
    assert mapped["Contact Name"] == "Jo Doe"
    assert mapped["Contact Email"] == "jo@maddox.com"
    assert mapped["Contact Phone"] == "555-1234"
    assert mapped["Default Terms Profile"] == "standard_17"
    for fragment in ("Specialty: Transformers", "Payment terms: Net 30", "Type: Material",
                     "Preferred: Preferred", "Legacy stub.", "2026-07-09"):
        assert fragment in mapped["Notes"]
    assert "Region" not in mapped and "Supply Categories" not in mapped


def test_build_seed_rows_dedupes_and_allocates_sequential_keys():
    old_rows = [
        {"Vendor": "B2 Sales / Zpower"},   # dup of corpus B2 Sales → skipped loudly
        {"Vendor": "Maddox"},               # new → seeded
        {"Vendor": ""},                     # blank → skipped
    ]
    to_add, skips = seed.build_seed_rows([], [], old_rows, "2026-07-09")
    names = [r["Vendor Name"] for r in to_add]
    assert names[: len(seed.CORPUS_VENDORS)] == [v["Vendor Name"] for v in seed.CORPUS_VENDORS]
    assert "Maddox" in names
    assert "B2 Sales / Zpower" not in names
    assert any("B2 Sales / Zpower" in s for s in skips)
    assert any("blank vendor name" in s for s in skips)
    # keys sequential from VEN-000001, no gaps, no dupes
    keys = [r["Vendor Key"] for r in to_add]
    assert keys == [seed.format_key(i) for i in range(1, len(to_add) + 1)]
    assert all(r["Active"] == "Active" for r in to_add)


def test_build_seed_rows_idempotent_against_existing_sheet():
    existing_names = [v["Vendor Name"] for v in seed.CORPUS_VENDORS] + ["Maddox"]
    existing_keys = ["VEN-000042"]
    to_add, skips = seed.build_seed_rows(existing_names, existing_keys,
                                         [{"Vendor": "Maddox"}], "2026-07-09")
    assert to_add == []
    assert len(skips) == len(seed.CORPUS_VENDORS) + 1
