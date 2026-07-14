"""Panel: recent tail of the structured local log (~/its/logs/YYYY-MM-DD.log).

The daily file is TAB-delimited `ts<TAB>sev<TAB>script<TAB>message`, with raw
multi-line tracebacks appended after a message (continuation lines that don't
start with an ISO timestamp). Those are grouped back into the preceding
record. The on-disk log is intentionally un-redacted (§54, raw forensic
surface), so every rendered line is passed through clean() (redact) before it
reaches this new egress surface.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from operator_dashboard.config import LOGS_DIR
from operator_dashboard.sources.base import (
    SEV_ERROR,
    SEV_INFO,
    SEV_OK,
    SEV_WARN,
    DataSource,
    PanelResult,
    clean,
)

MAX_RECORDS = 60
TAIL_BYTES = 96 * 1024
_ISO_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_SEV_MAP = {
    "INFO": SEV_OK,
    "WARN": SEV_WARN,
    "ERROR": SEV_ERROR,
    "CRITICAL": SEV_ERROR,
}


class LogTailSource(DataSource):
    panel_id = "logs"
    title = "Recent log tail"

    def _pick_file(self) -> Path | None:
        # error_log names the file by LOCAL date; fall back to the newest
        # *.log by mtime if today's file doesn't exist yet.
        today = LOGS_DIR / f"{datetime.now():%Y-%m-%d}.log"
        if today.exists():
            return today
        candidates = sorted(
            LOGS_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        return candidates[0] if candidates else None

    def _tail_lines(self, path: Path, tail_bytes: int = TAIL_BYTES) -> list[str]:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > tail_bytes:
                fh.seek(size - tail_bytes)
                fh.readline()  # discard the partial first line
            data = fh.read()
        return data.decode("utf-8", errors="replace").splitlines()

    def _group(self, lines: list[str]) -> list[tuple[str, str, str, str]]:
        records: list[list[str]] = []
        for line in lines:
            if _ISO_LINE.match(line) or not records:
                records.append([line])
            else:
                records[-1].append(line)  # traceback continuation
        parsed: list[tuple[str, str, str, str]] = []
        for rec in records:
            parts = rec[0].split("\t", 3)
            if len(parts) == 4:
                ts, sev, script, msg = parts
            else:
                ts, sev, script, msg = "", "", "", rec[0]
            if len(rec) > 1:
                msg = msg + "\n" + "\n".join(rec[1:])
            parsed.append((ts, sev, script, msg))
        return parsed

    def _fetch(self, detail: bool = False) -> PanelResult:
        path = self._pick_file()
        if path is None:
            return PanelResult(
                panel_id=self.panel_id,
                title=self.title,
                summary="no log file yet",
                severity=SEV_INFO,
                columns=["time (UTC)", "sev", "script", "message"],
                rows=[],
            )
        # drill-down: read a much larger tail + keep more records
        tail_bytes = TAIL_BYTES * 8 if detail else TAIL_BYTES
        cap = MAX_RECORDS * 10 if detail else MAX_RECORDS
        records = self._group(self._tail_lines(path, tail_bytes))[-cap:]
        rows: list[dict[str, str]] = [
            {
                "time (UTC)": clean(ts),
                "sev": clean(sev),
                "script": clean(script),
                "message": clean(msg),
                "_sev": _SEV_MAP.get(sev, SEV_INFO),
            }
            for ts, sev, script, msg in records
        ]
        rows.reverse()  # newest first
        return PanelResult(
            panel_id=self.panel_id,
            title=f"{self.title} · {path.name}",
            summary=f"{len(rows)} lines",
            # A log feed is a stream, not a health signal — keep the card
            # neutral; individual rows carry their own severity color.
            severity=SEV_INFO,
            columns=["time (UTC)", "sev", "script", "message"],
            rows=rows,
        )
