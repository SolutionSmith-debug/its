"""Sustained consecutive-failure counter — the ERROR→CRITICAL escalation primitive.

THE GAP THIS CLOSES (2026-07-20 forensic): `estimate_poll`'s pending fetch failed every
120s cycle for ~21 hours — 629 ITS_Errors rows — and the operator never saw it, because
each cycle logged Severity **ERROR** ("transient, rows left for next cycle") and the
dashboard's fire surfaces (the Open-CRITICALs panel, the /system map badges) and the
triple-fire push path all key on **CRITICAL**. A *persistent* transient IS an outage;
without escalation it is structurally invisible.

`fieldops_sync` and `portal_poll` (Check Q) already carry a per-daemon copy of the
persisted consecutive-failure counter that closes this. This module is that pattern
extracted once (§14: 4 immediate live consumers — estimate_poll / rfq_poll / po_poll /
subcontract_poll — plus the 2 existing per-daemon copies as future convergence), so a
new intake daemon gets escalation by construction instead of by remembering.

Posture (mirrors `fieldops_sync._record_pending_fetch_failure` exactly):
- state under ``~/its/state/`` via `state_io` (atomic write + sidecar lock — the
  house write discipline);
- `record()` returns the new consecutive count; ANY state error degrades to
  ``1`` with a WARN (never page off a state glitch);
- `reset()` zeroes after a success, best-effort (a reset failure risks one spurious
  CRITICAL next cycle, never a missed outage);
- the CALLER owns the threshold compare + both log lines, so each daemon's error
  codes and remediation copy stay lane-specific (`<lane>_pending_fetch_sustained`).
"""
from __future__ import annotations

import json
from pathlib import Path

from shared import error_log, state_io
from shared.error_log import Severity

#: Consecutive failing cycles before the caller escalates ERROR → CRITICAL. Shared
#: default (5 × 120s ≈ 10 min of sustained outage); callers may override.
DEFAULT_CRITICAL_THRESHOLD = 5


class SustainedFailureCounter:
    """A persisted consecutive-failure counter for one recurring operation."""

    def __init__(self, state_path: Path, script_name: str, counter_error_code: str) -> None:
        self._path = state_path
        self._script = script_name
        self._counter_error_code = counter_error_code

    def record(self) -> int:
        """Increment + persist; return the new consecutive count (state error → 1 + WARN)."""
        try:
            with state_io.with_path_lock(self._path):
                count = 0
                if self._path.exists():
                    try:
                        count = int(json.loads(self._path.read_text()).get("count", 0))
                    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                        count = 0
                count += 1
                state_io.atomic_write_json(self._path, {"count": count})
                return count
        except Exception as exc:  # noqa: BLE001 — counter is best-effort; never page off a state glitch
            error_log.log(
                Severity.WARN, self._script,
                f"consecutive-failure counter write failed (treating as #1): {exc!r}",
                error_code=self._counter_error_code,
            )
            return 1

    def reset(self) -> None:
        """Zero after a success. Best-effort — a failure only risks one spurious CRITICAL."""
        try:
            with state_io.with_path_lock(self._path):
                if self._path.exists():
                    state_io.atomic_write_json(self._path, {"count": 0})
        except Exception:  # noqa: BLE001 — best-effort reset
            pass
