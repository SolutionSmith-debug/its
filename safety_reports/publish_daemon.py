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
import time
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

# CI poll cadence for the publish merge. The repo has auto-merge DISABLED, so the daemon
# waits for the render-smoke gate (C12) ITSELF and merges synchronously — bounded so one
# stuck CI run can't wedge the daemon.
CI_POLL_S = 20.0
CI_TIMEOUT_S = 900.0  # 15 min — generous vs the ~3-5 min portal CI


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


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], cwd=_ROOT, check=True, capture_output=True, text=True).stdout


def _reset_to_main() -> None:
    """Start each actuation from a CLEAN, current main — recover from any interrupted prior
    cycle (a leftover branch + uncommitted catalog/forms edits, e.g. the failed-at-merge that
    motivated this fix). Discards ONLY the daemon-managed paths (catalog + forms); the
    operator's untracked files elsewhere in ~/its are never touched."""
    _git("checkout", "--", "safety_portal/catalog.json")
    # forms/ holds only tracked shipped forms + the meta-schema, so any UNTRACKED file here is
    # a stray from an interrupted cycle — clean it so it can't ride into the next commit.
    _git("clean", "-fd", "safety_portal/forms")
    _git("checkout", "main")
    _git("pull", "--ff-only", "origin", "main")


_CI_FAIL_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def _wait_for_ci(branch: str) -> None:
    """Poll the branch's PR until CI is green (mergeStateStatus == CLEAN), then return. Raises
    on a failing required check or a timeout. This REPLACES `gh pr merge --auto` (which needs
    the repo's auto-merge setting AND merges ASYNCHRONOUSLY, which broke the deploy ordering):
    the daemon waits for the C12 render-smoke gate itself, then merges synchronously. Polls
    mergeStateStatus (NOT `gh pr checks --watch` — a check can stick IN_PROGRESS while the PR
    is already mergeable)."""
    deadline = time.monotonic() + CI_TIMEOUT_S
    while time.monotonic() < deadline:
        data = json.loads(_gh("pr", "view", branch, "--json", "mergeStateStatus,statusCheckRollup"))
        if data.get("mergeStateStatus") == "CLEAN":
            return
        rollup = data.get("statusCheckRollup") or []
        bad = [c for c in rollup if str(c.get("conclusion") or "").upper() in _CI_FAIL_CONCLUSIONS]
        if bad:
            names = ", ".join(str(c.get("name") or c.get("context") or "check") for c in bad)
            raise RuntimeError(f"CI failed for {branch}: {names}")
        if data.get("mergeStateStatus") == "BEHIND":
            _gh("pr", "update-branch", branch)
        time.sleep(CI_POLL_S)
    raise RuntimeError(f"CI did not pass for {branch} within {int(CI_TIMEOUT_S)}s")


def _commit_test_merge(request_id: int, identity: str, note: str) -> None:
    """Commit the worktree change on a per-request branch, open a PR, WAIT for CI (the
    3-renderer render-smoke gate, C12), then MERGE on green — branch-protection-respecting,
    no repo auto-merge needed. Raises if CI red / merge blocked (the form does NOT go live)."""
    branch = f"publish/req-{request_id}-{identity}"
    # Idempotent: clear a stale local/remote branch from a prior failed run of this request.
    subprocess.run(["git", "-C", str(_ROOT), "branch", "-D", branch], capture_output=True, text=True)
    subprocess.run(["git", "-C", str(_ROOT), "push", "origin", "--delete", branch],
                   capture_output=True, text=True)
    _git("checkout", "-b", branch)
    _git("add", "safety_portal/catalog.json", "safety_portal/forms")
    _git("commit", "-m", f"chore(safety-portal): publish {note} (req {request_id})")
    _git("push", "-u", "origin", branch)
    _gh("pr", "create", "--fill", "--head", branch)
    _wait_for_ci(branch)
    _gh("pr", "merge", branch, "--squash", "--delete-branch")


def _deploy_land_health(creds: _Creds, current_form_code: str) -> None:
    """The merge has landed on main → land it locally (fast-forward ~/its so load_definition
    sees the new file), deploy the Worker/SPA via the operator's LOCAL wrangler (the CF
    credential never leaves the Mac), then a post-deploy liveness check. Raises on failure."""
    _git("checkout", "main")
    _git("pull", "--ff-only", "origin", "main")
    subprocess.run(["npm", "run", "deploy"], cwd=_ROOT / "safety_portal",
                   check=True, capture_output=True, text=True)
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

    # Stage 0 — sync to a clean, current main (recover from an interrupted prior cycle) so the
    # catalog re-check + the commit start from live HEAD.
    try:
        _reset_to_main()
    except Exception as exc:  # noqa: BLE001
        _fail(creds, request_id, "validated", f"could not sync to main: {_exc_reason(exc)}")
        stats.failed += 1
        return

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
        _commit_test_merge(request_id, identity, note)
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
