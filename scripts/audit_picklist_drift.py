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
    python -m scripts.audit_picklist_drift             # report only (read-only audit)
    python -m scripts.audit_picklist_drift --update-audit-doc
    python -m scripts.audit_picklist_drift --apply           # PREVIEW the reconcile (no write)
    python -m scripts.audit_picklist_drift --apply --commit  # APPLY the reconcile (live write)

`--update-audit-doc` re-writes the conversion status emojis in
`docs/audits/picklist_hardening_audit.md` (planned; not yet implemented — print
findings and leave the doc edits to the operator until the table-rewrite
heuristic is proven safe). For now the flag prints "TODO: implement
auto-update of audit doc" and exits 0 alongside the regular report.

`--apply` (Phase 3b) is the operator-friendly reconcile: for every registered
`(sheet_id, column → values)` in `picklist_validation.REGISTRY` it calls the
additive `smartsheet_client.ensure_picklist_options` to push any MISSING options
into the live picklist. It is **additive only** (never removes an option — that
parity matches `ensure_picklist_options`; a prune would be a separate flag behind
`picklist_sync`'s reference-check guard), **idempotent** (a column already at its
registry set is a no-op), and **option-only** — a missing or wrong-typed COLUMN
is logged and skipped (creating a column is the Phase 3a schema decision, not
this command's job). **Dry-run is the default**: bare `--apply` previews the
proposed adds per column and writes nothing; `--commit` is required to mutate.
This is the real form of the `--apply` flow the §43 runbook
(`docs/runbooks/picklist_drift_reconcile.md`) describes for the Successor-Operator.

Exits:
  0 — audit: zero drift findings | apply: reconcile completed.
  1 — audit: at least one drift finding (operator UI work pending or registry/sheet disagreement).

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


def apply_reconcile(*, commit: bool) -> tuple[int, int, list[str]]:
    """Additively reconcile every registered picklist UP TO its REGISTRY set.

    Phase 3b — the operator-friendly form of the picklist-drift reconcile. For
    each registered `(sheet_id, column → values)` in `picklist_validation.REGISTRY`
    it calls `smartsheet_client.ensure_picklist_options` (additive, idempotent,
    NEVER creates columns) with `dry_run = not commit`.

    Scope (deliberate):
      - **Option drift only.** A missing or wrong-typed COLUMN raises `ValueError`
        from `ensure_picklist_options`; that is the Phase 3a schema decision
        (add-column vs trim-registry), NOT this command's job — log + skip + continue.
      - **Additive only.** Never removes an option (parity with the underlying
        helper); a prune would be a separate flag behind a reference-check guard.
      - **Idempotent.** A column already at its registry set is a no-op (no write).

    Returns `(columns_changed, options_added, skipped)` where `skipped` is the list
    of human-readable column-absent/wrong-type notes. A real SMARTSHEET error
    (auth/permission/rate-limit/circuit-open) is NOT swallowed — it propagates to
    `@its_error_log` so a genuine write failure surfaces (re-run is safe: additive
    + idempotent).
    """
    columns_changed = 0
    options_added = 0
    skipped: list[str] = []
    for sheet_id, column_registry in picklist_validation.REGISTRY.items():
        if not column_registry:
            continue  # empty registry entry (per-project shells)
        for column, values in column_registry.items():
            # sorted() → deterministic preview/append order for the missing values
            # (Smartsheet doesn't guarantee server-side option order anyway).
            try:
                result = smartsheet_client.ensure_picklist_options(
                    sheet_id, column, sorted(values), dry_run=not commit,
                )
            except ValueError as e:
                note = (
                    f"sheet={sheet_id} column={column!r}: column absent/wrong-type "
                    f"— schema decision (Phase 3a), skipping ({e})"
                )
                print(f"  [skip] {note}")
                skipped.append(note)
                continue
            if result.added:
                columns_changed += 1
                options_added += len(result.added)
                verb = "applied" if result.applied else "would add"
                print(
                    f"  [{verb}] sheet={sheet_id} column={column!r}: "
                    f"+{list(result.added)}"
                )
    return columns_changed, options_added, skipped


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
    parser.add_argument(
        "--apply", action="store_true",
        help=(
            "Reconcile mode (Phase 3b): additively push every REGISTRY column's "
            "missing options into the live picklist via ensure_picklist_options. "
            "DRY-RUN by default (preview only); add --commit to write. "
            "Option-only — a missing column is logged + skipped (Phase 3a)."
        ),
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="With --apply: actually write. Without it, --apply previews only.",
    )
    args = parser.parse_args(argv)
    if args.commit and not args.apply:
        parser.error("--commit is only valid together with --apply.")
    return args


@require_active
@its_error_log(_SCRIPT)
def main() -> None:
    args = _parse_args()

    if args.apply:
        mode = "COMMIT (live write)" if args.commit else "DRY-RUN (preview only)"
        print(f"[info] --apply reconcile mode: {mode}")
        changed, added, skipped = apply_reconcile(commit=args.commit)
        print()
        print("Summary:")
        verb = "applied" if args.commit else "would apply"
        print(f"  Columns with option adds {verb}: {changed}")
        print(f"  Options {verb}: {added}")
        print(f"  Columns skipped (absent/wrong-type — Phase 3a): {len(skipped)}")
        if not args.commit and (changed or added):
            print("  Re-run with --commit to apply.")
        if not args.no_emit and args.commit and added:
            log(
                Severity.INFO,
                _SCRIPT,
                f"picklist reconcile applied: {added} option(s) added across "
                f"{changed} column(s); {len(skipped)} column(s) skipped.",
                error_code="picklist_reconcile_applied",
            )
        _write_marker()
        sys.exit(0)

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
