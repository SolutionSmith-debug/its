"""Panels: TTL-cached Smartsheet READ views (ITS_Errors, ITS_Review_Queue).

These are the only network-backed, cached panels. They reuse the existing
shared read helpers (get_rows / review_queue.get_pending) and NEVER any
write/update/add method. Every cell is redacted + escaped on render — the
Smartsheet content is untrusted external data (Invariant 2). A failed read
degrades to 'unavailable' via the fail-soft base wrapper; failures are not
cached.
"""
from __future__ import annotations

import importlib
import re
from collections import Counter
from typing import Any

from operator_dashboard.cache import cached
from operator_dashboard.config import SMARTSHEET_TTL_SECONDS
from operator_dashboard.sources.base import (
    SEV_ERROR,
    SEV_INFO,
    SEV_OK,
    SEV_UNAVAILABLE,
    SEV_WARN,
    DataSource,
    PanelResult,
    clean,
)

MAX_ERROR_ROWS = 25
MAX_ERROR_ROWS_DETAIL = 500  # drill-down (/view/errors_recent) shows far more


def _map_link(script: str) -> str | None:
    """A Script cell's system-map deep link, or None when the script has no node
    (rendered as plain text; the map registry deliberately omits retired /
    test-only identities)."""
    from operator_dashboard.system_map import NODE_BY_ERROR_SCRIPT

    node_id = NODE_BY_ERROR_SCRIPT.get(script.strip())
    return f"/system?focus={node_id}" if node_id else None


def _cached_error_rows() -> list[dict[str, Any]]:
    """Single TTL-cached fetch of the (large) ITS_Errors sheet, SHARED by the
    ErrorsRecent + ACT-audit panels — the sheet is at/near its row cap, so fetch
    it once per TTL, not once per panel."""

    def _load() -> list[dict[str, Any]]:
        ss: Any = importlib.import_module("shared.smartsheet_client")
        sid: Any = importlib.import_module("shared.sheet_ids")
        return list(ss.get_rows(sid.SHEET_ERRORS))

    return cached("its_errors_raw", SMARTSHEET_TTL_SECONDS, _load)

_ERRORS_PRIORITY = [
    re.compile(r"time|date|when|created", re.IGNORECASE),
    re.compile(r"sever", re.IGNORECASE),
    re.compile(r"script|source", re.IGNORECASE),
    re.compile(r"code", re.IGNORECASE),
    re.compile(r"message|summary|error|detail", re.IGNORECASE),
    re.compile(r"resolved", re.IGNORECASE),
    re.compile(r"correlation", re.IGNORECASE),
]


def _pick_error_columns(keys: list[str]) -> list[str]:
    # ITS_Errors has no column-name constants in sheet_ids.py, so choose a
    # useful, bounded column set from the LIVE titles by priority pattern.
    chosen: list[str] = []
    for pat in _ERRORS_PRIORITY:
        for k in keys:
            if k != "_row_id" and k not in chosen and pat.search(k):
                chosen.append(k)
                break
    if not chosen:
        chosen = [k for k in keys if k != "_row_id"][:5]
    return chosen[:6]


class ErrorsRecentSource(DataSource):
    panel_id = "errors_recent"
    title = "ITS_Errors — recent"

    def _fetch(self, detail: bool = False) -> PanelResult:
        cap = MAX_ERROR_ROWS_DETAIL if detail else MAX_ERROR_ROWS
        rows_raw = _cached_error_rows()[-cap:]
        if not rows_raw:
            return PanelResult(
                panel_id=self.panel_id,
                title=self.title,
                summary="0 rows",
                severity=SEV_OK,
                columns=[],
                rows=[],
            )
        # Union keys across all rows (order-preserving): Smartsheet omits empty
        # cells on read, so the newest row alone can miss occasionally-populated
        # columns (e.g. Resolved At / Correlation_ID).
        keys = list(dict.fromkeys(k for r in rows_raw for k in r))
        columns = _pick_error_columns(keys)
        sev_col = next((k for k in keys if re.search(r"sever", k, re.IGNORECASE)), None)
        script_col = next((k for k in keys if re.search(r"script|source", k, re.IGNORECASE)), None)
        corr_col = next((k for k in keys if re.search(r"correlation", k, re.IGNORECASE)), None)
        rows: list[dict[str, str]] = []
        crit = 0
        for raw in reversed(rows_raw):  # newest first
            sev_val = str(raw.get(sev_col, "")).upper() if sev_col else ""
            if sev_val in ("ERROR", "CRITICAL"):
                row_sev = SEV_ERROR
            elif sev_val == "WARN":
                row_sev = SEV_WARN
            else:
                row_sev = SEV_OK
            if sev_val == "CRITICAL":
                crit += 1
            row: dict[str, str] = {c: clean(raw.get(c)) for c in columns}
            # Compact panel: a full UUID correlation id wraps badly at panel
            # width — show the first 8 chars (the drill-down keeps the whole id).
            if not detail and corr_col and len(row.get(corr_col, "")) > 12:
                row[corr_col] = row[corr_col][:8] + "…"
            if script_col:
                link = _map_link(row.get(script_col, ""))
                if link:
                    row[f"_link_{script_col}"] = link
            row["_sev"] = row_sev
            rows.append(row)
        summary = f"{len(rows)} recent" + (f" · {crit} CRITICAL" if crit else "")
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=summary,
            severity=SEV_WARN if crit else SEV_INFO,
            columns=columns,
            rows=rows,
        )


class OpenCriticalsSource(DataSource):
    """The open-CRITICAL 'fire' surface — unresolved CRITICAL ITS_Errors rows, sheet-wide.

    This is the "am I on fire" working set watchdog Check B counts, NOT the recency slice
    ErrorsRecentSource shows. It reuses the SAME cached ITS_Errors read (no extra Smartsheet
    call) and the CANONICAL terminality predicate (``shared.errors_rotation.errors_row_is_terminal``)
    — so "open CRITICAL" means exactly what the dashboard mark-resolved verb and Check O rotation
    mean: a CRITICAL with a blank "Resolved At". Resolving CRITICALs is therefore visible here
    immediately, and a clean backlog reads green.
    """

    panel_id = "open_criticals"
    title = "Open CRITICALs (fire surface)"

    def _fetch(self, detail: bool = False) -> PanelResult:
        er: Any = importlib.import_module("shared.errors_rotation")
        open_crit = [r for r in _cached_error_rows() if not er.errors_row_is_terminal(r)]
        n = len(open_crit)
        if n == 0:
            return PanelResult(
                panel_id=self.panel_id,
                title=self.title,
                summary="0 open — clear",
                severity=SEV_OK,
                columns=[],
                rows=[],
            )
        keys = list(dict.fromkeys(k for r in open_crit for k in r))
        # Resolve the grouping columns from the LIVE titles, defaulting to the real
        # ITS_Errors column names so these are always `str` (never None).
        script_k = next((k for k in keys if re.search(r"script|source", k, re.IGNORECASE)), "Script")
        code_k = next((k for k in keys if re.search(r"^error$|code", k, re.IGNORECASE)), "Error")
        ts_k = next((k for k in keys if re.search(r"time|date|created", k, re.IGNORECASE)), "Timestamp")
        groups = Counter((str(r.get(script_k)), str(r.get(code_k))) for r in open_crit)
        rows: list[dict[str, str]] = []
        for (s, c), cnt in groups.most_common():
            oldest = min(
                (str(r.get(ts_k)) for r in open_crit
                 if str(r.get(script_k)) == s and str(r.get(code_k)) == c),
                default="",
            )
            row = {
                "Script": clean(s), "Error": clean(c), "Count": str(cnt),
                "Oldest": clean(oldest), "_sev": SEV_ERROR,
            }
            link = _map_link(row["Script"])
            if link:
                row["_link_Script"] = link
            rows.append(row)
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=f"{n} open CRITICAL"
            + (f" · {len(groups)} class(es)" if len(groups) > 1 else ""),
            severity=SEV_ERROR,
            columns=["Script", "Error", "Count", "Oldest"],
            rows=rows,
        )


class ReviewQueueDepthSource(DataSource):
    panel_id = "review_queue"
    title = "ITS_Review_Queue — depth"

    def _load(self) -> dict[str, Any]:
        rq: Any = importlib.import_module("shared.review_queue")
        pending = list(rq.get_pending())
        by_ws: dict[str, int] = {}
        by_sev: dict[str, int] = {}
        past = 0
        for row in pending:
            ws = str(row.get("Workstream") or "—")
            sev = str(row.get("Severity") or "—")
            by_ws[ws] = by_ws.get(ws, 0) + 1
            by_sev[sev] = by_sev.get(sev, 0) + 1
            try:
                if rq.is_past_sla(row):
                    past += 1
            except Exception:
                pass
        return {"total": len(pending), "by_ws": by_ws, "by_sev": by_sev, "past_sla": past}

    def _fetch(self, detail: bool = False) -> PanelResult:
        data = cached("review_queue_depth", SMARTSHEET_TTL_SECONDS, self._load)
        total = int(data["total"])
        past = int(data["past_sla"])
        rows: list[dict[str, str]] = []
        for ws, n in sorted(data["by_ws"].items(), key=lambda kv: -kv[1]):
            rows.append(
                {"dimension": "workstream", "key": clean(ws), "count": clean(n), "_sev": SEV_INFO}
            )
        for sev, n in sorted(data["by_sev"].items(), key=lambda kv: -kv[1]):
            upper = str(sev).upper()
            if upper in ("ERROR", "CRITICAL"):
                s = SEV_ERROR
            elif upper == "WARN":
                s = SEV_WARN
            else:
                s = SEV_INFO
            rows.append(
                {"dimension": "severity", "key": clean(sev), "count": clean(n), "_sev": s}
            )
        panel_sev = SEV_ERROR if past else (SEV_WARN if total else SEV_OK)
        summary = f"{total} PENDING" + (f" · {past} past SLA" if past else "")
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=summary,
            severity=panel_sev,
            columns=["dimension", "key", "count"],
            rows=rows,
        )


# The review/approve/send surfaces (WSR schema twins). READ-ONLY visibility of the
# send queue: pending / HELD / SENT / FAILED counts per workstream. This panel
# NEVER approves / resends / mutates — the send lane stays human-in-loop at the
# review sheet + the two-process send daemons (D13: the send gate is never a
# dashboard action; any mutating send-lane verb is a parked Seth decision).
#
# tests/test_system_map.py::test_send_queue_panel_covers_every_review_sheet_feeding_a_send_node
# holds this list to the map: every review sheet with a `human approval` edge into
# a send node must appear here, or that lane's backlog is invisible while the
# panel still reads "all clear".
_SEND_QUEUE_SHEETS = [
    ("safety", "SHEET_WSR_HUMAN_REVIEW"),
    ("progress", "SHEET_WPR_HUMAN_REVIEW"),
    ("po", "SHEET_PO_PENDING_REVIEW"),
    ("subcontracts", "SHEET_SUBCONTRACT_PENDING_REVIEW"),
    ("rfq", "SHEET_RFQ_PENDING_REVIEW"),  # ADR-0004 R3 — the RFQ send lane (dark)
]
_SEND_STATUS_COL = "Send Status"


def _bucket_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return "(none)"
    if s.startswith("held"):  # held / held_oversized_packet / held_workstream_mismatch ...
        return "HELD"
    if s in ("pending", "sending", "sent", "failed"):
        return s.upper()
    return raw.strip().upper()


class SendQueueSource(DataSource):
    panel_id = "send_queue"
    title = "Send queue — review / approve / send"

    def _load(self) -> dict[str, Any]:
        ss: Any = importlib.import_module("shared.smartsheet_client")
        sid: Any = importlib.import_module("shared.sheet_ids")
        per: list[dict[str, Any]] = []
        for ws, attr in _SEND_QUEUE_SHEETS:
            sheet_id = getattr(sid, attr, None)
            if sheet_id is None:
                continue
            try:
                rows = ss.get_rows(sheet_id)
            except Exception:
                per.append({"ws": ws, "unavailable": True, "counts": {}})
                continue
            counts: dict[str, int] = {}
            for r in rows:
                b = _bucket_status(str(r.get(_SEND_STATUS_COL) or ""))
                counts[b] = counts.get(b, 0) + 1
            per.append({"ws": ws, "unavailable": False, "counts": counts})
        return {"per": per}

    def _fetch(self, detail: bool = False) -> PanelResult:
        data = cached("send_queue", SMARTSHEET_TTL_SECONDS, self._load)
        rows: list[dict[str, str]] = []
        held = failed = pending = 0
        any_avail = False
        for entry in data["per"]:
            ws = entry["ws"]
            if entry["unavailable"]:
                rows.append({"workstream": clean(ws), "status": "(unavailable)", "count": "—", "_sev": SEV_WARN})
                continue
            any_avail = True
            for status, n in sorted(entry["counts"].items(), key=lambda kv: -kv[1]):
                up = status.upper()
                if up == "FAILED":
                    s, failed = SEV_ERROR, failed + n
                elif up == "HELD":
                    s, held = SEV_WARN, held + n
                elif up == "PENDING":
                    s, pending = SEV_INFO, pending + n
                elif up == "SENT":
                    s = SEV_OK
                else:
                    s = SEV_INFO
                rows.append({"workstream": clean(ws), "status": clean(status), "count": clean(n), "_sev": s})
        parts = [f"{n} {lbl}" for n, lbl in ((pending, "PENDING"), (held, "HELD"), (failed, "FAILED")) if n]
        summary = " · ".join(parts) if parts else ("all clear" if any_avail else "unavailable")
        panel_sev = (
            SEV_ERROR if failed else SEV_WARN if held else SEV_INFO if any_avail else SEV_UNAVAILABLE
        )
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=summary,
            severity=panel_sev,
            columns=["workstream", "status", "count"],
            rows=rows,
        )


# The ACT audit trail — every config edit / secret rotation / daemon control /
# denial the dashboard itself performed lands in ITS_Errors under the config
# editor's own Script name. Surfacing it HERE (read-only) puts accountability
# where the actions happen. Values are already redacted by error_log at write
# (secret rotations name the KEY only, never a value); every cell is re-redacted
# + escaped on render (Invariant 2).
_ACT_SCRIPT = "operator_dashboard.config_editor"
MAX_AUDIT_ROWS = 20
MAX_AUDIT_ROWS_DETAIL = 400  # drill-down (/view/act_audit) shows far more


class AuditTrailSource(DataSource):
    panel_id = "act_audit"
    title = "ACT audit — recent config actions"

    def _fetch(self, detail: bool = False) -> PanelResult:
        act = [r for r in _cached_error_rows() if str(r.get("Script") or "") == _ACT_SCRIPT]
        cap = MAX_AUDIT_ROWS_DETAIL if detail else MAX_AUDIT_ROWS
        rows_raw = act[-cap:]
        if not rows_raw:
            return PanelResult(
                panel_id=self.panel_id, title=self.title, summary="no ACT actions yet",
                severity=SEV_OK, columns=[], rows=[],
            )
        keys = list(dict.fromkeys(k for r in rows_raw for k in r))
        columns: list[str] = [k for k in ("Error", "Message", "Severity") if k in keys] or [
            k for k in keys if k != "_row_id"
        ][:3]
        rows: list[dict[str, str]] = []
        denials = 0
        for raw in reversed(rows_raw):  # newest first
            code = str(raw.get("Error") or "")
            sev = str(raw.get("Severity") or "").upper()
            if sev == "CRITICAL":
                row_sev = SEV_ERROR
            elif code == "config_denied":
                row_sev, denials = SEV_WARN, denials + 1
            else:
                row_sev = SEV_INFO
            row: dict[str, str] = {c: clean(raw.get(c)) for c in columns}
            row["_sev"] = row_sev
            rows.append(row)
        summary = f"{len(rows)} recent" + (f" · {denials} denied" if denials else "")
        return PanelResult(
            panel_id=self.panel_id,
            title=self.title,
            summary=summary,
            severity=SEV_WARN if denials else SEV_INFO,
            columns=columns,
            rows=rows,
        )
