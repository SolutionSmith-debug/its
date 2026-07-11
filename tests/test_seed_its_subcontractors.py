"""Pure-logic tests for the ITS_Subcontractors seeder (SC-S2) — roster load, SUB- key minting,
dedup, and the roster→upsert payload adapter. No Smartsheet (main() is operator-run)."""
from __future__ import annotations

import sys
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "migrations"
if str(_MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_MIGRATIONS_DIR))

import seed_its_subcontractors as seed  # noqa: E402


def test_roster_loads_the_corpus_firms():
    roster = seed.load_roster()
    assert len(roster) >= 20  # ~24 firms extracted from the corpus
    names = {f["name"] for f in roster}
    assert "D.E.L. Electric OR, Inc." in names
    assert "BG Wing LLC" in names  # the doubled-suffix bug was collapsed at extraction


def test_key_minting_starts_past_the_highest_existing():
    assert seed.format_key(1) == "SUB-000001"
    assert seed.next_key_start([]) == 1
    assert seed.next_key_start(["SUB-000003", "SUB-000050", "SUB-000012"]) == 51


def test_is_duplicate_name_normalizes():
    assert seed.is_duplicate_name("Peerless Fence", "  peerless   fence ")
    assert seed.is_duplicate_name("Austin Engineering Co., Inc.", "AUSTIN ENGINEERING CO., INC.")
    assert not seed.is_duplicate_name("Legacy Paving & Construction LLC", "Legacy Solar Systems")


def test_build_seed_payloads_fresh_mints_sequential_keys_and_maps_fields():
    roster = seed.load_roster()
    payloads, skips = seed.build_seed_payloads([], [], roster, "2026-07-11")
    assert len(payloads) == len(roster)  # nothing deduped on an empty sheet
    assert [p["sub_key"] for p in payloads][:3] == ["SUB-000001", "SUB-000002", "SUB-000003"]
    first = payloads[0]
    assert first["sub_name"] == roster[0]["name"]
    assert first["active"] == 1
    assert isinstance(first["trades"], list)
    # every trade seeded is one of the canonical slots (parity with the builder / registry)
    from shared import picklist_validation
    for p in payloads:
        for t in p["trades"]:
            assert t in picklist_validation._SUBCONTRACTOR_TRADE_VALUES, t


def test_build_seed_payloads_dedups_against_existing_and_within_batch():
    roster = seed.load_roster()
    existing = ["Peerless Fence"]  # already on the sheet
    payloads, skips = seed.build_seed_payloads(existing, ["SUB-000009"], roster, "2026-07-11")
    seeded_names = {p["sub_name"] for p in payloads}
    assert "Peerless Fence" not in seeded_names  # deduped out — operator edits never overwritten
    assert any("Peerless Fence" in s for s in skips)
    assert [p["sub_key"] for p in payloads][0] == "SUB-000010"  # keys start past the existing max
