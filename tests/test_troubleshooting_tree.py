"""Coverage tests for the troubleshooting tree — THESE ARE THE COMPLETION METER.

Each test extracts a LIVE floor from the codebase (daemon plists, watchdog CHECKS registry,
runbook files, the HELD send-status vocabulary) and asserts ``docs/troubleshooting/tree.yaml``
covers it. A new daemon / watchdog check / runbook that the tree does not yet cover RED-lights
here — by design, so the tree stays honest as the system grows.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# scripts/ is not a Python package; use the repo's sys.path-insert idiom (see
# tests/test_docs_pdf.py) so the modules import as top-level `watchdog` /
# `build_troubleshooting_guide` — a `from scripts import …` makes mypy see the file
# under two module names ("watchdog" and "scripts.watchdog").
_SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import watchdog  # noqa: E402 — sys.path-driven import
from build_runbook_xrefs import main as xref_main  # noqa: E402
from build_troubleshooting_guide import GUIDE_PATH, render_guide  # noqa: E402

from troubleshooting.loader import CLASSES, Tree, load_tree  # noqa: E402

LAUNCHD_DIR = REPO_ROOT / "scripts" / "launchd"
RUNBOOK_DIR = REPO_ROOT / "docs" / "runbooks"

# The HELD send-status vocabulary (Invariant-1 send gate). This is the documented floor; each
# value is also asserted to still exist in the send-path code by test_held_states_still_in_code,
# so a rename surfaces as a currency failure rather than silently dropping coverage.
HELD_STATES = (
    "held_no_recipient",
    "held_missing_pdf",
    "held_missing_envelope",
    "held_oversized_packet",
    "held_workstream_mismatch",
    "held_failed",
)


@pytest.fixture(scope="module")
def tree() -> Tree:
    return load_tree()


def _daemon_labels() -> set[str]:
    """The stable daemon keys = the launchd plist labels (minus org.solutionsmith.its. prefix)."""
    return {
        p.name.removeprefix("org.solutionsmith.its.").removesuffix(".plist")
        for p in LAUNCHD_DIR.glob("org.solutionsmith.its.*.plist")
    }


def _runbook_files() -> set[str]:
    return {p.name for p in RUNBOOK_DIR.glob("*.md") if p.name != "README.md"}


def _live_watchdog_checks() -> set[str]:
    return {c.__name__ for c in watchdog.CHECKS}


# ── the tree loads + is internally valid ─────────────────────────────────────────────────
def test_tree_loads_and_validates(tree: Tree) -> None:
    assert tree.workflows, "tree has no workflows"
    # Every failure mode's class is a known value (the loader enforces this; assert explicitly).
    for fm in tree.all_failure_modes():
        assert fm.cls in CLASSES


def test_every_step_has_failure_modes_or_reason(tree: Tree) -> None:
    for wf in tree.workflows:
        for st in wf.steps:
            assert st.failure_modes or st.no_failure_modes, (
                f"{wf.id}.{st.id}: no failure_modes and no no_failure_modes reason"
            )


# ── coverage floors ──────────────────────────────────────────────────────────────────────
def test_every_daemon_appears_in_a_step(tree: Tree) -> None:
    """Every launchd daemon appears as a `what_happens.daemon` in ≥1 step."""
    labels = _daemon_labels()
    covered = tree.referenced_daemons()
    missing = labels - covered
    assert not missing, f"daemons not covered by any tree step: {sorted(missing)}"
    # No phantom daemons referenced that don't exist as a plist.
    phantom = covered - labels
    assert not phantom, f"tree references non-existent daemon(s): {sorted(phantom)}"


def test_every_held_state_appears_in_a_failure_node(tree: Tree) -> None:
    """Every HELD send-status word appears in ≥1 failure mode (signals or symptom)."""
    blob = "\n".join(
        " ".join([*fm.signals, fm.symptom]) for fm in tree.all_failure_modes()
    )
    missing = [h for h in HELD_STATES if h not in blob]
    assert not missing, f"HELD states not referenced by any failure node: {missing}"


def test_every_watchdog_check_referenced(tree: Tree) -> None:
    """Every live watchdog CHECKS function is referenced by ≥1 failure node's watchdog_check."""
    live = _live_watchdog_checks()
    covered = tree.referenced_watchdog_checks()
    missing = live - covered
    assert not missing, f"watchdog checks not referenced by any failure node: {sorted(missing)}"
    phantom = covered - live
    assert not phantom, f"tree references non-existent watchdog check(s): {sorted(phantom)}"


def test_every_runbook_link_resolves(tree: Tree) -> None:
    """Every `runbook:` path in the tree points at a real file (no dead links)."""
    dead = [rb for rb in tree.referenced_runbooks() if not (REPO_ROOT / rb).is_file()]
    assert not dead, f"tree links to non-existent runbook file(s): {dead}"


def test_every_runbook_referenced_or_exempt(tree: Tree) -> None:
    """Every runbook file is referenced by ≥1 node OR explicitly exempted in the tree header."""
    files = _runbook_files()
    referenced = {Path(rb).name for rb in tree.referenced_runbooks()}
    exempt = set(tree.runbook_exemptions)
    # exemptions must name real files (no stale exemptions)
    stale_exempt = exempt - files
    assert not stale_exempt, f"runbook_exemptions names non-existent file(s): {sorted(stale_exempt)}"
    uncovered = files - referenced - exempt
    assert not uncovered, (
        f"runbook files neither referenced by a node nor exempted: {sorted(uncovered)}"
    )


def test_runbook_anchors_exist(tree: Tree) -> None:
    """Where a runbook link carries an #anchor, the target heading exists in that runbook."""
    problems: list[str] = []
    for fm in tree.all_failure_modes():
        if not fm.runbook or "#" not in fm.runbook:
            continue
        path, anchor = fm.runbook.split("#", 1)
        f = REPO_ROOT / path
        if not f.is_file():
            continue  # covered by test_every_runbook_link_resolves
        text = f.read_text(encoding="utf-8")
        slugs = {_slugify(h) for h in re.findall(r"^#+\s+(.*)$", text, re.MULTILINE)}
        if anchor not in slugs:
            problems.append(f"{fm.runbook} (anchor {anchor!r} not a heading)")
    assert not problems, f"dead runbook anchors: {problems}"


def _slugify(heading: str) -> str:
    s = heading.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"[\s]+", "-", s)


# ── the generated guide is deterministic + current ──────────────────────────────────────
def test_guide_generation_is_deterministic(tree: Tree) -> None:
    """render_guide is a pure function of the tree — run-twice-identical."""
    assert render_guide(tree) == render_guide(tree)


def test_committed_guide_is_current(tree: Tree) -> None:
    """The committed troubleshooting_guide.md equals a fresh render of tree.yaml. Editing the
    tree without regenerating the guide RED-lights here (the currency teeth)."""
    committed = GUIDE_PATH.read_text(encoding="utf-8") if GUIDE_PATH.is_file() else ""
    assert committed == render_guide(tree), (
        "troubleshooting_guide.md is stale — regenerate with "
        "`python -m scripts.build_troubleshooting_guide` and re-record its manifest sha256"
    )


def test_runbook_xrefs_are_current() -> None:
    """Every tree-referenced runbook carries a current cross-link block. Editing the tree (which
    changes a node title/symptom or a daemon) without re-running the generator RED-lights here."""
    assert xref_main(["--check"]) == 0, (
        "runbook xref blocks are stale — run `python -m scripts.build_runbook_xrefs`"
    )


# ── drift guard: the HELD vocabulary is still present in the send-path code ───────────────
def test_held_states_still_in_code() -> None:
    """Each HELD state string still appears somewhere under the send modules — a rename here
    means the tree's coverage target drifted and must be updated (currency signal)."""
    send_files = [
        REPO_ROOT / "safety_reports" / "weekly_send.py",
        REPO_ROOT / "po_materials" / "po_send.py",
    ]
    blob = "\n".join(f.read_text(encoding="utf-8") for f in send_files if f.is_file())
    missing = [h for h in HELD_STATES if h not in blob]
    assert not missing, f"HELD state(s) no longer found in send-path code (drift): {missing}"
