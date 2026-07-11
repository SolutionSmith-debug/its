"""Subcontract S1 structural tests — builder↔registry parity, the WSR schema-twin
contract, and the log status vocabulary (mirror of tests/test_po_s1_sheets.py).

The S1 controls-that-bite:
  * Builder option lists vs the picklist REGISTRY value sets (the #247→#253 class:
    an option the builder creates but the write-gate lacks blocks the live write
    path with PicklistViolationError — invisible to mocks).
  * The Subcontract_Pending_Review schema-twin contract: identical (title, type,
    systemColumnType) tuples to the WPR builder, and every `wsr_review` COL_*
    protocol title present — this is what lets the shared send engine bind a
    subcontract_review module in S4 without surgery.
  * Subcontract_Log LEGAL_STATUSES == the builder Status options == the registry set,
    and draft/queued are deliberately absent (the ledger row is first written at filing).

All Smartsheet calls mocked — never hits the live API.
"""
from __future__ import annotations

import sys
from pathlib import Path

# sys.path-driven import (scripts/ has no __init__.py) — mirrors tests/test_po_s1_sheets.py.
_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import build_its_subcontractors_sheet as subs_mig  # noqa: E402
import build_po_pending_review_sheet as po_review_mig  # noqa: E402
import build_subcontract_log_sheet as sc_log_mig  # noqa: E402
import build_subcontract_pending_review_sheet as sc_review_mig  # noqa: E402
import build_wpr_human_review_sheet as wpr_mig  # noqa: E402

from safety_reports import wsr_review  # noqa: E402
from shared import picklist_validation, sheet_ids  # noqa: E402
from subcontracts import governing_law, subcontract_log  # noqa: E402

# ---- Builder ↔ REGISTRY option parity -------------------------------------


def test_subcontractors_builder_options_match_registry_sets():
    assert set(subs_mig.STATE_OPTIONS) == picklist_validation._SUBCONTRACTOR_STATE_VALUES
    assert set(subs_mig.TRADE_OPTIONS) == picklist_validation._SUBCONTRACTOR_TRADE_VALUES
    assert set(subs_mig.TERMS_PROFILE_OPTIONS) == picklist_validation._SUBCONTRACTOR_TERMS_PROFILE_VALUES
    assert set(subs_mig.ACTIVE_OPTIONS) == picklist_validation._ACTIVE_LIFECYCLE_VALUES


def test_subcontractor_state_axis_three_way_parity_with_governing_law():
    """The State grouping axis MUST be set-equal across all three surfaces: the sheet builder's
    STATE_OPTIONS, the write-gate's _SUBCONTRACTOR_STATE_VALUES, AND the governing-law resolver's
    _STATE_NAMES keys. A State on the sheet that governing_law rejects would fence EVERY subcontract
    for that subcontractor (render raises GoverningLawError) — so a drift in any one surface is a
    latent contract-blocking bug the mocks can't catch. Locks the multi-surface fan-out."""
    builder_states = set(subs_mig.STATE_OPTIONS)
    gate_states = set(picklist_validation._SUBCONTRACTOR_STATE_VALUES)
    law_states = set(governing_law._STATE_NAMES)
    assert builder_states == gate_states == law_states
    # And every value actually resolves through the public resolver (not just key-equality).
    for st in sorted(builder_states):
        assert governing_law.resolve(st)["governing_law_state_name"]


def test_subcontract_log_builder_status_options_match_registry_and_module():
    assert set(sc_log_mig.STATUS_OPTIONS) == picklist_validation._SUBCONTRACT_LOG_STATUS_VALUES
    # The module's own LEGAL_STATUSES must equal the builder + registry set (three-surface parity).
    assert subcontract_log.LEGAL_STATUSES == picklist_validation._SUBCONTRACT_LOG_STATUS_VALUES


def test_subcontract_log_module_col_titles_match_builder_columns():
    """Every subcontract_log.COL_* constant must be an ACTUAL Subcontract_Log column title — the
    ledger writes rows keyed by these titles through smartsheet_client (a title the sheet lacks
    KeyErrors the FIRST filing). Ops-stds-enforcer caught the 'SC PDF' vs 'Subcontract PDF' fork
    drift here; this test red-lights that class automatically. (Mirror of the WSR-twin title test.)"""
    builder_titles = {c["title"] for c in sc_log_mig.COLUMN_SCHEMA}
    module_titles = {
        v for n, v in vars(subcontract_log).items()
        if n.startswith("COL_") and isinstance(v, str)
    }
    assert module_titles, "no COL_* constants found in subcontract_log — test wiring broke"
    missing = module_titles - builder_titles
    assert not missing, (
        f"subcontract_log COL_* {sorted(missing)} are not real Subcontract_Log columns — the "
        f"builder titles are {sorted(builder_titles)}. A ledger write keyed on a missing title "
        "KeyErrors the first filing."
    )


def test_subcontract_review_builder_options_match_registry_sets():
    assert set(sc_review_mig.SEND_STATUS_OPTIONS) == picklist_validation._SUBCONTRACT_SEND_STATUS_VALUES
    assert set(sc_review_mig.WORKSTREAM_OPTIONS) == picklist_validation._SUBCONTRACT_WORKSTREAM_VALUES


def test_log_status_set_omits_draft_and_queued():
    """The ledger row is first written at filing (status already pending_review), so
    draft/queued are deliberately absent; 'executed' (the wet-signature terminal) is present."""
    for pre_filing in ("draft", "queued"):
        assert pre_filing not in picklist_validation._SUBCONTRACT_LOG_STATUS_VALUES
    assert "executed" in picklist_validation._SUBCONTRACT_LOG_STATUS_VALUES


def test_subcontract_send_status_set_is_the_wsr_sending_inclusive_set():
    """The subcontract review sheet rides the SAME shared send engine — the write-ahead
    SENDING marker contract comes with it, so the Send Status gate must be the WSR set."""
    assert picklist_validation._SUBCONTRACT_SEND_STATUS_VALUES == picklist_validation._WSR_SEND_STATUS_VALUES
    assert "SENDING" in picklist_validation._SUBCONTRACT_SEND_STATUS_VALUES


def test_subcontract_workstream_value_set_is_subcontracts_only():
    """P1b contamination guard vocabulary: exactly {subcontracts} — a safety/progress/po
    tag here must raise at the write gate."""
    assert picklist_validation._SUBCONTRACT_WORKSTREAM_VALUES == frozenset({"subcontracts"})
    for other in ("safety", "progress", "po_materials"):
        assert other not in picklist_validation._SUBCONTRACT_WORKSTREAM_VALUES


# ---- The WSR schema-twin contract (S4 engine bind) ------------------------


def _shape(schema: list[dict]) -> list[tuple]:
    return [(c["title"], c["type"], c.get("systemColumnType")) for c in schema]


def test_subcontract_review_schema_is_exact_structural_twin_of_wpr():
    """(title, type, systemColumnType) tuples identical, in order, to the WPR builder —
    options/descriptions differ by design (Workstream tag, subcontract semantics),
    structure may not. This is the strongest static guarantee the S4 SendConfig bind holds."""
    assert _shape(sc_review_mig.COLUMN_SCHEMA) == _shape(wpr_mig.COLUMN_SCHEMA)


def test_subcontract_review_is_structural_twin_of_po_review_too():
    """PO_Pending_Review is itself a WSR twin; the subcontract review sheet must match it
    structurally as well (defense-in-depth against a drift in either fork)."""
    assert _shape(sc_review_mig.COLUMN_SCHEMA) == _shape(po_review_mig.COLUMN_SCHEMA)


def test_subcontract_review_schema_carries_every_wsr_protocol_title():
    """Every wsr_review COL_* constant must be a column title here — the send engine reads
    rows by these exact titles (Sub Key rides in 'Job ID', the Subcontract date in 'Week Of',
    the Subcontract PDF in 'Compiled PDF'; S1 protocol-slot contract)."""
    protocol_titles = {
        v for n, v in vars(wsr_review).items()
        if n.startswith("COL_") and isinstance(v, str)
    }
    assert protocol_titles, "no COL_* constants found in wsr_review — test wiring broke"
    builder_titles = {c["title"] for c in sc_review_mig.COLUMN_SCHEMA}
    missing = protocol_titles - builder_titles
    assert not missing, (
        f"Subcontract_Pending_Review builder is missing protocol column(s) {sorted(missing)} — "
        "the shared send engine binds by these titles (S1 schema-twin contract)."
    )


def test_subcontract_review_datetime_columns_are_date_not_abstract_datetime():
    """ABSTRACT_DATETIME is not API-creatable (errorCode 1142 — the live-verified WPR lesson).
    Regression pin: the twin must ship DATE."""
    types = {c["title"]: c["type"] for c in sc_review_mig.COLUMN_SCHEMA}
    assert types["Approved At"] == "DATE"
    assert types["Sent At"] == "DATE"


def test_subcontract_registry_entries_conditional_on_real_sheet_ids():
    """Placeholder-0 guard: no REGISTRY entry until the operator flips the real ids post-build."""
    for sid in (sheet_ids.SHEET_ITS_SUBCONTRACTORS, sheet_ids.SHEET_SUBCONTRACT_LOG,
                sheet_ids.SHEET_SUBCONTRACT_PENDING_REVIEW):
        if sid == 0:
            assert sid not in picklist_validation.REGISTRY
        else:
            assert sid in picklist_validation.REGISTRY
