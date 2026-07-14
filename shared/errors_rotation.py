"""Canonical ITS_Errors rotation predicates — the SINGLE SOURCE OF TRUTH for
"which ITS_Errors rows may be deleted", shared by watchdog Check O
(`scripts/watchdog.py` row-cap rotation) and the operator dashboard's
clear-error-log verb (`operator_dashboard/act/errors_ops.py`).

Extracted 2026-07-14 so the two consumers can never drift (HOUSE_REFLEXES §1,
multi-surface fan-out): a change to terminality updates ONE place. `scripts/watchdog.py`
imports these and keeps its private `_errors_row_is_terminal` / `_row_age_date`
names as thin aliases, so its call sites + rationale block are unchanged.

Terminality rule (§3.1 forensic surface): a row is deletable ("terminal") iff it is
NOT an OPEN CRITICAL. Every INFO/WARN/ERROR row is terminal (they are never "open");
a CRITICAL is terminal only once it carries a "Resolved At" stamp. An open CRITICAL
(blank "Resolved At") is NEVER deletable — it is the "am I on fire" working set that
watchdog Check B counts.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from shared.error_log import Severity


def errors_row_is_terminal(row: dict[str, Any]) -> bool:
    """ITS_Errors terminality: True unless the row is an OPEN CRITICAL.

    `row` is a `smartsheet_client.get_rows` dict (title-keyed). INFO/WARN/ERROR are
    always terminal; a CRITICAL is terminal only when its "Resolved At" cell is set.
    """
    severity = str(row.get("Severity") or "").strip()
    if severity != Severity.CRITICAL.value:
        return True
    resolved_at = row.get("Resolved At")
    return bool(str(resolved_at).strip()) if resolved_at is not None else False


def row_age_date(row: dict[str, Any], date_column: str) -> date | None:
    """Parse the row's ISO date cell; None = unprovable age (not eligible)."""
    raw = row.get(date_column)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        # Cells are written as date.today().isoformat(); tolerate a datetime
        # prefix (YYYY-MM-DDTHH:MM:SS) if the column type ever drifts.
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None
