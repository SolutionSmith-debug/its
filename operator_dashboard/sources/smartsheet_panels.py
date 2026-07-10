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
from typing import Any

from operator_dashboard.cache import cached
from operator_dashboard.config import SMARTSHEET_TTL_SECONDS
from operator_dashboard.sources.base import (
    SEV_ERROR,
    SEV_INFO,
    SEV_OK,
    SEV_WARN,
    DataSource,
    PanelResult,
    clean,
)

MAX_ERROR_ROWS = 25

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

    def _load(self) -> list[dict[str, Any]]:
        ss: Any = importlib.import_module("shared.smartsheet_client")
        sid: Any = importlib.import_module("shared.sheet_ids")
        rows = ss.get_rows(sid.SHEET_ERRORS)
        return list(rows)[-MAX_ERROR_ROWS:]

    def _fetch(self) -> PanelResult:
        rows_raw = cached("errors_recent", SMARTSHEET_TTL_SECONDS, self._load)
        if not rows_raw:
            return PanelResult(
                panel_id=self.panel_id,
                title=self.title,
                summary="0 rows",
                severity=SEV_OK,
                columns=[],
                rows=[],
            )
        keys = list(rows_raw[-1].keys())
        columns = _pick_error_columns(keys)
        sev_col = next((k for k in keys if re.search(r"sever", k, re.IGNORECASE)), None)
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

    def _fetch(self) -> PanelResult:
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
