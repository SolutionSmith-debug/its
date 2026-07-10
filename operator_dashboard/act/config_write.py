"""Apply a validated Class-A config edit to ITS_Config (WS2 D1-2).

Order is load-bearing (mirrors po_materials/config_apply.py): validate FULLY
first, then locate the row, then write LAST — a rejection anywhere aborts before
any Smartsheet mutation. A `false->true` edit on a first-activation-gated
send-poller gate is ESCALATED (audited, NOT applied). Every applied edit (and
every escalation) writes a durable audit row via error_log at WARN (always
lands in ITS_Errors, auto-redacted, no operator page).
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from operator_dashboard.act.registry import REGISTRY, get_entry
from operator_dashboard.act.validators import ConfigValidationError

# Outcome kinds (also used as CSS status classes + test assertions).
APPLIED = "applied"
NOOP = "noop"
ESCALATED = "escalated"
REJECTED = "rejected"
NOT_EDITABLE = "not_editable"
ERROR = "error"


@dataclass
class Outcome:
    kind: str
    message: str
    setting: str
    workstream: str


def _load(name: str) -> Any:
    return importlib.import_module(name)


def read_current(setting: str, workstream: str) -> tuple[int | None, str | None]:
    """Return (row_id, current_value) for a (Setting, Workstream) pair, or
    (None, None) if no row exists. Read-only."""
    ss = _load("shared.smartsheet_client")
    sid = _load("shared.sheet_ids")
    rows = ss.get_rows(sid.SHEET_CONFIG, filters={"Setting": setting, "Workstream": workstream})
    if not rows:
        return None, None
    row = rows[0]
    val = row.get("Value")
    return row.get("_row_id"), (val if isinstance(val, str) else None)


def read_registry_state() -> list[dict[str, Any]]:
    """One ITS_Config fetch → the current state of every registry entry, for the
    editor UI. A registry key with no live row is flagged `present=False`."""
    ss = _load("shared.smartsheet_client")
    sid = _load("shared.sheet_ids")
    by_pair: dict[tuple[str, str], str] = {}
    for r in ss.get_rows(sid.SHEET_CONFIG):
        val = r.get("Value")
        by_pair[(r.get("Setting"), r.get("Workstream"))] = val if isinstance(val, str) else ""
    out: list[dict[str, Any]] = []
    for (setting, ws), entry in REGISTRY.items():
        present = (setting, ws) in by_pair
        # URL/CSS-safe id for the per-row htmx swap target (dots in the pair
        # would be read as class selectors by htmx's hx-target).
        slug = re.sub(r"[^a-z0-9]+", "-", f"{setting}-{ws}".lower()).strip("-")
        out.append(
            {
                "setting": setting,
                "workstream": ws,
                "group": entry.group,
                "label": entry.label,
                "value": by_pair.get((setting, ws), ""),
                "present": present,
                "note": entry.note,
                "gated": entry.first_activation_gated,
                "slug": slug,
            }
        )
    out.sort(key=lambda d: (d["group"], d["setting"]))
    return out


def apply_edit(setting: str, workstream: str, new_value: str, operator: str) -> Outcome:
    entry = get_entry(setting, workstream)
    if entry is None:
        return Outcome(
            NOT_EDITABLE, f"{setting} [{workstream}] is not an editable Class-A key", setting, workstream
        )
    # 1. validate + normalize — the checkpoint. A bad value never reaches ITS_Config.
    try:
        normalized = entry.validator(new_value)
    except ConfigValidationError as exc:
        return Outcome(REJECTED, str(exc), setting, workstream)
    except Exception as exc:  # a validator must never escape → 500; treat as rejected
        return Outcome(REJECTED, f"invalid value ({type(exc).__name__})", setting, workstream)
    # 2. locate the live row + read current value
    try:
        row_id, current = read_current(setting, workstream)
    except Exception as exc:  # SmartsheetError incl. circuit-open, etc.
        return Outcome(ERROR, f"could not read current value: {type(exc).__name__}: {exc}", setting, workstream)
    if row_id is None:
        return Outcome(
            NOT_EDITABLE,
            f"no ITS_Config row for {setting} [{workstream}] — seed the row before editing",
            setting,
            workstream,
        )
    # 3. no-op if the stored value already CANONICALIZES to the same thing
    #    (normalize `current` through the same validator so 'TRUE'=='true',
    #    '0.90'=='0.9' etc. are true no-ops, not cosmetic rewrites).
    if current is not None:
        try:
            current_norm: str | None = entry.validator(current)
        except Exception:
            current_norm = None
        if current_norm is not None and current_norm == normalized:
            return Outcome(NOOP, f"already {normalized!r} — no change made", setting, workstream)
    # 4. first-activation gate: a send-poller gate going ->true from ANY non-true
    #    (dark) state is a dark->live activation → escalate (audited), NOT applied.
    #    Fails SAFE (empty/blank/junk current also escalates); mirrors the daemon's
    #    truthy coercion. Pause (->false) always applies.
    if (
        entry.first_activation_gated
        and normalized == "true"
        and (current or "").strip().lower() not in ("true", "1", "yes", "on")
    ):
        _audit(operator, setting, workstream, current, normalized, escalated=True)
        return Outcome(
            ESCALATED,
            f"turning ON {setting} is a dark→live activation — routed to the escalate path (D1-3), NOT applied "
            "(its go-live preconditions must be checked first). Pausing is always available here.",
            setting,
            workstream,
        )
    # 5. write LAST (validate-first ordering means only a good value reaches here)
    try:
        ss = _load("shared.smartsheet_client")
        sid = _load("shared.sheet_ids")
        ss.update_rows(sid.SHEET_CONFIG, [{"_row_id": row_id, "Value": normalized}])
    except Exception as exc:
        return Outcome(ERROR, f"write failed: {type(exc).__name__}: {exc}", setting, workstream)
    # 6. durable audit row
    _audit(operator, setting, workstream, current, normalized, escalated=False)
    return Outcome(APPLIED, f"{setting} [{workstream}]: {current!r} → {normalized!r}", setting, workstream)


def _audit(
    operator: str, setting: str, workstream: str, old: str | None, new: str, *, escalated: bool
) -> None:
    # WARN => always writes to ITS_Errors (no INFO env gate), message auto-redacted,
    # non-CRITICAL so no Resend/Sentry page. Precise timestamp in the message
    # because the ITS_Errors Timestamp column is date-only.
    try:
        el = _load("shared.error_log")
        verb = "ESCALATED (not applied)" if escalated else "applied"
        ts = datetime.now(UTC).isoformat()
        el.log(
            el.Severity.WARN,
            "operator_dashboard.config_editor",
            f"config edit {verb}: {setting} [{workstream}] {old!r} -> {new!r} by {operator} at {ts}",
            error_code="config_audit",
            alert=False,
        )
    except Exception:
        # The audit must never break the operator's action; error_log already
        # writes a raw local copy even if its Smartsheet leg fails.
        pass


def audit_denied(operator: str, setting: str, workstream: str, reason: str) -> None:
    """Record a DENIED ACT attempt (wrong PIN, off-allowlist origin, or a
    non-editable key) as a security signal — WARN, durable, no page. No secret
    or submitted value is included; error_log redacts the message regardless."""
    try:
        el = _load("shared.error_log")
        ts = datetime.now(UTC).isoformat()
        el.log(
            el.Severity.WARN,
            "operator_dashboard.config_editor",
            f"config edit DENIED ({reason}): {setting} [{workstream}] by {operator} at {ts}",
            error_code="config_denied",
            alert=False,
        )
    except Exception:
        pass
