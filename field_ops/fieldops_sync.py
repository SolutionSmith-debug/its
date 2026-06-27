"""Field-Ops D1→Smartsheet mirror daemon (SKELETON, P0).

Purpose:
    The Mac-side mirror half of the Field-Ops data-residency model (plan: "D1 primary +
    Smartsheet mirror"). The Cloudflare Worker writes operational data (jobs, crew, tasks,
    time, equipment, materials, checklist instances) to D1 SEND-FREE; this launchd daemon
    mirrors that data UP to Smartsheet so Smartsheet stays the operator-visible system of
    record, and reconciles the portal-job-create → ITS_Active_Jobs inversion (provisional
    `PJOB-<uuid8>` → canonical `JOB-####` write-back). SKELETON ONLY: the mirror + inversion
    logic lands in P2 — this file establishes the module, the kill-switch + error-log
    gating, and the ITS_Config runtime gate so the scaffold is conventionally correct.

Invariants:
    - AI-FREE and customer-SEND-FREE (External Send Gate, FM Invariant 1): imports no
      `anthropic*` and no `graph_client.send_mail` / `resend` / `smtplib` / `email.mime`.
      Smartsheet writes here are SYSTEM-OF-RECORD mirroring, NOT customer transmission;
      this module is enrolled in tests/test_capability_gating.py's AI-free list in P2.
    - Kill-switch first (`@require_active`) + `@its_error_log` on the public entry (Op Stds
      conventions). The runtime gate is `field_ops.fieldops_sync.sync_enabled` in ITS_Config
      (ARCH-1: the canonical gate, NOT a Daemon_Health checkbox).
    - Bearer privilege separation (P2): authenticates to the Worker's
      /api/internal/fieldops/* endpoints with `PORTAL_FIELDOPS_API_TOKEN` — DISTINCT from
      portal_poll's `PORTAL_INTERNAL_API_TOKEN` (neither token can do the other's mutations).

Failure modes:
    - PAUSED / MAINTENANCE → `@require_active` exits cleanly (no work).
    - `sync_enabled=false` → short-circuit (no-op; ships OFF until P2 live-smoke).
    - Unhandled exceptions → `@its_error_log` → ITS_Errors + CRITICAL triple-fire.
    - (P2) per-record fences route permanent refusals to ITS_Review_Queue; transient
      failures soft-fail for the next cycle (find-or-create; never silent — CLAUDE.md).

Consumers:
    - launchd `org.solutionsmith.its.fieldops-sync` (StartInterval; plist added in P2).
    - Watchdog Check C marker + ITS_Daemon_Health row land with the real work in P2 — via
      the tracked shared/heartbeat.py extraction, NOT a third inline copy of the helpers.
"""
from __future__ import annotations

from shared import smartsheet_client
from shared.error_log import its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "field_ops.fieldops_sync"
WORKSTREAM = "field_ops"

# ITS_Config keys.
CFG_SYNC_ENABLED = "field_ops.fieldops_sync.sync_enabled"
DEFAULT_SYNC_ENABLED = False  # P0 skeleton ships OFF; P2 flips it on after live-smoke.


def _read_str_setting(key: str, fallback: str) -> str:
    """Read an ITS_Config string, falling back on a missing row or an open circuit."""
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    except smartsheet_client.SmartsheetCircuitOpenError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _read_bool_setting(key: str, fallback: bool) -> bool:
    raw = _read_str_setting(key, str(fallback).lower())
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _sync_enabled() -> bool:
    return _read_bool_setting(CFG_SYNC_ENABLED, DEFAULT_SYNC_ENABLED)


@its_error_log(SCRIPT_NAME)
@require_active
def sync_once() -> int:
    """One mirror cycle. SKELETON: gate-checks only, returns 0 (no records mirrored).

    Returns the number of records mirrored this cycle — always 0 until the P2 mirror +
    inversion logic lands. launchd invokes this once per StartInterval.
    """
    if not _sync_enabled():
        return 0
    # P2: GET /api/internal/fieldops/pending-mirror (PORTAL_FIELDOPS_API_TOKEN) → upsert
    #     jobs/crew/tasks/time/equipment/materials into their Smartsheet mirror sheets
    #     (find-or-create) → reconcile portal-origin jobs into ITS_Active_Jobs (provisional
    #     PJOB-<uuid8> → canonical JOB-#### write-back) → heartbeat (shared/heartbeat.py).
    return 0


if __name__ == "__main__":
    sync_once()
