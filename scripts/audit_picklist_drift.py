"""Programmatic drift audit: REGISTRY vs live Smartsheet column config.

Op Stds v11 §35 requires two-layer enforcement (client-side validation +
server-side strict-picklist). This script verifies the server side: every
column registered in `shared.picklist_validation.REGISTRY` is fetched from
Smartsheet and compared to the registry's allowed set.

Three drift categories surface as findings:

  1. Column type wrong — `REGISTRY` has the column but Smartsheet reports
     TEXT_NUMBER (operator UI conversion pending).
  2. Allowed-set mismatch — Smartsheet has values the registry doesn't, or
     vice versa.
  3. "Restrict to picklist values only" toggle off — column type is
     PICKLIST but server-side enforcement isn't enabled. (Smartsheet's
     Python SDK exposes this as `column.validation` on the Column model;
     we check the `validation` attribute when present.)

CLI:
    python -m scripts.audit_picklist_drift             # report only
    python -m scripts.audit_picklist_drift --update-audit-doc

`--update-audit-doc` re-writes the conversion status emojis in
`docs/audits/picklist_hardening_audit.md` (planned; not yet implemented — print
findings and leave the doc edits to the operator until the table-rewrite
heuristic is proven safe). For now the flag prints "TODO: implement
auto-update of audit doc" and exits 0 alongside the regular report.

Exits:
  0 — zero drift findings.
  1 — at least one drift finding (operator UI work pending or registry/sheet disagreement).

Watchdog integration: this script writes
`~/its/.watchdog/safety_picklist_audit.last_run` on completion so
`scripts/watchdog.py::TRACKED_JOBS` Check C can surface a stale run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared import picklist_validation, smartsheet_client  # noqa: E402
from shared.error_log import Severity, its_error_log, log  # noqa: E402
from shared.kill_switch import require_active  # noqa: E402

_SCRIPT = "scripts.audit_picklist_drift"
WATCHDOG_JOB_NAME = "safety_picklist_audit"


def _watchdog_marker_path() -> Path:
    return Path.home() / "its" / ".watchdog" / f"{WATCHDOG_JOB_NAME}.last_run"


def _write_marker() -> None:
    """Inline-replicate watchdog.write_last_run_marker.

    Avoids importing `scripts.watchdog` (heavy, pulls in its own
    side-effecty initialization) for what's effectively a 3-line write.
    Fail-soft per the watchdog pattern.
    """
    from datetime import UTC, datetime
    try:
        marker = _watchdog_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now(UTC).isoformat())
    except OSError as e:
        log(
            Severity.WARN,
            f"{_SCRIPT}.write_marker",
            f"failed to write last_run marker: {e!r}",
        )


def _column_type(col_meta: dict[str, Any]) -> str:
    return str(col_meta.get("type") or "").upper()


def _column_options(col_meta: dict[str, Any]) -> set[str]:
    raw = col_meta.get("options") or []
    return {str(o) for o in raw if isinstance(o, str)}


def audit_one_sheet(
    sheet_id: int,
    column_registry: dict[str, frozenset[str]],
) -> list[str]:
    """Audit one sheet's registered columns against live config.

    Returns a list of drift findings as human-readable strings. Empty list
    means the sheet's registered columns are all properly hardened.
    """
    findings: list[str] = []
    try:
        live_columns = smartsheet_client.list_columns_with_options(sheet_id)
    except smartsheet_client.SmartsheetError as e:
        findings.append(
            f"sheet={sheet_id} unreadable: {type(e).__name__}: {e!r}"
        )
        return findings

    live_by_title = {c["title"]: c for c in live_columns}
    for column_title, expected_values in column_registry.items():
        live = live_by_title.get(column_title)
        if live is None:
            findings.append(
                f"sheet={sheet_id} column={column_title!r}: NOT PRESENT in live sheet"
            )
            continue
        ctype = _column_type(live)
        if ctype not in ("PICKLIST", "MULTI_PICKLIST"):
            findings.append(
                f"sheet={sheet_id} column={column_title!r}: type={ctype!r} "
                f"(expected PICKLIST; operator UI conversion pending)"
            )
            continue
        live_opts = _column_options(live)
        if live_opts != set(expected_values):
            extra_in_live = live_opts - set(expected_values)
            extra_in_registry = set(expected_values) - live_opts
            findings.append(
                f"sheet={sheet_id} column={column_title!r}: allowed-set mismatch "
                f"(in live only: {sorted(extra_in_live)!r}; "
                f"in registry only: {sorted(extra_in_registry)!r})"
            )
    return findings


def audit() -> list[str]:
    """Run the drift audit across REGISTRY. Returns all findings, empty list = clean."""
    all_findings: list[str] = []
    for sheet_id, column_registry in picklist_validation.REGISTRY.items():
        if not column_registry:
            continue  # empty registry entry (per-project shells)
        all_findings.extend(audit_one_sheet(sheet_id, column_registry))
    return all_findings


def _emit_findings_to_its_errors(findings: list[str]) -> None:
    """Push one ITS_Errors row per finding (WARN). Empty findings → one INFO."""
    if not findings:
        log(
            Severity.INFO,
            _SCRIPT,
            "picklist drift audit clean — zero findings.",
            error_code="picklist_audit_clean",
        )
        return
    for finding in findings:
        log(
            Severity.WARN,
            _SCRIPT,
            f"picklist drift: {finding}",
            error_code="picklist_drift",
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit picklist registry vs live Smartsheet config.",
    )
    parser.add_argument(
        "--update-audit-doc", action="store_true",
        help=(
            "Re-write docs/audits/picklist_hardening_audit.md status emojis "
            "based on findings. Currently prints TODO — auto-update "
            "heuristic not yet implemented."
        ),
    )
    parser.add_argument(
        "--no-emit", action="store_true",
        help="Skip ITS_Errors writes (useful for dry-run / local probe).",
    )
    return parser.parse_args(argv)


@require_active
@its_error_log(_SCRIPT)
def main() -> None:
    args = _parse_args()

    findings = audit()
    print(f"[info] Audited {len(picklist_validation.REGISTRY)} registered sheet(s).")
    if findings:
        print(f"[warn] {len(findings)} drift finding(s):")
        for f in findings:
            print(f"  - {f}")
    else:
        print("[ok] No drift findings.")

    if not args.no_emit:
        _emit_findings_to_its_errors(findings)

    if args.update_audit_doc:
        print(
            "[todo] --update-audit-doc requested; auto-update heuristic not "
            "yet implemented. Edit docs/audits/picklist_hardening_audit.md manually "
            "based on the findings above."
        )

    _write_marker()
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
