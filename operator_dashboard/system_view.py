"""The system map (`/system`) — the live machine-room schematic. READ-ONLY.

Renders the `system_map` registry as trust-gradient lanes with two walls
(untrusted ingress · External Send Gate) and joins each node to its LIVE
state: open CRITICALs (by ITS_Errors Script), launchd loaded/running state,
heartbeat liveness age, and the ITS_Config gate value ("dark" when off).

Every join is fail-soft: an unreachable source degrades that decoration to
absent — the map itself always renders. Deep links: `/system?focus=<node>`
(from error rows, panels, troubleshoot) and `/system?wf=<workflow>` (from a
troubleshooting-tree card — highlights that workflow's daemons). The detail
rail is one htmx GET per node, `/system/node/{id}`.
"""
from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.templating import Jinja2Templates

from operator_dashboard.cache import cached
from operator_dashboard.config import SMARTSHEET_TTL_SECONDS
from operator_dashboard.sources.base import clean, fmt_timedelta
from operator_dashboard.system_map import (
    BANDS,
    EDGES,
    LANES,
    NODE_BY_LAUNCHD_LABEL,
    NODES,
    NODES_BY_ID,
    WALL_AFTER,
    MapNode,
    edges_for,
    gate_workstream,
)

_FALSY_GATE_VALUES = {"", "false", "0", "no", "off"}


# ── live joins (each fail-soft, each cheap or TTL-cached) ────────────────────

def _open_criticals_by_node() -> dict[str, int]:
    """node id -> open-CRITICAL count, via the SAME cached ITS_Errors read the
    panels use (no extra Smartsheet call) and the canonical terminality
    predicate."""
    try:
        sp: Any = importlib.import_module("operator_dashboard.sources.smartsheet_panels")
        er: Any = importlib.import_module("shared.errors_rotation")
        counts: dict[str, int] = {}
        for row in sp._cached_error_rows():
            if er.errors_row_is_terminal(row):
                continue
            script = str(row.get("Script") or "").strip()
            from operator_dashboard.system_map import NODE_BY_ERROR_SCRIPT

            node_id = NODE_BY_ERROR_SCRIPT.get(script)
            if node_id:
                counts[node_id] = counts.get(node_id, 0) + 1
        return counts
    except Exception:
        return {}


def _launchd_state_by_node() -> dict[str, str]:
    """node id -> 'running' | 'idle' | 'exited N' | 'NOT LOADED' (from launchctl)."""
    try:
        from operator_dashboard.sources.daemons import DaemonStatusSource

        src = DaemonStatusSource()
        live = src._launchctl_table()
        states: dict[str, str] = {}
        for label, node_id in NODE_BY_LAUNCHD_LABEL.items():
            if label not in live:
                states[node_id] = "NOT LOADED"
                continue
            pid, status = live[label]
            if pid not in ("-", ""):
                states[node_id] = "running"
            elif status != "0":
                states[node_id] = f"exited {status}"
            else:
                states[node_id] = "idle"
        return states
    except Exception:
        return {}


def _heartbeat_age_by_node() -> dict[str, str]:
    """node id -> compact liveness age (from state/<stem>_heartbeat.txt)."""
    try:
        hb: Any = importlib.import_module("shared.heartbeat")
        now = datetime.now(UTC)
        ages: dict[str, str] = {}
        for node in NODES:
            if not node.heartbeat_stem:
                continue
            path = hb.STATE_DIR / f"{node.heartbeat_stem}_heartbeat.txt"
            try:
                ts = datetime.fromisoformat(path.read_text().strip())
            except (OSError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            ages[node.id] = fmt_timedelta(now - ts)
        return ages
    except Exception:
        return {}


def _cached_config_values() -> dict[tuple[str, str], str]:
    """(Setting, Workstream) -> Value for the whole ITS_Config sheet, TTL-cached."""

    def _load() -> dict[tuple[str, str], str]:
        ss: Any = importlib.import_module("shared.smartsheet_client")
        sid: Any = importlib.import_module("shared.sheet_ids")
        out: dict[tuple[str, str], str] = {}
        for row in ss.get_rows(sid.SHEET_CONFIG):
            setting = str(row.get("Setting") or "").strip()
            ws = str(row.get("Workstream") or "").strip()
            if setting:
                out[(setting, ws)] = str(row.get("Value") or "").strip()
        return out

    return cached("its_config_values", SMARTSHEET_TTL_SECONDS, _load)


def _gate_state_by_node() -> dict[str, str]:
    """node id -> 'dark' | 'on' for nodes with a config gate ('' when unreadable —
    the map never guesses)."""
    try:
        values = _cached_config_values()
    except Exception:
        return {}
    states: dict[str, str] = {}
    for node in NODES:
        if not node.config_gate:
            continue
        value = values.get((node.config_gate, gate_workstream(node.config_gate)))
        if value is None:
            # No row at all: a dark-shipped gate with no seeded row reads dark.
            states[node.id] = "dark"
        else:
            states[node.id] = "dark" if value.lower() in _FALSY_GATE_VALUES else "on"
    return states


def _troubleshoot_joins() -> dict[str, list[dict[str, str]]]:
    """node id -> troubleshooting-tree steps that name this daemon (the tree's
    daemon vocabulary is the launchd-label suffix). Fail-soft: {} on any error."""
    try:
        from troubleshooting.loader import load_tree

        tree = load_tree()
    except Exception:
        return {}
    prefix = "org.solutionsmith.its."
    joins: dict[str, list[dict[str, str]]] = {}
    for wf in tree.workflows:
        for step in wf.steps:
            daemon = step.what_happens.daemon
            if not daemon:
                continue
            node_id = NODE_BY_LAUNCHD_LABEL.get(prefix + daemon)
            if not node_id:
                continue
            joins.setdefault(node_id, []).append(
                {"wf": wf.id, "wf_title": wf.title, "step": step.id, "step_title": step.title}
            )
    return joins


def _workflow_node_ids(workflow_id: str) -> set[str]:
    """Node ids named by a troubleshooting workflow (for ?wf= highlighting)."""
    highlight: set[str] = set()
    for node_id, steps in _troubleshoot_joins().items():
        if any(s["wf"] == workflow_id for s in steps):
            highlight.add(node_id)
    return highlight


# ── layout assembly ──────────────────────────────────────────────────────────

_BAND_INDEX = {band_id: i + 1 for i, (band_id, _) in enumerate(BANDS)}
# Grid columns: lane columns interleaved with the two walls.
_LANE_COL = {"field": 1, "cloud": 2, "generation": 4, "records": 5, "send": 7, "outside": 8}
_WALL_COL = {"wall-ingress": 3, "wall-send": 6}


def _grid_cells() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(cells, spanners): span-1 nodes grouped per (lane, band) cell; tall nodes
    as their own grid items."""
    grouped: dict[tuple[str, str], list[MapNode]] = {}
    spanners: list[dict[str, Any]] = []
    for node in NODES:
        if node.band_span > 1:
            spanners.append(
                {
                    "node": node,
                    "col": _LANE_COL[node.lane],
                    "row": _BAND_INDEX[node.band],
                    "row_end": _BAND_INDEX[node.band] + node.band_span,
                }
            )
        else:
            grouped.setdefault((node.lane, node.band), []).append(node)
    cells = [
        {"col": _LANE_COL[lane], "row": _BAND_INDEX[band], "nodes": nodes}
        for (lane, band), nodes in grouped.items()
    ]
    return cells, spanners


def _edges_payload() -> str:
    """The edge list as JSON for the client-side SVG underlay.

    Rendered with `|safe` inside a <script type="application/json"> block (a
    browser does NOT entity-decode script content, so Jinja's autoescape would
    corrupt the JSON). Safe because every value comes from the static registry —
    no request or Smartsheet data — and `<` is escaped anyway so the payload can
    never close its own script tag."""
    payload = json.dumps(
        [
            {"src": e.src, "dst": e.dst, "label": e.label, "kind": e.kind, "port": e.port}
            for e in EDGES
        ]
    )
    return payload.replace("<", "\\u003c")


# ── routes ───────────────────────────────────────────────────────────────────

def register_system_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    @app.get("/system")
    def system(request: Request, focus: str = "", wf: str = "") -> Response:
        cells, spanners = _grid_cells()
        focus_id = focus if focus in NODES_BY_ID else ""
        highlight = _workflow_node_ids(wf) if wf else set()
        return templates.TemplateResponse(
            request,
            "system.html",
            {
                "lanes": LANES,
                "walls": WALL_AFTER,
                "bands": BANDS,
                "cells": cells,
                "spanners": spanners,
                "edges_json": _edges_payload(),
                "focus": focus_id,
                "wf": wf,
                "highlight": highlight,
                "criticals": _open_criticals_by_node(),
                "launchd": _launchd_state_by_node(),
                "gates": _gate_state_by_node(),
                "wall_col": _WALL_COL,
                "n_bands": len(BANDS),
            },
        )

    @app.get("/system/node/{node_id}")
    def system_node(request: Request, node_id: str) -> Response:
        node = NODES_BY_ID.get(node_id)
        if node is None:
            return templates.TemplateResponse(
                request, "_system_node.html", {"node": None, "node_id": clean(node_id)}
            )
        criticals = _open_criticals_by_node().get(node_id, 0)
        # Per-script open counts for the rail (so a multi-script node like
        # "weekly send" shows which identity is on fire).
        per_script: dict[str, int] = {}
        if criticals:
            try:
                sp: Any = importlib.import_module(
                    "operator_dashboard.sources.smartsheet_panels"
                )
                er: Any = importlib.import_module("shared.errors_rotation")
                for row in sp._cached_error_rows():
                    if er.errors_row_is_terminal(row):
                        continue
                    script = str(row.get("Script") or "").strip()
                    if script in node.error_scripts:
                        per_script[script] = per_script.get(script, 0) + 1
            except Exception:
                per_script = {}
        return templates.TemplateResponse(
            request,
            "_system_node.html",
            {
                "node": node,
                "edges": edges_for(node_id),
                "nodes_by_id": NODES_BY_ID,
                "criticals": criticals,
                "per_script": per_script,
                "launchd_state": _launchd_state_by_node().get(node_id),
                "heartbeat_age": _heartbeat_age_by_node().get(node_id),
                "gate_state": _gate_state_by_node().get(node_id),
                "ts_joins": _troubleshoot_joins().get(node_id, []),
            },
        )
