"""Smoke tests: the mechanical doctrine-drift checker runs + parses the manifest.

Validates that `scripts/check_doctrine_drift.py` consumes
`docs/doctrine_manifest.yaml` and produces well-formed findings against the real
repo. Does NOT assert a finding count — the checker reports whatever drift exists
(its job); this only locks the contract (exit 0, parseable shape).
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_runs_and_emits_json():
    r = subprocess.run(
        [sys.executable, "-m", "scripts.check_doctrine_drift", "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert isinstance(data, list)
    for f in data:
        assert set(f) >= {"check", "severity", "location", "detail"}
        assert f["check"] in {"M1", "M2", "M3", "M4", "M5", "M6"}
        assert f["severity"] in {"drift", "coverage", "clean"}


def test_human_output_runs():
    r = subprocess.run(
        [sys.executable, "-m", "scripts.check_doctrine_drift"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "PROPOSE-ONLY" in r.stdout
    assert "DRIFT" in r.stdout


def test_sheet_ids_are_clean():
    # The two canonical sheet IDs in shared/sheet_ids.py must match the manifest
    # (this is verified-clean state; a failure here is real M4 drift).
    r = subprocess.run(
        [sys.executable, "-m", "scripts.check_doctrine_drift", "--json"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    data = json.loads(r.stdout)
    assert [f for f in data if f["check"] == "M4"] == [], "unexpected sheet-ID drift (M4)"
