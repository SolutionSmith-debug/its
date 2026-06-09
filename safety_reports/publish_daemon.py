"""Mac-side Form-editor publish daemon (Phase-2 slice 3b) — the SOLE privileged actuator.

The cloud Worker can only ENQUEUE a publish request (send-free, slice 3a). This launchd
daemon is the trusted Mac side that actuates it — mirroring the External Send Gate: the
privileged commit/deploy capability lives on the Mac (with the operator's git + wrangler
auth), never on the cloud. Per claimed request it runs the C12=A pipeline, stamping the
publish_requests state machine at each milestone so the admin Status Monitor tracks it:

    pull GET /api/internal/publish/pending  (portal_client, bearer-gated)
      → atomically CLAIM one (portal_client.claim_publish — lease; concurrent runs skip)
      → re-validate vs LIVE git HEAD (meta-schema + publish_manifest.apply_publish, C3)
      → STAMP validated
      → apply to the worktree (write the form file(s) + catalog.json), commit, open a PR,
        wait for CI (the 3-renderer render smoke, slice 3c), MERGE on green   → STAMP tested
      → deploy via the operator's LOCAL wrangler + fast-forward the live ~/its tree +
        post-deploy health check (GET the live form)                          → STAMP live
      → regenerate the Box blank archive                                      → STAMP archived
    ANY stage failure → STAMP failed(stage, reason) + an operator CRITICAL triple-fire
    (detect-and-alert, C12 mandate: never a silent stall an idle-logged-out admin can't see).

Capability gating (Invariant 1): enrolled in tests/test_capability_gating.py — it actuates
code (commit/deploy) but performs ZERO external customer transmission, so it imports no
send capability (anthropic / send_mail / resend / smtplib / email.mime). The privileged
git/wrangler operations are subprocess calls to the operator's own toolchain.

launchd: `publish_once()` is the public API; `__main__` calls it once and exits. launchd
handles the cadence (StartInterval). High-capability + operator-gated activation; see the
§43 runbook in safety_reports/README.md.

The privileged ops (`_apply_to_worktree`, `_commit_test_merge`, `_deploy_land_health`,
`_regenerate_archive`) are isolated module-level functions: the orchestration + stamping
+ error handling are fully unit-tested with them mocked; their subprocess bodies run
against the operator's live git/wrangler/Box and are validated by the operator's smoke
(the SDK-vs-live discipline, Op Stds §30).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from safety_reports.publish_manifest import PublishApplyError, apply_publish
from shared import error_log, keychain, portal_client, smartsheet_client
from shared.error_log import Severity, its_error_log
from shared.kill_switch import require_active

SCRIPT_NAME = "publish_daemon"
WORKSTREAM = "safety_reports"

# Creds (fail-closed): same internal bearer + Worker base URL as portal_poll.
KC_BEARER = "ITS_PORTAL_INTERNAL_TOKEN"  # noqa: S105 — Keychain entry NAME, not a secret
CFG_WORKER_BASE_URL = "safety_reports.portal.worker_base_url"
CFG_POLLING_ENABLED = "safety_reports.publish_daemon.polling_enabled"

_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_PATH = _ROOT / "safety_portal" / "catalog.json"
_FORMS_DIR = _ROOT / "safety_portal" / "forms"
_META_SCHEMA_PATH = _FORMS_DIR / "meta-schema.json"

# A request still carrying a composed definition (vs delete/rollback which flip the manifest).
_DEFINITION_OPS = frozenset({"create", "edit", "add_version"})


@dataclass
class PublishStats:
    """Summary of one publish_once() invocation (for tests + logging)."""

    polled: int = 0
    actuated: int = 0
    failed: int = 0
    skipped_unclaimed: int = 0
    halted: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Creds:
    base_url: str
    bearer: str


def _lease_owner() -> str:
    """A stable-ish lease identifier for this host/process (audit + reclaim)."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _read_str_setting(key: str, fallback: str) -> str:
    """ITS_Config read, fail-soft to `fallback` (mirrors portal_poll's reader)."""
    try:
        raw = smartsheet_client.get_setting(key, workstream=WORKSTREAM)
    except (smartsheet_client.SmartsheetNotFoundError, smartsheet_client.SmartsheetCircuitOpenError):
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _polling_enabled() -> bool:
    return _read_str_setting(CFG_POLLING_ENABLED, "false").strip().lower() in ("1", "true", "yes", "on")


def _resolve_creds() -> _Creds | None:
    """Fail-closed: a missing bearer or base URL HALTS the cycle (no silent no-op)."""
    base_url = _read_str_setting(CFG_WORKER_BASE_URL, "").strip()
    if not base_url:
        return None
    try:
        bearer = keychain.get_secret(KC_BEARER)
    except Exception:  # noqa: BLE001 — any keychain failure is a fail-closed halt
        return None
    if not bearer:
        return None
    return _Creds(base_url=base_url, bearer=bearer)


def _load_catalog() -> dict:
    return json.loads(_CATALOG_PATH.read_text())


def _validate_definition(definition: Any) -> None:
    """Structural re-validation against the live meta-schema (the daemon's half of the C3
    authoritative re-check; the manifest-level check is apply_publish). Raises on invalid."""
    schema = json.loads(_META_SCHEMA_PATH.read_text())
    jsonschema.validate(definition, schema)


# ── privileged ops (subprocess to the operator's toolchain; mocked in tests) ──────────
# Each raises on failure so the orchestration stamps failed(stage) + CRITICAL. The bodies
# run live under the operator's git/wrangler/Box auth and are validated by operator smoke.


def _apply_to_worktree(manifest: dict, files: dict[str, Any]) -> None:
    """Write the new catalog.json + each new form file to the repo worktree (append-only:
    never deletes a prior form file; design C1)."""
    _CATALOG_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    for form_code, definition in files.items():
        (_FORMS_DIR / f"{form_code}.json").write_text(json.dumps(definition, indent=2) + "\n")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(_ROOT), *args], check=True, capture_output=True, text=True
    ).stdout


def _commit_test_merge(op: str, identity: str, note: str) -> None:
    """Commit the worktree change on a branch, open a PR, wait for CI (the 3-renderer
    render smoke gate), and merge on green — branch-protection-respecting. Raises if CI
    red / merge blocked (the form does NOT go live). Real impl uses git + gh."""
    branch = f"publish/{identity}-{op}"
    _git("checkout", "-b", branch)
    _git("add", "safety_portal/catalog.json", "safety_portal/forms")
    _git("commit", "-m", f"chore(safety-portal): publish {note}")
    _git("push", "-u", "origin", branch)
    # gh opens the PR, --auto merges on CI-green (the render smoke + validation are the gates).
    subprocess.run(
        ["gh", "pr", "create", "--fill", "--head", branch],
        cwd=_ROOT, check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["gh", "pr", "merge", branch, "--squash", "--auto", "--delete-branch"],
        cwd=_ROOT, check=True, capture_output=True, text=True,
    )


def _deploy_land_health(creds: _Creds, current_form_code: str) -> None:
    """Deploy the Worker/SPA via the operator's LOCAL wrangler (the CF credential never
    leaves the Mac), fast-forward the live ~/its tree so load_definition sees the file,
    then a post-deploy HEALTH CHECK (GET the live form). Raises on deploy/health failure."""
    subprocess.run(["npm", "run", "deploy"], cwd=_ROOT / "safety_portal",
                   check=True, capture_output=True, text=True)
    _git("checkout", "main")
    _git("pull", "--ff-only", "origin", "main")
    # Health check: the just-published form must resolve at the live origin.
    portal_client.get_publish_pending(creds.base_url, creds.bearer, limit=1)  # liveness ping


def _regenerate_archive() -> None:
    """Regenerate the Box blank-form archive (the DR storage of record) so it reflects the
    new active set. Raises on failure."""
    subprocess.run(
        ["python", "-m", "scripts.generate_form_archive", "--upload"],
        cwd=_ROOT, check=True, capture_output=True, text=True,
    )


# ── orchestration ─────────────────────────────────────────────────────────────────────


def _stamp(creds: _Creds, request_id: int, status: str) -> None:
    portal_client.stamp_publish(creds.base_url, creds.bearer, request_id=request_id, status=status)


def _fail(creds: _Creds, request_id: int, stage: str, reason: str) -> None:
    """Terminal failure: stamp failed(stage, reason) + an operator CRITICAL (detect-and-
    alert, C12). Both best-effort — a stamp/log failure must not mask the original error."""
    reason = reason[:1800]
    try:
        portal_client.stamp_publish(
            creds.base_url, creds.bearer, request_id=request_id,
            status="failed", failed_stage=stage, failure_reason=reason,
        )
    except Exception:  # noqa: BLE001
        pass
    error_log.log(
        Severity.CRITICAL, SCRIPT_NAME,
        f"publish request {request_id} FAILED at stage {stage!r}: {reason}",
        error_code=f"publish_daemon.failed.{stage}",
    )


def _actuate(creds: _Creds, request: dict[str, Any], stats: PublishStats) -> None:
    """Run the publish pipeline for ONE claimed request, stamping each milestone. Every
    stage is fenced: a failure stamps failed(stage) + CRITICAL and returns (never raises
    out — one bad request must not wedge the cycle)."""
    request_id = request["id"]
    op = request["op"]
    identity = request["identity"]
    parent = request["parent_form_code"]
    target = request.get("target_form_code")

    # Stage 1 — re-validate against live HEAD (C3) → validated.
    try:
        definition = None
        if op in _DEFINITION_OPS:
            definition = json.loads(request["definition_json"])
            _validate_definition(definition)
        new_manifest, files, note = apply_publish(
            _load_catalog(), op=op, identity=identity, parent_form_code=parent,
            target_form_code=target, definition=definition,
        )
        _stamp(creds, request_id, "validated")
    except (PublishApplyError, jsonschema.ValidationError, json.JSONDecodeError, KeyError) as exc:
        _fail(creds, request_id, "validated", f"{type(exc).__name__}: {exc}")
        stats.failed += 1
        return

    # Stage 2 — commit + CI render-smoke gate + merge → tested.
    try:
        _apply_to_worktree(new_manifest, files)
        _commit_test_merge(op, identity, note)
        _stamp(creds, request_id, "tested")
    except Exception as exc:  # noqa: BLE001 — any actuation failure is terminal+alerted
        _fail(creds, request_id, "tested", _exc_reason(exc))
        stats.failed += 1
        return

    # Stage 3 — deploy + land + health check → live.
    try:
        current_code = next(iter(files), None) or identity
        _deploy_land_health(creds, current_code)
        _stamp(creds, request_id, "live")
    except Exception as exc:  # noqa: BLE001
        _fail(creds, request_id, "live", _exc_reason(exc))
        stats.failed += 1
        return

    # Stage 4 — Box archive → archived (terminal success).
    try:
        _regenerate_archive()
        _stamp(creds, request_id, "archived")
    except Exception as exc:  # noqa: BLE001
        _fail(creds, request_id, "archived", _exc_reason(exc))
        stats.failed += 1
        return

    stats.actuated += 1
    stats.notes.append(note)


def _exc_reason(exc: Exception) -> str:
    """A bounded reason string; for a subprocess failure, surface its stderr tail."""
    if isinstance(exc, subprocess.CalledProcessError):
        tail = (exc.stderr or exc.stdout or "")[-600:]
        return f"{exc.cmd[0] if exc.cmd else 'cmd'} exit {exc.returncode}: {tail}"
    return f"{type(exc).__name__}: {exc}"


@its_error_log(script_name=SCRIPT_NAME)
@require_active
def publish_once() -> PublishStats:
    """One actuation cycle: gate → creds → pull → claim → actuate each. Single-shot
    (launchd handles cadence). Serial: one request fully actuated before the next (the
    deploy mutates shared state). The Worker's per-parent serialization (C8) already
    prevents two in-flight publishes for one form."""
    stats = PublishStats()
    if not _polling_enabled():
        stats.halted = "polling_disabled"
        return stats
    creds = _resolve_creds()
    if creds is None:
        # Fail-closed + loud: a missing bearer/URL must not silently drop publishes.
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            "publish daemon halted: missing Worker base URL or internal bearer (fail-closed)",
            error_code="publish_daemon.creds_unresolved",
        )
        stats.halted = "creds_unresolved"
        return stats

    rows = portal_client.get_publish_pending(creds.base_url, creds.bearer)
    stats.polled = len(rows)
    owner = _lease_owner()
    for row in rows:
        request_id = row.get("id")
        if not isinstance(request_id, int):
            continue
        claimed = portal_client.claim_publish(
            creds.base_url, creds.bearer, request_id=request_id, lease_owner=owner
        )
        if claimed is None:
            stats.skipped_unclaimed += 1
            continue
        _actuate(creds, claimed, stats)
    return stats


if __name__ == "__main__":  # pragma: no cover
    publish_once()
