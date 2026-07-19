"""System map (`/system`): registry parity teeth + route behavior.

The registry-parity tests are the anti-drift contract (HOUSE_REFLEXES §1
reconcile-every-registry): a NEW daemon (plist / TRACKED_JOBS marker) must get
a `system_map` node in the same PR, or these fail naming the missing piece.
Route tests are hermetic — every live join (Smartsheet, launchctl, heartbeat
files, the troubleshooting tree) is monkeypatched or verified fail-soft.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from operator_dashboard import system_view
from operator_dashboard.app import create_app
from operator_dashboard.system_map import (
    BANDS,
    EDGES,
    LANES,
    NODE_BY_ERROR_SCRIPT,
    NODE_BY_LAUNCHD_LABEL,
    NODES,
    NODES_BY_ID,
)

_REPO = Path(__file__).resolve().parent.parent


# ── registry shape ───────────────────────────────────────────────────────────

def test_node_ids_unique_and_lanes_bands_valid() -> None:
    ids = [n.id for n in NODES]
    assert len(ids) == len(set(ids)), "duplicate node id"
    lanes = {lane_id for lane_id, _ in LANES}
    bands = {band_id for band_id, _ in BANDS}
    for n in NODES:
        assert n.lane in lanes, f"{n.id}: unknown lane {n.lane}"
        assert n.band in bands, f"{n.id}: unknown band {n.band}"
        assert n.blurb, f"{n.id}: empty blurb"


def test_edges_reference_real_nodes() -> None:
    for e in EDGES:
        assert e.src in NODES_BY_ID, f"edge src {e.src!r} has no node"
        assert e.dst in NODES_BY_ID, f"edge dst {e.dst!r} has no node"
        assert e.kind in ("push", "pull", "write", "read", "trigger", "send", "alert")


def test_send_edges_originate_only_from_send_half_nodes() -> None:
    # The map must never draw a transmission edge out of a generation-half node —
    # that would misrepresent the External Send Gate (Invariant 1).
    for e in EDGES:
        if e.kind == "send":
            assert NODES_BY_ID[e.src].send_half == "send", (
                f"send edge from non-send node {e.src}"
            )


# ── parity teeth (a new daemon must land here too) ───────────────────────────

def test_every_launchd_plist_label_has_a_node() -> None:
    plists = sorted(
        p.stem for p in (_REPO / "scripts" / "launchd").glob("org.solutionsmith.its.*.plist")
    )
    assert plists, "no plists found — repo layout changed?"
    missing = [lbl for lbl in plists if lbl not in NODE_BY_LAUNCHD_LABEL]
    assert not missing, (
        f"launchd labels with no system-map node: {missing} — add a node to "
        "operator_dashboard/system_map.py (registry reconciliation, HOUSE_REFLEXES §1)"
    )


def test_every_tracked_job_marker_has_a_node() -> None:
    # scripts/ is not a package — the repo's sys.path-insert idiom imports the
    # watchdog as top-level `watchdog` (a `from scripts import …` makes mypy see
    # the file under two module names; see tests/test_troubleshooting_tree.py).
    scripts_dir = _REPO / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from watchdog import TRACKED_JOBS

    claimed = {n.marker for n in NODES if n.marker}
    missing = [m for m in TRACKED_JOBS if m not in claimed]
    assert not missing, (
        f"TRACKED_JOBS markers with no system-map node: {missing} — add a node (or a "
        "marker= join) in operator_dashboard/system_map.py"
    )


def test_every_send_half_node_has_an_inbound_human_approval_edge() -> None:
    """The Send Gate must be DRAWN, not merely implied.

    Invariant 1 says nothing transmits without a human approving that exact
    packet. On the map that contract IS the `port="human approval"` edge from a
    review sheet into the send-half node. A send node without one renders as a
    transmitter fed by nothing a human touched — the map would misrepresent the
    gate. (rfq_send shipped with only its ITS_Vendors recipient-lookup edge.)
    """
    approved = {e.dst for e in EDGES if e.port == "human approval"}
    missing = sorted(n.id for n in NODES if n.send_half == "send" and n.id not in approved)
    assert not missing, (
        f"send-half nodes with no inbound human-approval edge: {missing} — add the "
        'MapEdge(<review sheet>, <node>, "APPROVED rows only — F22", "read", '
        'port="human approval") so the map draws the External Send Gate crossing'
    )


def test_every_node_config_gate_is_reachable_in_the_config_editor() -> None:
    """A gate the map advertises must be a gate the operator can actually reach.

    Every `config_gate` / `extra_gates` key on a node is an ITS_Config row the
    operator is told controls that capability. If it is absent from the Class-A
    ACT registry the editor refuses to write it, so the map points at a switch
    that does not exist on the console — the "phantom gate" failure mode.
    Deliberate omissions go in gate_not_editable with a stated reason.
    """
    from operator_dashboard.act.registry import REGISTRY
    from operator_dashboard.system_map import gate_workstream

    # Gates deliberately NOT editable from the dashboard (each needs a reason).
    # These are surfaced READ-ONLY instead (registry.CLASS_E_DISPLAY), which the
    # companion test below asserts — "not editable" must never mean "invisible".
    gate_not_editable: dict[str, str] = {
        "po_materials.estimate_extract.tier1_enabled": "ADR-0004 extraction ladder — dark + unvalidated",
        "po_materials.estimate_extract.tier2_enabled": "ADR-0004 extraction ladder — dark + unvalidated",
        "po_materials.estimate_extract.ocr_enabled": "ADR-0004 extraction ladder — dark + unvalidated",
    }

    missing: list[str] = []
    for n in NODES:
        for gate in ([n.config_gate] if n.config_gate else []) + list(n.extra_gates):
            if gate in gate_not_editable:
                continue
            if (gate, gate_workstream(gate)) not in REGISTRY:
                missing.append(f"{n.id} -> {gate}")
    assert not missing, (
        f"system-map gates absent from the ACT config registry: {sorted(missing)} — add a "
        "ConfigEntry in operator_dashboard/act/registry.py (registry reconciliation, "
        "HOUSE_REFLEXES §1), or record a reason in gate_not_editable"
    )


def test_uneditable_node_gates_are_still_visible_read_only() -> None:
    """"Not editable" must never degrade into "invisible".

    A gate withheld from the config editor (an unvalidated AI extraction tier) is
    still state the operator must be able to READ — otherwise the console silently
    hides a capability switch. Every such gate has to appear in CLASS_E_DISPLAY,
    which renders the LIVE value with no edit control.
    """
    from operator_dashboard.act.registry import CLASS_E_DISPLAY

    displayed = {d.setting for d in CLASS_E_DISPLAY}
    ladder = [
        "po_materials.estimate_extract.tier1_enabled",
        "po_materials.estimate_extract.tier2_enabled",
        "po_materials.estimate_extract.ocr_enabled",
    ]
    missing = [g for g in ladder if g not in displayed]
    assert not missing, (
        f"gates withheld from the editor but not surfaced read-only: {missing} — add a "
        "DisplayEntry to CLASS_E_DISPLAY in operator_dashboard/act/registry.py"
    )
    # and they must NOT have leaked into the editable registry
    from operator_dashboard.act.registry import REGISTRY

    leaked = [g for g in ladder if (g, "po_materials") in REGISTRY]
    assert not leaked, (
        f"unvalidated extraction tiers became editable: {leaked} — promoting one is gated "
        "on scripts/eval_estimate_ladder.py qualifying a model on the production corpus"
    )


def test_send_queue_panel_covers_every_review_sheet_feeding_a_send_node() -> None:
    """The send-queue panel must show EVERY lane's approval backlog.

    Its sheet list is hardcoded; a new send lane whose review sheet is omitted
    makes that lane's PENDING / HELD / FAILED rows invisible on the console while
    the panel still reads "all clear" — a silent blind spot, not an obvious gap.
    """
    import operator_dashboard.sources.smartsheet_panels as sp
    from shared import sheet_ids

    covered = {getattr(sheet_ids, attr, None) for _ws, attr in sp._SEND_QUEUE_SHEETS}
    missing = sorted(
        f"{NODES_BY_ID[e.src].label} (feeds {e.dst})"
        for e in EDGES
        if e.port == "human approval" and NODES_BY_ID[e.src].sheet_id not in covered
    )
    assert not missing, (
        f"review sheets feeding a send node but absent from the send-queue panel: {missing} "
        "— add them to _SEND_QUEUE_SHEETS in operator_dashboard/sources/smartsheet_panels.py"
    )


def test_runbook_paths_exist() -> None:
    for n in NODES:
        if n.runbook:
            assert (_REPO / n.runbook).is_file(), f"{n.id}: runbook {n.runbook} missing"


def test_script_paths_exist() -> None:
    for n in NODES:
        if n.script_path:
            assert (_REPO / n.script_path).exists(), f"{n.id}: script_path {n.script_path} missing"


def test_error_script_join_covers_the_known_identities() -> None:
    # Spot-lock the join keys the error panels rely on, including the two
    # UNDOTTED outliers (publish_daemon / config_actuator).
    for script, node in {
        "safety_reports.portal_poll": "portal_poll",
        "safety_reports.weekly_send_poll": "weekly_send",
        "po_materials.po_poll": "po_poll",
        "subcontracts.subcontract_send": "subcontract_send",
        "publish_daemon": "publish_daemon",
        "config_actuator": "config_actuator",
        "scripts.watchdog": "watchdog",
        "field_ops.fieldops_sync": "fieldops_sync",
    }.items():
        assert NODE_BY_ERROR_SCRIPT.get(script) == node


# ── routes (hermetic) ────────────────────────────────────────────────────────

@pytest.fixture
def offline_joins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every live join returns empty — the map must render fully regardless."""
    monkeypatch.setattr(system_view, "_open_criticals_by_node", lambda: {})
    monkeypatch.setattr(system_view, "_launchd_state_by_node", lambda: {})
    monkeypatch.setattr(system_view, "_heartbeat_age_by_node", lambda: {})
    monkeypatch.setattr(system_view, "_gate_state_by_node", lambda: {})
    monkeypatch.setattr(system_view, "_troubleshoot_joins", lambda: {})


def test_system_page_renders_every_node(offline_joins: None) -> None:
    client = TestClient(create_app())
    r = client.get("/system")
    assert r.status_code == 200
    for n in NODES:
        assert f'id="node-{n.id}"' in r.text, f"node {n.id} not rendered"
    # The two walls are structural — they must always be present.
    assert 'id="wall-send"' in r.text
    assert 'id="wall-ingress"' in r.text


def test_system_focus_param_marks_node(offline_joins: None) -> None:
    client = TestClient(create_app())
    r = client.get("/system?focus=portal_poll")
    assert r.status_code == 200
    assert 'data-focus="portal_poll"' in r.text
    # An unknown focus is dropped, never echoed back into the page.
    r = client.get("/system?focus=<script>alert(1)</script>")
    assert r.status_code == 200
    assert 'data-focus=""' in r.text


def test_node_rail_renders_and_unknown_is_soft(offline_joins: None) -> None:
    client = TestClient(create_app())
    r = client.get("/system/node/weekly_send")
    assert r.status_code == 200
    assert "send half — no AI" in r.text
    assert "/doc/runbooks/safety_weekly_send.md" in r.text
    r = client.get("/system/node/nope")
    assert r.status_code == 200
    assert "unknown node" in r.text


def test_live_join_failure_degrades_to_plain_map(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every join helper swallows its own failure — force the underlying imports
    # to explode and assert the page still renders (fail-soft contract).
    import builtins

    real_import = builtins.__import__

    def bomb(name: str, *a: Any, **k: Any) -> Any:
        if name.startswith("shared.") or name == "troubleshooting.loader":
            raise RuntimeError("offline")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", bomb)
    client = TestClient(create_app())
    r = client.get("/system")
    assert r.status_code == 200
    assert 'id="wall-send"' in r.text


def test_open_criticals_rows_carry_map_links(monkeypatch: pytest.MonkeyPatch) -> None:
    import operator_dashboard.sources.smartsheet_panels as sp

    monkeypatch.setattr(
        sp,
        "_cached_error_rows",
        lambda: [
            {"_row_id": 1, "Timestamp": "2026-07-18", "Severity": "CRITICAL",
             "Script": "po_materials.po_poll", "Error": "uncaught_exception"},
            {"_row_id": 2, "Timestamp": "2026-07-18", "Severity": "CRITICAL",
             "Script": "some.retired_thing", "Error": "x"},
        ],
    )
    result = sp.OpenCriticalsSource().fetch()
    by_script = {r["Script"]: r for r in result.rows}
    assert by_script["po_materials.po_poll"]["_link_Script"] == "/system?focus=po_poll"
    assert "_link_Script" not in by_script["some.retired_thing"]  # no node → plain text


def test_troubleshoot_deep_link_renders_expanded() -> None:
    client = TestClient(create_app())
    r = client.get("/troubleshoot?wf=safety_report")
    assert r.status_code == 200
    assert "ts-workflow-open" in r.text  # expanded, not collapsed cards
    r = client.get("/troubleshoot?wf=not_a_workflow")
    assert r.status_code == 200
    assert "Nothing matches" in r.text


def test_pulse_renders_chips(monkeypatch: pytest.MonkeyPatch) -> None:
    # Panels themselves are fail-soft, so /pulse renders chips even offline.
    client = TestClient(create_app())
    r = client.get("/pulse")
    assert r.status_code == 200
    for name in ("daemons", "watchdog", "breaker", "criticals", "review", "sends"):
        assert name in r.text
