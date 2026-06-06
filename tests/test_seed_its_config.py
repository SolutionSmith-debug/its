"""Tests for scripts/seed_its_config.py.

Smartsheet reads/writes are mocked at the module level. These tests cover
classify-and-skip logic and the seed-row build (including the reviewer_chain
JSON round-trip back to the canonical DEFAULT_REVIEWER_CHAINS dict).

Run with: pytest -q tests/test_seed_its_config.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from shared.defaults import DEFAULT_REVIEWER_CHAINS

# scripts/ isn't a package — load seed_its_config by file path.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import seed_its_config  # noqa: E402  (path manipulation must precede import)

# ---- _build_seed_rows ----------------------------------------------------


def test_build_seed_rows_has_expected_entries():
    rows = seed_its_config._build_seed_rows()
    # 7 Handover-v5 rows + 5 F08/F09 rows (4 circuit_breaker.* +
    # alerting.max_alerts_per_hour). The F22 authorized_approvers seed was removed
    # 2026-06-06 (approval authority = ITS — Safety Portal workspace membership).
    assert len(rows) == 12


def test_build_seed_rows_have_expected_columns():
    expected = {"Setting", "Value", "Workstream", "Description"}
    for row in seed_its_config._build_seed_rows():
        assert set(row.keys()) == expected


def test_build_seed_rows_only_uses_valid_workstreams():
    valid = {"global", "safety_reports"}
    for row in seed_its_config._build_seed_rows():
        assert row["Workstream"] in valid


def test_reviewer_chain_value_roundtrips_to_defaults():
    # The Value must be json.dumps(DEFAULT_REVIEWER_CHAINS["safety_reports"], ...).
    # Verify parse → equals canonical dict so emails/delay fields stay in sync.
    rows = seed_its_config._build_seed_rows()
    reviewer_row = next(r for r in rows if r["Setting"] == "safety_reports.reviewer_chain")

    parsed = json.loads(reviewer_row["Value"])
    assert parsed == DEFAULT_REVIEWER_CHAINS["safety_reports"]


# ---- classify ------------------------------------------------------------


def test_classify_empty_sheet_routes_all_to_added():
    seed = seed_its_config._build_seed_rows()
    added, skipped, stale = seed_its_config.classify(seed, [])

    assert len(added) == len(seed)
    assert skipped == []
    assert stale == []


def test_classify_full_sheet_matching_values_routes_all_to_skipped():
    seed = seed_its_config._build_seed_rows()
    existing = [
        {"Setting": s["Setting"], "Value": s["Value"], "Workstream": s["Workstream"]}
        for s in seed
    ]

    added, skipped, stale = seed_its_config.classify(seed, existing)

    assert added == []
    assert len(skipped) == len(seed)
    assert stale == []


def test_classify_one_divergent_value_flagged_stale_not_overwritten():
    seed = seed_its_config._build_seed_rows()
    existing = [
        {"Setting": s["Setting"], "Value": s["Value"], "Workstream": s["Workstream"]}
        for s in seed
    ]
    # Mutate one existing Value so it diverges from the seed.
    for row in existing:
        if row["Setting"] == "system.state":
            row["Value"] = "PAUSED"
            break

    added, skipped, stale = seed_its_config.classify(seed, existing)

    assert added == []
    assert len(skipped) == len(seed) - 1  # one diverged → stale, rest skipped
    assert len(stale) == 1
    stale_seed, existing_value = stale[0]
    assert stale_seed["Setting"] == "system.state"
    assert existing_value == "PAUSED"


def test_classify_match_is_workstream_scoped():
    # Same Setting key on different Workstream is a different row — must not collide.
    seed = [
        {"Setting": "x", "Value": "v1", "Workstream": "global", "Description": ""},
        {"Setting": "x", "Value": "v2", "Workstream": "safety_reports", "Description": ""},
    ]
    existing = [{"Setting": "x", "Value": "v1", "Workstream": "global"}]

    added, skipped, stale = seed_its_config.classify(seed, existing)

    assert [r["Workstream"] for r in added] == ["safety_reports"]
    assert [r["Workstream"] for r in skipped] == ["global"]
    assert stale == []


def test_classify_setting_match_is_case_sensitive():
    seed = [
        {"Setting": "system.state", "Value": "ACTIVE", "Workstream": "global", "Description": ""},
    ]
    existing = [{"Setting": "System.State", "Value": "ACTIVE", "Workstream": "global"}]

    added, _, _ = seed_its_config.classify(seed, existing)
    assert len(added) == 1


# ---- main() integration --------------------------------------------------


def test_main_empty_sheet_prompts_and_writes_on_confirm(mocker):
    mocker.patch.object(seed_its_config.smartsheet_client, "get_rows", return_value=[])
    add_rows = mocker.patch.object(seed_its_config.smartsheet_client, "add_rows")
    mocker.patch("builtins.input", return_value="y")

    seed_its_config.main()

    add_rows.assert_called_once()
    _, called_rows = add_rows.call_args.args
    assert len(called_rows) == len(seed_its_config._build_seed_rows())


def test_main_empty_sheet_aborts_on_decline(mocker):
    mocker.patch.object(seed_its_config.smartsheet_client, "get_rows", return_value=[])
    add_rows = mocker.patch.object(seed_its_config.smartsheet_client, "add_rows")
    mocker.patch("builtins.input", return_value="n")

    with pytest.raises(SystemExit):
        seed_its_config.main()

    add_rows.assert_not_called()


def test_main_fully_seeded_sheet_skips_prompt_and_write(mocker):
    seed = seed_its_config._build_seed_rows()
    existing = [
        {"Setting": s["Setting"], "Value": s["Value"], "Workstream": s["Workstream"]}
        for s in seed
    ]
    mocker.patch.object(seed_its_config.smartsheet_client, "get_rows", return_value=existing)
    add_rows = mocker.patch.object(seed_its_config.smartsheet_client, "add_rows")
    input_mock = mocker.patch("builtins.input")

    seed_its_config.main()

    input_mock.assert_not_called()
    add_rows.assert_not_called()


def test_main_stale_sheet_does_not_overwrite(mocker):
    seed = seed_its_config._build_seed_rows()
    existing = [
        {"Setting": s["Setting"], "Value": s["Value"], "Workstream": s["Workstream"]}
        for s in seed
    ]
    for row in existing:
        if row["Setting"] == "system.state":
            row["Value"] = "PAUSED"
            break
    mocker.patch.object(seed_its_config.smartsheet_client, "get_rows", return_value=existing)
    add_rows = mocker.patch.object(seed_its_config.smartsheet_client, "add_rows")
    input_mock = mocker.patch("builtins.input")

    seed_its_config.main()

    input_mock.assert_not_called()
    add_rows.assert_not_called()
