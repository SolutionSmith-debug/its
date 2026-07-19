"""Vendor-estimate pull daemon — the Mac half of the estimate importer (ADR-0004 E2).

Purpose
-------
The Worker (safety_portal/worker/po_estimates.ts) accepts office-uploaded vendor
estimates SEND-FREE into the D1 pool (`po_estimates` + chunks, migration 0054),
signing each with the est:v1 HMAC at upload; this launchd daemon (120s,
`org.solutionsmith.its.estimate-poll`) is the Mac-side consumer — a SINGLE pass
behind ONE gate (`po_materials.estimate_poll.polling_enabled`, shipped false):

  GET /api/po/estimates/internal/pending → per row: claim FIRST (crash recovery)
  → pull chunks → STRICT reassembly (one chunk_total, gap-free, strict b64 — any
  malformation is an INTEGRITY failure: one-shot flag + security Review-Queue row +
  CRITICAL, NO result post, bytes left in D1 for forensics) → est:v1 HMAC verify
  (`shared.portal_hmac.verify_po_estimate`, constant-time) + len/sha256 recompute
  vs the SIGNED values → §34 doc screen (`po_attach_screen.screen_attachment` —
  the SAME screener as PO attachments; ClamAV layer reuses
  `po_materials.po_attach_screen.clamav_enabled`) → deterministic DOC-TYPE gate
  (`estimate_classify`, pdfplumber INSIDE the killable rlimited sandbox child —
  red-team #5): invoice/ap_report are REFUSED from the PO path, visibly
  (Estimate_Log 'refused' + POLICY_EDGE Review-Queue row + result post
  `wrong_doc_type:<t>`; the Worker deletes the chunks on refused) → surviving docs
  file the ORIGINAL bytes to Box (ROOT→job→"Purchase Orders"→"Vendor Quotes",
  §45 find-or-create / §47 version-on-conflict, name "<est_uuid> - <filename>")
  → Estimate_Log row (needs_review) → page-preview PNGs (Quartz via the sandbox;
  Pillow re-encoded; best-effort — failure never blocks filing) → result post
  LAST (status needs_review + box_file_id; a crash before it re-serves the claimed
  row and every prior step is idempotent).

PR-A posts ONLY refused/needs_review — no extraction (Tiers 0-2 are E4/E5/E6);
every needs_review doc lands in the disposition screen for manual Tier-3 entry.

Invariants
----------
- GENERATION-side of the External Send Gate (FM Invariant 1): AI-FREE (cloud AND
  local — no `anthropic*`, no `ollama`) and customer-SEND-FREE — no
  `graph_client`/`send_mail`/`resend`/`smtplib`/`email.mime` (enroll in
  tests/test_capability_gating.py GATED_SCRIPTS). All egress rides the
  F02-allowlisted `shared.portal_client` (our Worker) + `shared.box_client`
  (filing) + `shared.smartsheet_client` (ledger writes).
- Invariant 2: a /pending row is UNTRUSTED until its est:v1 HMAC verifies AND the
  reassembled bytes match the signed sha256/size; §34 screening precedes every
  parse; every hostile-byte parse (pdfplumber / Quartz) runs in the killable
  rlimited `estimate_sandbox` child — the daemon NEVER dies from a hostile
  document (a wedged parse degrades the doc, never the cycle).
- Bearer privilege separation (ADR-0004 red-team #1): the Keychain
  `ITS_PORTAL_ESTIMATE_TOKEN` mirrors the Worker's PORTAL_ESTIMATE_API_TOKEN and
  scopes ONLY /api/po/estimates/internal/* — this highest-exposure process holds
  no other tier's bearer.
- Kill-switch first (`@require_active`) + `@its_error_log`; observable config
  resolution (`REQUIRED_CONFIG` + `resolve_and_log`, #336).

Failure modes
-------------
- PAUSED/MAINTENANCE → `@require_active` exits cleanly. Gate false (the shipped
  default) → pure no-op (no per-cycle spam; the seeded ITS_Config row is the
  operator's switch — scripts/migrations/seed_estimates_config.py).
- Missing base URL / bearer / HMAC secret → FAIL-CLOSED: CRITICAL + ERROR
  heartbeat, nothing polled.
- 401 anywhere → the SAME bearer fails every estimate route, so the cycle STOPS:
  CRITICAL (`estimate_bearer_rejected`) + ERROR heartbeat.
- Per-row fences: PERMANENT (integrity / screen-refused / wrong doc type) →
  Review-Queue row + one-shot flag (state `estimate_poll_flagged.json` — delete an
  entry to retry after fixing the cause); TRANSIENT (SmartsheetError / BoxError /
  PortalTransportError) → ERROR-logged, row stays claimed/serviceable, next cycle
  retries. One bad row never kills the cycle (`estimate_service_failed`).

Consumers
---------
- launchd `org.solutionsmith.its.estimate-poll` (StartInterval 120s default;
  RunAtLoad).
- Watchdog Check C marker (`estimate_poll`) + ITS_Daemon_Health row
  (shared.heartbeat).
- §43 runbook: docs/runbooks/estimate_import_path.md.
"""
from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from po_materials import (
    estimate_classify,
    estimate_log,
    estimate_preview,
    po_attach_screen,
)
from safety_reports import safety_naming
from shared import (
    anomaly_logger,
    box_client,
    circuit_breaker,
    error_log,
    keychain,
    portal_client,
    portal_hmac,
    review_queue,
    smartsheet_client,
    state_io,
)
from shared.error_log import Severity, its_error_log
from shared.heartbeat import HeartbeatReporter, HeartbeatStatus
from shared.kill_switch import require_active
from shared.required_config import ConfigKey, resolve_and_log

SCRIPT_NAME = "po_materials.estimate_poll"
WORKSTREAM = "po_materials"

# ITS_Config keys (read under Workstream='po_materials' except the two SHARED
# safety_reports-owned keys — the po_poll ownership pattern).
CFG_POLLING_ENABLED = "po_materials.estimate_poll.polling_enabled"
CFG_MAX_PAGES_PREVIEW = "po_materials.estimate_poll.max_pages_preview"
# The §34 screener's optional ClamAV layer — REUSES the existing PO gate (one
# scanner posture across both doc pools; seeded false by seed_po_materials_config).
CFG_ATTACH_CLAMAV = "po_materials.po_attach_screen.clamav_enabled"
CFG_WORKER_BASE_URL = "safety_reports.portal.worker_base_url"  # shared with portal_poll
CFG_WORKER_BASE_URL_WORKSTREAM = "safety_reports"

# Keychain entry names (NOT secrets). The estimate bearer mirrors the Worker's
# PORTAL_ESTIMATE_API_TOKEN (privilege-separated per red-team #1); the HMAC secret
# is the SAME payload secret the Worker signs with (domain separation, not key
# separation, isolates the est:v1 protocol).
KC_EST_TOKEN = "ITS_PORTAL_ESTIMATE_TOKEN"  # noqa: S105 — Keychain entry NAME, not a secret
KC_HMAC_SECRET = "ITS_PORTAL_HMAC_SECRET"  # noqa: S105 — Keychain entry NAME, not a secret

DEFAULT_POLLING_ENABLED = False  # ships dark; the operator flips the seeded row
DEFAULT_MAX_PAGES_PREVIEW = 12
POLL_INTERVAL_SECONDS = 120  # registration metadata; mirrors the launchd StartInterval

# Box filing path under the job's mirror-tree folder: the PO subfolder plus the
# estimate-specific leaf (§45 find-or-create at every level).
PO_BOX_SUBFOLDER = "Purchase Orders"
VENDOR_QUOTES_SUBFOLDER = "Vendor Quotes"

# #336 — every ITS_Config key this daemon resolves at RUNTIME. The declared-but-not-
# runtime-read *.poll_interval_seconds key is deliberately EXCLUDED (install.sh bakes
# it into the plist; the daemon never reads it) — same posture as po_poll.
REQUIRED_CONFIG: list[ConfigKey] = [
    ConfigKey(CFG_POLLING_ENABLED, WORKSTREAM, DEFAULT_POLLING_ENABLED, "bool"),
    ConfigKey(
        CFG_MAX_PAGES_PREVIEW, WORKSTREAM, DEFAULT_MAX_PAGES_PREVIEW, "int",
        description=(
            "Max pages rendered as disposition-screen previews per estimate "
            "(Quartz via the estimate_sandbox child)."
        ),
    ),
    ConfigKey(
        CFG_ATTACH_CLAMAV, WORKSTREAM, False, "bool",
        description=(
            "Optional ClamAV layer of the §34 doc screener (po_attach_screen L3), "
            "SHARED with po_poll's attachment pass. Default OFF."
        ),
    ),
    ConfigKey(
        CFG_WORKER_BASE_URL, CFG_WORKER_BASE_URL_WORKSTREAM, "", "str",
        description="Shared Worker base URL; owned by safety_reports, read here too.",
    ),
    ConfigKey(
        safety_naming.CFG_BOX_PORTAL_ROOT, CFG_WORKER_BASE_URL_WORKSTREAM, "", "str",
        description=(
            "Shared Box mirror-tree root; owned by safety_reports. Clean estimates "
            "file under ROOT→<job>→'Purchase Orders'→'Vendor Quotes'."
        ),
    ),
]

# State paths. HEARTBEAT_ROW_STATE_PATH is the SHARED row-id cache (ARCH-2).
STATE_DIR = Path.home() / "its" / "state"
HEARTBEAT_PATH = STATE_DIR / "estimate_poll_heartbeat.txt"
LOCK_PATH = STATE_DIR / "estimate_poll.lock"
HEARTBEAT_ROW_STATE_PATH = STATE_DIR / "heartbeat_row_ids.json"
# One-shot flag state for PERMANENTLY-refused pool rows (`{estimate_id: reason}`).
# A flagged row is skipped every subsequent cycle (no 120s Review-Queue spam); the
# operator remediates by fixing the cause and deleting the entry (or the file).
EST_FLAGGED_PATH = STATE_DIR / "estimate_poll_flagged.json"
MAX_EST_FLAGS = 500  # drained/settled entries are dead weight only — cap the file

DAEMON_NAME = "po_materials.estimate_poll"
_REGISTRATION_SOURCE_ID = "Safety Portal Worker /api/po/estimates/internal/pending"

_heartbeat_reporter = HeartbeatReporter(
    script_name=SCRIPT_NAME,
    daemon_name=DAEMON_NAME,
    workstream=WORKSTREAM,
    liveness_path=HEARTBEAT_PATH,
    interval_seconds=POLL_INTERVAL_SECONDS,
    source_id=_REGISTRATION_SOURCE_ID,
    row_state_path=HEARTBEAT_ROW_STATE_PATH,  # shared file — make the contract explicit
)

WATCHDOG_MARKER_DIR = Path.home() / "its" / ".watchdog"
WATCHDOG_JOB_SLUG = "estimate_poll"

MIME_PDF = "application/pdf"


@dataclass(frozen=True)
class EstimatePollStats:
    """Summary of one poll_once() invocation."""

    skipped_disabled: bool = False
    skipped_locked: bool = False
    halted_no_creds: bool = False
    bearer_rejected: bool = False
    scanned: int = 0
    filed: int = 0              # docs filed to Box + Estimate_Log + needs_review posted
    refused: int = 0            # screen/doc-type refusals posted
    integrity_failures: int = 0  # bad HMAC / digest / chunk-set (no result post)
    skipped_flagged: int = 0    # rows already one-shot-flagged in a prior cycle
    previews_posted: int = 0
    errors: int = 0             # transient failures (row stays serviceable)


class _BearerRejectedError(Exception):
    """Internal: a 401 anywhere — the SAME bearer fails every estimate route, stop
    the cycle."""


@dataclass(frozen=True)
class _EstCreds:
    """Resolved credentials with NAMED fields (the portal_poll CodeQL taint
    rationale: named fields keep the bearer/secret taint off base_url and
    everything logged)."""

    base_url: str
    bearer: str
    secret: str


# ---- Config readers (replicated per preservation, mirror po_poll) ----------------


def _read_str_setting(key: str, fallback: str, workstream: str | None = None) -> str:
    try:
        raw = smartsheet_client.get_setting(
            key, workstream=workstream if workstream is not None else WORKSTREAM
        )
    except smartsheet_client.SmartsheetNotFoundError:
        return fallback
    except smartsheet_client.SmartsheetCircuitOpenError:
        return fallback
    return raw if isinstance(raw, str) and raw else fallback


def _read_bool_setting(key: str, fallback: bool) -> bool:
    raw = _read_str_setting(key, str(fallback).lower())
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _read_int_setting(key: str, fallback: int) -> int:
    raw = _read_str_setting(key, str(fallback))
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return fallback


def _polling_enabled() -> bool:
    return _read_bool_setting(CFG_POLLING_ENABLED, DEFAULT_POLLING_ENABLED)


def _max_pages_preview() -> int:
    value = _read_int_setting(CFG_MAX_PAGES_PREVIEW, DEFAULT_MAX_PAGES_PREVIEW)
    return max(1, min(value, 50))


def _attach_clamav_enabled() -> bool:
    """SHARED gate `po_materials.po_attach_screen.clamav_enabled` (default OFF)."""
    return _read_bool_setting(CFG_ATTACH_CLAMAV, False)


# ---- Lock + heartbeat + marker seams (mirror po_poll) -----------------------------


@contextmanager
def _file_lock(path: Path) -> Iterator[bool]:
    """Acquire exclusive non-blocking lock; yield True on success, False if held."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001 — cleanup best-effort
            pass
        handle.close()


def _write_heartbeat() -> None:
    """Liveness file touch — thin delegator to the shared HeartbeatReporter (the
    canonical test mock seam; see shared/heartbeat.py §42)."""
    _heartbeat_reporter.write_liveness()


def _write_heartbeat_row(
    *,
    status: HeartbeatStatus,
    items_processed: int,
    error_summary: str | None = None,
    correlation_id: str | None = None,
    notes: str | None = None,
) -> None:
    """ITS_Daemon_Health per-cycle row update — thin delegator to the shared
    HeartbeatReporter (the canonical test mock seam)."""
    _heartbeat_reporter.write_row(
        status=status,
        items_processed=items_processed,
        error_summary=error_summary,
        correlation_id=correlation_id,
        notes=notes,
    )


def _write_watchdog_marker() -> None:
    """Touch the Check C freshness marker for this run (mirror po_poll)."""
    try:
        WATCHDOG_MARKER_DIR.mkdir(parents=True, exist_ok=True)
        marker = WATCHDOG_MARKER_DIR / f"{WATCHDOG_JOB_SLUG}.last_run"
        marker.write_text(datetime.now(UTC).isoformat())
    except OSError as exc:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"watchdog marker write failed: {exc!r}",
            error_code="watchdog_marker_failed",
        )


# ---- One-shot flag state (the po_poll flag pattern) -------------------------------


def _load_flags() -> dict[str, str]:
    """Load the one-shot flag set `{estimate_id: reason}`. {} on any read error
    (fail-open: the only cost is one redundant re-flag, never a missed alert)."""
    if not EST_FLAGGED_PATH.exists():
        return {}
    try:
        parsed = json.loads(EST_FLAGGED_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _persist_flags(flags: dict[str, str]) -> None:
    """Atomically persist the flag set (capped). Lock-timeout fails OPEN with a WARN —
    a lost flag set costs a duplicate Review-Queue flag next cycle, never a missed one."""
    if len(flags) > MAX_EST_FLAGS:
        flags = dict(list(flags.items())[-MAX_EST_FLAGS:])
    EST_FLAGGED_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with state_io.with_path_lock(EST_FLAGGED_PATH):
            state_io.atomic_write_json(EST_FLAGGED_PATH, flags)
    except state_io.StateLockTimeoutError:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"could not acquire lock on {EST_FLAGGED_PATH} after retries; "
            f"estimate flag set not persisted",
            error_code="estimate_flags_persist_failed",
        )


# ---- Credential resolution (fail-CLOSED) ------------------------------------------


def _resolve_credentials() -> _EstCreds | None:
    """Resolve (base_url, bearer, secret) fail-CLOSED. None if any is absent."""
    base_url = _read_str_setting(
        CFG_WORKER_BASE_URL, "", workstream=CFG_WORKER_BASE_URL_WORKSTREAM
    )
    try:
        bearer = keychain.get_secret(KC_EST_TOKEN)
    except keychain.KeychainError:
        bearer = ""
    try:
        secret = keychain.get_secret(KC_HMAC_SECRET)
    except keychain.KeychainError:
        secret = ""
    if not (base_url and bearer and secret):
        return None
    return _EstCreds(base_url=base_url, bearer=bearer, secret=secret)


# ---- Chunk reassembly (the po_poll strictness, replicated per preservation) --------


def _reassemble_chunks(chunks: list[dict[str, Any]]) -> bytes:
    """Concatenate the decoded chunk bytes into the original file.

    STRICT: every chunk must agree on chunk_total, the index set must be exactly
    {0..n-1} (gap-free), and every chunk_b64 must strictly decode. Raises ValueError
    on ANY malformation — the caller treats that as an INTEGRITY failure (the chunk
    set was written atomically with the row, so a broken set is tamper or a serving
    defect, never a benign partial)."""
    if not chunks:
        raise ValueError("empty chunk set")
    totals = {c.get("chunk_total") for c in chunks}
    if len(totals) != 1:
        raise ValueError("inconsistent chunk_total")
    (total,) = totals
    if not isinstance(total, int) or isinstance(total, bool) or total < 1:
        raise ValueError("malformed chunk_total")
    indices: list[int] = []
    for c in chunks:
        idx = c.get("chunk_index")
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise ValueError("malformed chunk_index")
        indices.append(idx)
    if sorted(indices) != list(range(total)) or len(chunks) != total:
        raise ValueError("chunk index set not gap-free")
    by_index = sorted(chunks, key=lambda c: int(c["chunk_index"]))
    parts: list[bytes] = []
    for chunk in by_index:
        b64 = chunk.get("chunk_b64")
        if not isinstance(b64, str) or not b64:
            raise ValueError("malformed chunk_b64")
        try:
            parts.append(base64.b64decode(b64, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"chunk_b64 decode failed: {exc}") from exc
    return b"".join(parts)


# ---- Public API -------------------------------------------------------------------


@its_error_log(SCRIPT_NAME)
@require_active
def poll_once() -> EstimatePollStats:
    """Run one estimate-servicing cycle. launchd invokes this once per StartInterval;
    idempotent across crashes (the result post is LAST — see the module docstring)."""
    # #336 startup observability (after @require_active, fail-open — never blocks).
    resolve_and_log(SCRIPT_NAME, REQUIRED_CONFIG)

    if not _polling_enabled():
        # Shipped default — an intentional dark state, not an anomaly: no
        # heartbeat/marker/log spam every 120s. The seeded ITS_Config row is the
        # operator's switch (scripts/migrations/seed_estimates_config.py).
        return EstimatePollStats(skipped_disabled=True)

    with _file_lock(LOCK_PATH) as acquired:
        if not acquired:
            error_log.log(
                Severity.INFO, SCRIPT_NAME,
                "another estimate cycle holds the lock; skipping this cycle",
                error_code="estimate_poll_lock_held",
            )
            return EstimatePollStats(skipped_locked=True)
        return _poll_inside_lock()


def _poll_inside_lock() -> EstimatePollStats:
    """Body of poll_once running under the file lock."""
    creds = _resolve_credentials()
    if creds is None:
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            (
                # Deliberately does NOT interpolate the ITS_Config key or the Keychain
                # entry NAMES (secret-store names in a log are a CodeQL clear-text trip).
                "fail-closed: missing estimate portal credentials — the Worker base "
                "URL (ITS_Config) and/or the estimate bearer + HMAC-secret Keychain "
                "entries are unset; NOT polling until fixed"
            ),
            error_code="estimate_creds_missing",
        )
        _write_heartbeat()
        _write_heartbeat_row(
            status="ERROR", items_processed=0,
            error_summary="fail-closed: estimate portal credentials missing",
        )
        return EstimatePollStats(halted_no_creds=True)

    counters: dict[str, int] = {
        "scanned": 0, "filed": 0, "refused": 0, "integrity_failures": 0,
        "skipped_flagged": 0, "previews_posted": 0, "errors": 0,
    }
    bearer_rejected = False
    try:
        _estimates_pass(creds, counters)
    except _BearerRejectedError:
        # A 401 anywhere: the SAME bearer fails every estimate route, so nothing
        # else can work this cycle. A bad/rotated bearer will NOT self-heal → page.
        bearer_rejected = True
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            "estimate bearer UNAUTHORIZED (401) — rejected by the Worker's "
            "requireEstimateToken tier; cycle STOPPED until the token is fixed "
            "(everything stays queued — safe re-attempt)",
            error_code="estimate_bearer_rejected",
        )

    _write_heartbeat()
    total_flagged = counters["refused"] + counters["integrity_failures"]
    if bearer_rejected:
        cycle_status: HeartbeatStatus = "ERROR"
    elif counters["errors"] > 0:
        cycle_status = "DEGRADED"
    elif total_flagged > 0:
        cycle_status = "WARN"
    else:
        cycle_status = "OK"
    if circuit_breaker.is_open():
        cycle_status = "CIRCUIT_OPEN"
    if counters["errors"] == 0 and total_flagged == 0 and not bearer_rejected:
        error_summary = None
    else:
        error_summary = (
            f"errors={counters['errors']} flagged={total_flagged}"
            + (" bearer_rejected" if bearer_rejected else "")
        )
    try:
        _write_heartbeat_row(
            status=cycle_status,
            items_processed=counters["filed"],
            error_summary=error_summary,
        )
    except Exception as exc:  # noqa: BLE001 — heartbeat must never block
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"heartbeat write outer-catch tripped: {exc!r}",
            error_code="daemon_health_write_failed",
        )
    _write_watchdog_marker()

    error_log.log(
        Severity.INFO, SCRIPT_NAME,
        (
            f"estimate cycle: scanned={counters['scanned']} filed={counters['filed']} "
            f"refused={counters['refused']} integrity={counters['integrity_failures']} "
            f"flag-skipped={counters['skipped_flagged']} "
            f"previews={counters['previews_posted']} errors={counters['errors']}"
        ),
        error_code="estimate_cycle_summary",
    )
    return EstimatePollStats(
        bearer_rejected=bearer_rejected,
        scanned=counters["scanned"],
        filed=counters["filed"],
        refused=counters["refused"],
        integrity_failures=counters["integrity_failures"],
        skipped_flagged=counters["skipped_flagged"],
        previews_posted=counters["previews_posted"],
        errors=counters["errors"],
    )


# ---- The single estimates pass ----------------------------------------------------


def _estimates_pass(creds: _EstCreds, counters: dict[str, int]) -> None:
    """Drain the estimate pool: claim → verify → screen → classify → file → post."""
    try:
        pending = portal_client.get_estimates_pending(creds.base_url, creds.bearer)
    except portal_client.PortalAuthError as exc:
        raise _BearerRejectedError from exc
    except portal_client.PortalTransportError as exc:
        counters["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"failed to GET estimates pending (rows left for next cycle): {exc!r}",
            error_code="estimate_pending_fetch_failed",
        )
        return
    if not pending:
        return
    clamav_enabled = _attach_clamav_enabled()
    max_pages = _max_pages_preview()
    flags = _load_flags()
    flags_dirty = False
    for row in pending:
        counters["scanned"] += 1
        if _service_one_estimate(row, creds, counters, flags, clamav_enabled, max_pages):
            flags_dirty = True
    if flags_dirty:
        _persist_flags(flags)


def _service_one_estimate(
    row: dict[str, Any],
    creds: _EstCreds,
    counters: dict[str, int],
    flags: dict[str, str],
    clamav_enabled: bool,
    max_pages: int,
) -> bool:
    """Claim, verify, screen, classify, and disposition ONE pool row. Returns True
    iff the one-shot flag set was mutated (the caller persists once per cycle).

    Verify-before-anything (Invariant 2): the est:v1 HMAC binds the row's fields
    AND the content digest; the sha256 recompute over the reassembled chunks
    extends the signature to the bytes. Either failing → CRITICAL + security
    Review-Queue row + one-shot flag; the bytes stay in D1 for forensics (NO
    result post)."""
    raw_id = row.get("id")
    if not isinstance(raw_id, int) or isinstance(raw_id, bool) or raw_id <= 0:
        counters["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"pending estimate row has a missing/malformed id ({raw_id!r}); skipping",
            error_code="estimate_row_no_id",
        )
        return False
    est_id = raw_id
    flag_key = str(est_id)
    if flag_key in flags:
        counters["skipped_flagged"] += 1
        return False
    est_uuid = str(row.get("est_uuid") or "")
    job_no = str(row.get("job_no") or "")
    job_name = str(row.get("job_name") or "")
    filename = str(row.get("filename") or "")
    declared_mime = str(row.get("declared_mime") or "")
    uploaded_by = str(row.get("uploaded_by") or "")
    provided_hmac = str(row.get("hmac") or "")
    raw_size = row.get("size_bytes")
    size_bytes = raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) else -1
    signed_sha256 = str(row.get("sha256") or "")
    correlation_id = uuid.uuid4().hex[:12]

    try:
        # 1 — claim FIRST (the attachment-pool claim semantics): a crash after this
        # leaves an observable 'claimed' row that re-serves next cycle.
        portal_client.claim_estimate(creds.base_url, creds.bearer, estimate_id=est_id)

        # 2 — pull + reassemble the bytes (the ONLY Mac-ward byte flow).
        chunks = portal_client.get_estimate_chunks(
            creds.base_url, creds.bearer, estimate_id=est_id
        )
        try:
            data = _reassemble_chunks(chunks)
        except ValueError as exc:
            counters["integrity_failures"] += 1
            _handle_integrity_failure(
                est_id, est_uuid, filename, uploaded_by,
                f"chunk reassembly failed: {exc}", correlation_id, flags,
            )
            return True

        # 3 — verify: the est:v1 HMAC over the served fields, then the content
        # digest + size against the SIGNED values (never screen unverified bytes).
        if not portal_hmac.verify_po_estimate(
            creds.secret, provided_hmac,
            est_uuid=est_uuid, job_no=job_no, filename=filename,
            declared_mime=declared_mime, size_bytes=size_bytes, sha256=signed_sha256,
        ):
            counters["integrity_failures"] += 1
            _handle_integrity_failure(
                est_id, est_uuid, filename, uploaded_by,
                "HMAC verification FAILED", correlation_id, flags,
            )
            return True
        if len(data) != size_bytes or hashlib.sha256(data).hexdigest() != signed_sha256:
            counters["integrity_failures"] += 1
            _handle_integrity_failure(
                est_id, est_uuid, filename, uploaded_by,
                "content digest/size disagrees with the signed values",
                correlation_id, flags,
            )
            return True

        # 4 — §34 screen (the SAME doc screener as PO attachments: L1
        # magic/consistency → L2 structural → L3 config-gated ClamAV on raw bytes).
        result = po_attach_screen.screen_attachment(
            filename, declared_mime, data, clamav_enabled=clamav_enabled
        )
        if result.disposition != "clean":
            _refuse_screened(
                est_id, est_uuid, job_no, filename, uploaded_by, signed_sha256,
                result, correlation_id, flags,
            )
            counters["refused"] += 1
            _post_refused_result(
                creds, est_id, est_uuid,
                f"screen:{result.disposition}:{result.layer}:{result.detail}"[:200],
                correlation_id,
                error_counter=counters,
            )
            return True

        # 5 — deterministic doc-type gate (pdfplumber INSIDE the sandbox child;
        # non-PDF uploads are 'other' in PR-A → needs_review, never refused).
        if declared_mime == MIME_PDF:
            pages = estimate_classify.extract_pages_text(data)
            doc_type, confidence = estimate_classify.classify_doc_type(pages)
        else:
            doc_type, confidence = ("other", 0.0)
        if doc_type in estimate_classify.REFUSED_DOC_TYPES:
            _refuse_wrong_doc_type(
                est_id, est_uuid, job_no, filename, uploaded_by, signed_sha256,
                doc_type, confidence, correlation_id, flags,
            )
            counters["refused"] += 1
            _post_refused_result(
                creds, est_id, est_uuid, f"wrong_doc_type:{doc_type}", correlation_id,
                error_counter=counters,
            )
            return True

        # 6 — Box filing: §45 find-or-create ROOT→job→"Purchase Orders"→"Vendor
        # Quotes", §47 version-on-conflict under the est_uuid-prefixed name (the
        # uuid disambiguates same-named uploads AND makes a crash-retry version
        # instead of duplicate).
        folder_id = _resolve_quotes_box_folder(job_name or job_no)
        if folder_id is None:
            counters["errors"] += 1
            error_log.log(
                Severity.ERROR, SCRIPT_NAME,
                f"Box portal root unresolved (ITS_Config "
                f"{safety_naming.CFG_BOX_PORTAL_ROOT} unset) — estimate {est_uuid} "
                f"left claimed until the root is configured",
                error_code="estimate_box_root_unresolved",
                correlation_id=correlation_id,
            )
            return False
        filed_name = f"{est_uuid} - {filename}"
        file_info = box_client.upload_bytes_or_new_version(folder_id, filed_name, data)
        box_file_id = str(file_info["id"])

        # 7 — Estimate_Log ledger row (idempotent by uuid — a crash-retry stamps
        # instead of duplicating).
        if estimate_log.find_row_by_uuid(est_uuid) is None:
            estimate_log.append_row(
                est_uuid=est_uuid,
                job_no=job_no,
                filename=filename,
                doc_type=doc_type,
                status=estimate_log.STATUS_NEEDS_REVIEW,
                sha256=signed_sha256,
                box_file_id=box_file_id,
                detail=f"doc_type={doc_type} confidence={confidence}",
            )
        else:
            estimate_log.update_status(
                est_uuid, estimate_log.STATUS_NEEDS_REVIEW, box_file_id=box_file_id
            )

        # 8 — disposition-screen previews (Quartz via the sandbox; Pillow
        # re-encoded), BEST-EFFORT: a preview failure degrades the doc to the
        # explicit no-preview path — it never blocks filing.
        if declared_mime == MIME_PDF:
            counters["previews_posted"] += _post_previews_best_effort(
                creds, est_id, est_uuid, data, max_pages, correlation_id
            )

        # 9 — the result post, LAST (claimed→needs_review; a crash before this
        # line re-serves the row and every step above is idempotent).
        portal_client.post_estimate_result(
            creds.base_url, creds.bearer,
            estimate_id=est_id, status="needs_review", box_file_id=box_file_id,
        )
        counters["filed"] += 1
        error_log.log(
            Severity.INFO, SCRIPT_NAME,
            f"filed estimate {est_uuid} ({filename!r}, job {job_no}, "
            f"doc_type={doc_type}) → Box + Estimate_Log + needs_review",
            error_code="estimate_filed",
            correlation_id=correlation_id,
        )
        return False
    except portal_client.PortalAuthError as exc:
        raise _BearerRejectedError from exc
    except (
        smartsheet_client.SmartsheetError,
        box_client.BoxError,
        portal_client.PortalTransportError,
    ) as exc:
        counters["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"transient failure servicing estimate {est_id} ({est_uuid}; stays "
            f"serviceable for next cycle): {type(exc).__name__}: {exc!r}",
            error_code="estimate_transient",
            correlation_id=correlation_id,
        )
        return False
    except Exception as exc:  # noqa: BLE001 — per-row fence; one bad row never kills the cycle
        counters["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"unexpected failure servicing estimate {est_id} ({est_uuid}): "
            f"{type(exc).__name__}: {exc!r}",
            error_code="estimate_service_failed",
            correlation_id=correlation_id,
        )
        return False


# ---- Refusal + integrity handlers -------------------------------------------------


def _handle_integrity_failure(
    est_id: int, est_uuid: str, filename: str, uploaded_by: str,
    detail: str, correlation_id: str, flags: dict[str, str],
) -> None:
    """Reject an estimate whose transport integrity failed (bad HMAC / digest
    mismatch / malformed chunk set) — the estimate twin of po_poll's
    _handle_attachment_integrity_failure.

    NEVER screened, NEVER parsed, NEVER filed, NO result post (the bytes stay in
    D1 for forensics). One-shot: anomaly-log + security Review-Queue row + CRITICAL
    only on the FIRST sighting; the flag set suppresses per-cycle re-flag spam."""
    anomaly_logger.check({"estimate_integrity": est_id, "est_uuid": est_uuid})
    review_queue.add(
        workstream=WORKSTREAM,
        summary=(
            f"estimate: INTEGRITY FAILURE (est {est_id}, uuid {est_uuid or est_id}, "
            f"file {filename!r}) — {detail}; rejected, NOT screened or filed"
        ),
        payload={
            "estimate_id": est_id,
            "est_uuid": est_uuid,
            "filename": filename,
            "uploaded_by": uploaded_by,
            "detail": detail,
            # The HMAC value is deliberately NOT recorded (signature material);
            # the raw row + chunks stay in D1.
        },
        sla_tier=review_queue.SlaTier.RFQ_DRAFT,
        reason=review_queue.ReviewReason.SECURITY_TRIGGER,
        severity=Severity.CRITICAL,
        source_file=f"est:{est_id}",
        security_flag=True,
    )
    error_log.log(
        Severity.CRITICAL, SCRIPT_NAME,
        f"estimate integrity FAIL est_id={est_id} est_uuid={est_uuid!r}: {detail} "
        f"(downgrade defense — never screened or filed)",
        error_code="estimate_integrity_failure",
        correlation_id=correlation_id,
    )
    flags[str(est_id)] = "integrity"


def _refuse_screened(
    est_id: int, est_uuid: str, job_no: str, filename: str, uploaded_by: str,
    sha256: str, result: po_attach_screen.ScreenResult,
    correlation_id: str, flags: dict[str, str],
) -> None:
    """Route a §34-refused estimate (suspicious/malicious) to the Review Queue +
    Estimate_Log, one-shot flagged. MALICIOUS fires CRITICAL NAMING THE ACCOUNT
    (the photo_screen/intake posture); suspicious structural-active-content gets
    the security flag; plainer inconsistencies stay ordinary review items."""
    detail = f"{result.layer}:{result.detail}"
    if result.disposition == "malicious":
        review_queue.add(
            workstream=WORKSTREAM,
            summary=(
                f"estimate: MALICIOUS upload {filename!r} (uuid {est_uuid}, job "
                f"{job_no}, uploaded by {uploaded_by!r}) — refused before filing "
                f"({detail})"
            ),
            payload={
                "estimate_id": est_id, "est_uuid": est_uuid, "job_no": job_no,
                "filename": filename, "uploaded_by": uploaded_by, "detail": detail,
            },
            sla_tier=review_queue.SlaTier.RFQ_DRAFT,
            reason=review_queue.ReviewReason.SECURITY_TRIGGER,
            severity=Severity.CRITICAL,
            source_file=f"est:{est_id}",
            security_flag=True,
        )
        error_log.log(
            Severity.CRITICAL, SCRIPT_NAME,
            f"MALICIOUS estimate refused (est {est_id}, uuid {est_uuid}, account "
            f"{uploaded_by!r}): {detail} — review the account before re-enabling "
            f"uploads",
            error_code="estimate_malicious",
            correlation_id=correlation_id,
        )
    else:  # suspicious
        security = po_attach_screen.is_structural_active_content(result)
        review_queue.add(
            workstream=WORKSTREAM,
            summary=(
                f"estimate: upload {filename!r} (uuid {est_uuid}, job {job_no}) "
                f"refused as SUSPICIOUS ({detail}) — not filed; operator review"
            ),
            payload={
                "estimate_id": est_id, "est_uuid": est_uuid, "job_no": job_no,
                "filename": filename, "uploaded_by": uploaded_by, "detail": detail,
            },
            sla_tier=review_queue.SlaTier.RFQ_DRAFT,
            reason=(
                review_queue.ReviewReason.SECURITY_TRIGGER
                if security else review_queue.ReviewReason.POLICY_EDGE
            ),
            severity=Severity.WARN,
            source_file=f"est:{est_id}",
            security_flag=security,
        )
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"suspicious estimate refused (est {est_id}, uuid {est_uuid}): {detail}",
            error_code="estimate_suspicious",
            correlation_id=correlation_id,
        )
    _log_refused_row_best_effort(
        est_uuid, job_no, filename, sha256, f"screen:{result.disposition}:{detail}",
        correlation_id,
    )
    # ONE-SHOT FLAG BEFORE the result post-back (the po_poll refused posture): the
    # alert + Review-Queue row above already fired; if the post fails transiently
    # the flag IS the dedupe against a 120s re-fire storm.
    flags[str(est_id)] = "refused"


def _refuse_wrong_doc_type(
    est_id: int, est_uuid: str, job_no: str, filename: str, uploaded_by: str,
    sha256: str, doc_type: str, confidence: float,
    correlation_id: str, flags: dict[str, str],
) -> None:
    """Refuse an invoice/AP-report from the PO path, VISIBLY (ADR-0004 decision 6):
    Estimate_Log 'refused' + a POLICY_EDGE Review-Queue WARN row + one-shot flag.
    Never silently dropped, never into the PO path."""
    detail = f"wrong_doc_type:{doc_type}"
    review_queue.add(
        workstream=WORKSTREAM,
        summary=(
            f"estimate: {filename!r} (uuid {est_uuid}, job {job_no}) classified as "
            f"{doc_type.upper()} — refused from the PO path (an invoice/AP report "
            f"is never parsed as new line items)"
        ),
        payload={
            "estimate_id": est_id, "est_uuid": est_uuid, "job_no": job_no,
            "filename": filename, "uploaded_by": uploaded_by,
            "doc_type": doc_type, "confidence": confidence,
        },
        sla_tier=review_queue.SlaTier.RFQ_DRAFT,
        reason=review_queue.ReviewReason.POLICY_EDGE,
        severity=Severity.WARN,
        source_file=f"est:{est_id}",
    )
    error_log.log(
        Severity.WARN, SCRIPT_NAME,
        f"estimate refused (wrong doc type) est_id={est_id} uuid={est_uuid!r} "
        f"doc_type={doc_type} confidence={confidence}",
        error_code="estimate_wrong_doc_type",
        correlation_id=correlation_id,
    )
    _log_refused_row_best_effort(
        est_uuid, job_no, filename, sha256, detail, correlation_id, doc_type=doc_type
    )
    flags[str(est_id)] = "refused"


def _log_refused_row_best_effort(
    est_uuid: str, job_no: str, filename: str, sha256: str, detail: str,
    correlation_id: str, *, doc_type: str = "other",
) -> None:
    """Record the refusal in Estimate_Log, BEST-EFFORT (the Review-Queue row is the
    operator signal of record; a ledger miss is a WARN, never a blocked refusal)."""
    try:
        if estimate_log.find_row_by_uuid(est_uuid) is None:
            estimate_log.append_row(
                est_uuid=est_uuid,
                job_no=job_no,
                filename=filename,
                doc_type=doc_type,
                status=estimate_log.STATUS_REFUSED,
                sha256=sha256,
                detail=detail,
            )
        else:
            estimate_log.update_status(est_uuid, estimate_log.STATUS_REFUSED, detail)
    except Exception as exc:  # noqa: BLE001 — supplementary ledger; the review row is the record
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"Estimate_Log refused-row write failed (uuid {est_uuid}): "
            f"{type(exc).__name__}: {exc!r}",
            error_code="estimate_log_write_failed",
            correlation_id=correlation_id,
        )


def _post_refused_result(
    creds: _EstCreds, est_id: int, est_uuid: str, detail: str, correlation_id: str,
    *, error_counter: dict[str, int],
) -> None:
    """Post the refused disposition, handled LOCALLY (not the outer transient fence)
    so a failed post keeps the already-set one-shot flag as the re-alert dedupe —
    the po_poll refused-post posture. The Worker deletes the chunks on refused."""
    try:
        portal_client.post_estimate_result(
            creds.base_url, creds.bearer,
            estimate_id=est_id, status="refused", detail=detail,
        )
    except portal_client.PortalAuthError as exc:
        raise _BearerRejectedError from exc
    except portal_client.PortalTransportError as exc:
        error_counter["errors"] += 1
        error_log.log(
            Severity.ERROR, SCRIPT_NAME,
            f"refused-disposition post failed for estimate {est_id} (uuid "
            f"{est_uuid}; row stays claimed in D1, one-shot flag prevents re-alert; "
            f"clear '{est_id}' from {EST_FLAGGED_PATH.name} to retry after the "
            f"transport recovers): {exc!r}",
            error_code="estimate_result_post_failed",
            correlation_id=correlation_id,
        )


# ---- Previews + Box folder --------------------------------------------------------


def _post_previews_best_effort(
    creds: _EstCreds, est_id: int, est_uuid: str, data: bytes,
    max_pages: int, correlation_id: str,
) -> int:
    """Render + post the disposition-screen previews. Returns the count posted.

    WHOLLY best-effort: a render failure yields zero previews (the SPA's forced
    no-preview acknowledgment path takes over); a per-page post failure is WARNed
    and the rest continue. Auth failures still stop the cycle (bearer contract)."""
    try:
        pngs = estimate_preview.render_page_pngs(data, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 — previews must never block filing
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"preview render failed for estimate {est_uuid} (doc degrades to "
            f"no-preview): {type(exc).__name__}: {exc!r}",
            error_code="estimate_preview_failed",
            correlation_id=correlation_id,
        )
        return 0
    if not pngs:
        error_log.log(
            Severity.WARN, SCRIPT_NAME,
            f"no previews rendered for estimate {est_uuid} (sandbox degrade or "
            f"unrenderable PDF) — the disposition screen takes the no-preview path",
            error_code="estimate_preview_empty",
            correlation_id=correlation_id,
        )
        return 0
    posted = 0
    for index, png in enumerate(pngs):
        try:
            portal_client.post_estimate_preview(
                creds.base_url, creds.bearer,
                estimate_id=est_id, page=index + 1,
                png_b64=base64.b64encode(png).decode("ascii"),
            )
            posted += 1
        except portal_client.PortalAuthError as exc:
            raise _BearerRejectedError from exc
        except portal_client.PortalTransportError as exc:
            error_log.log(
                Severity.WARN, SCRIPT_NAME,
                f"preview post failed for estimate {est_uuid} page {index + 1} "
                f"(page skipped): {exc!r}",
                error_code="estimate_preview_post_failed",
                correlation_id=correlation_id,
            )
    return posted


def _resolve_quotes_box_folder(job_name: str) -> str | None:
    """§45 find-or-create the estimate filing folder: mirror-tree ROOT → per-job
    folder (the SAME `safety_naming.job_folder_name` as every other portal
    artifact) → 'Purchase Orders' → 'Vendor Quotes'. None when the shared root is
    unconfigured (the caller leaves the row claimed + ERRORs — a config gap, not a
    per-row defect)."""
    root = _read_str_setting(
        safety_naming.CFG_BOX_PORTAL_ROOT, "",
        workstream=CFG_WORKER_BASE_URL_WORKSTREAM,
    ).strip()
    if not root:
        return None
    job_folder = box_client.get_or_create_folder(
        root, safety_naming.job_folder_name(job_name)
    )
    po_folder = box_client.get_or_create_folder(job_folder, PO_BOX_SUBFOLDER)
    return box_client.get_or_create_folder(po_folder, VENDOR_QUOTES_SUBFOLDER)


if __name__ == "__main__":
    poll_once()
